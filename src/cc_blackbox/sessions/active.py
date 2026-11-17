"""Running Claude Code sessions, as their registry records describe them."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from cc_blackbox.compatibility import Compatibility, check_compatibility
from cc_blackbox.errors import AmbiguousSession, AuthSwitchConfigError, SessionNotFound
from cc_blackbox.paths import node_tmpdir, scratchpad_dir
from cc_blackbox.profiles import (
    ClaudeProfile,
    current_profile,
    list_profiles,
    profile_for_config_dir,
    resolve_profiles,
)
from cc_blackbox.sessions._launch import find_transcript_file, read_launch_cwd
from cc_blackbox.sessions.claude_json import LastSessionIndex
from cc_blackbox.sessions.liveness import is_session_process_alive
from cc_blackbox.sessions.profile_starts import PROFILE_STARTS_FILE, ProfileStart, read_profile_starts, start_of_process
from cc_blackbox.sessions.registry import RecordFile, read_records
from cc_blackbox.transcripts.layout import ProjectsDir, is_session_id, list_projects_dirs, project_dirs_for

if TYPE_CHECKING:
    from cc_blackbox.sessions.stored import SessionTranscript

__all__ = [
    "ActiveSession",
    "ProfileAttribution",
    "SessionKind",
    "SessionStatus",
    "current_session",
    "find_session",
    "list_active_sessions",
]

SessionStatus = Literal["busy", "shell", "idle", "waiting"]
"""The status values Claude Code 2.1 writes. :attr:`ActiveSession.status` keeps any other string as it is."""

SessionKind = Literal["interactive", "bg", "daemon", "daemon-worker"]
"""The kinds Claude Code 2.1 writes. :attr:`ActiveSession.kind` keeps any other string as it is."""

ProfileAttribution = Literal["log", "environ", "claude_json", "dir", "inferred"]
"""How the profile of a session was decided, from the most to the least reliable source.

``log`` is the claude-code-auth-switch start log next to the transcript, see :class:`~cc_blackbox.ProfileStart`.
``environ`` is the ``CLAUDE_CONFIG_DIR`` of the session process, which the SDK can read only on Linux, or of this
process for :func:`current_session`. ``claude_json`` means exactly one candidate profile's ``.claude.json`` names the
session as the ``lastSessionId`` of a directory, which Claude Code writes when a session ends, so it finds earlier runs
of a resumed session, not new ones. ``dir`` means only one profile uses the dir that holds the registry record or the
transcript. ``inferred`` is a guess: several profiles share that dir and nothing else said which of them runs the
session.
"""


# Without slots: functools.cached_property keeps its value in the instance __dict__, which slots remove.
@dataclass(frozen=True)
class ActiveSession:
    """A Claude Code session whose registry record exists, and whose process runs if liveness was checked.

    Every field except ``profile``, ``pid``, ``record_path`` and ``profile_source`` is ``None`` when the record lacks it
    or holds a value of an unexpected type. ``raw`` keeps the whole record for fields the SDK does not model.

    The object is a snapshot of the moment it was listed. :attr:`transcript` is read on first access and kept.
    """

    profile: ClaudeProfile
    """The profile the session runs under. When several profiles share a sessions dir, see
    :attr:`profile_source` for how sure that is."""
    pid: int
    session_id: str | None
    name: str | None
    """The session name. A derived name ends in a random suffix and is not unique: Claude Code checks only names set by
    the user or by a peer for collisions."""
    name_source: str | None
    """How the name was set: ``user``, ``peer``, ``derived``, ``collision``, ``auto`` or ``hook``."""
    cwd: Path | None
    """The session's current directory, as its registry record holds it. Claude Code rewrites it when the session moves
    into a worktree or changes directory with ``/cd``, and moves the transcript to the project dir of the new one."""
    launch_cwd: Path | None
    """The directory the session's process was started in, which Claude Code does not record in the registry.

    It is the ``cwd`` of the first transcript record written by this process: a resumed session's transcript starts
    with the records of its earlier runs, which can be from other directories. ``None`` when the session has no
    transcript yet, or has not written a record since it started. Reading it costs a read of the start of the
    transcript, or for a resumed session a few reads spread over it."""
    kind: str | None
    entrypoint: str | None
    status: str | None
    version: str | None
    started_at: datetime | None
    updated_at: datetime | None
    """When the record last changed. Claude Code writes the record on events only, so an old value does not mean the
    session hangs."""
    messaging_socket: Path | None
    """The session's messaging socket, or ``None`` when the session has messaging turned off."""
    peer_protocol: int | None
    peer_features: frozenset[str]
    tmux: str | None
    """The tmux pane as ``<session>:@<window>.%<pane>``, when the session runs in tmux."""
    bridge_session_id: str | None
    scratchpad_dir: Path | None
    """The session's scratchpad dir, under the project slug of :attr:`launch_cwd`. Claude Code keeps the scratchpad of
    the launch directory when the session changes directory. It creates the dir on first use, so it may not exist
    yet."""
    scratchpad_dir_inferred: bool
    """``True`` when :attr:`scratchpad_dir` is a guess: its temp dir comes from this process's environment, or its
    project slug comes from :attr:`cwd` because :attr:`launch_cwd` is unknown.

    The temp dir comes from ``CLAUDE_CODE_TMPDIR`` in the session's settings files, then from the environment the
    session process started with, which the SDK can read only on Linux. On macOS and Windows the value is often a
    guess. :attr:`cwd` is right unless the session changed directory before writing its first transcript record."""
    record_path: Path
    raw: Mapping[str, Any]
    profile_source: ProfileAttribution = "dir"
    transcript_path: Path | None = None
    """The session's main transcript, or ``None`` when the session has no session id or has not written one yet."""
    _transcript_projects_dir: ProjectsDir | None = field(default=None, repr=False, compare=False)

    @cached_property
    def transcript(self) -> SessionTranscript | None:
        """The session's main transcript, read on first access, or ``None`` when :attr:`transcript_path` is ``None``
        or the file is gone.

        Its ``cached_property`` attributes, such as
        :attr:`~cc_blackbox.SessionTranscript.last_model_activity_at`, are read on their own first access.
        """
        from cc_blackbox.sessions.stored import _summarize

        if self.transcript_path is None:
            return None
        projects_dir = self._transcript_projects_dir or ProjectsDir(
            Path(os.path.realpath(self.transcript_path.parent.parent)), (self.profile,)
        )
        try:
            return _summarize(self.transcript_path, projects_dir)
        except OSError:
            return None

    def profile_starts(self) -> list[ProfileStart]:
        """Return the starts of :attr:`session_id` that claude-code-auth-switch logged, oldest first, or an empty list.

        Unlike :meth:`SessionTranscript.profile_starts() <cc_blackbox.SessionTranscript.profile_starts>`, it also finds
        the log of a session that has not written its transcript yet: the start hook writes the log before the first
        prompt, the transcript only after it. Such a log is looked up in the project dirs of :attr:`cwd` and
        :attr:`launch_cwd`, which costs a few stats. Reads the log on every call.
        """
        log = self._profile_starts_log()
        return read_profile_starts(log) if log is not None else []

    def process_start(self) -> ProfileStart | None:
        """Return the start that this session's process logged, from :meth:`profile_starts`, or ``None``.

        It is the newest start of :attr:`pid` that is not older than the process: a resumed session's log holds the
        starts of earlier processes, and a pid can be reused. A ``/clear`` or an in-process ``/resume`` gives the
        session another :attr:`session_id`, and the start hook logs that start into the new session's log, so the
        result is the start of the current session id.
        """
        started_at_ms = self.raw.get("startedAt")
        if isinstance(started_at_ms, bool) or not isinstance(started_at_ms, int | float) or started_at_ms <= 0:
            started_at_ms = None
        return start_of_process(self.profile_starts(), self.pid, started_at_ms)

    def _profile_starts_log(self) -> Path | None:
        if self.session_id is None or not is_session_id(self.session_id):
            return None
        # The log sits next to the transcript. A transcript of another session id is stale: the session was cleared.
        if self.transcript_path is not None and self.transcript_path.stem == self.session_id:
            return self.transcript_path.with_suffix("") / PROFILE_STARTS_FILE
        for cwd in dict.fromkeys(d for d in (self.cwd, self.launch_cwd) if d is not None):
            for project in project_dirs_for(cwd, [self.profile]):
                log = project / self.session_id / PROFILE_STARTS_FILE
                if log.is_file():
                    return log
        return None

    @property
    def profile_inferred(self) -> bool:
        """``True`` when :attr:`profile` is a guess among the profiles that share the session's sessions dir."""
        return self.profile_source == "inferred"

    @property
    def address(self) -> str:
        """``<profile>/<name>``, or ``<profile>/pid:<pid>`` when the session has no name.

        A pid address is unique on the machine. A name address is unique only if no two sessions share the name.
        """
        return f"{self.profile.name}/{self.name}" if self.name else f"{self.profile.name}/pid:{self.pid}"

    @property
    def compatibility(self) -> Compatibility:
        """Whether the session's record provides what each feature of this package needs; see
        :func:`check_compatibility`."""
        return check_compatibility(self)

    @property
    def can_receive_messages(self) -> bool:
        """Whether messaging is aligned (see :attr:`compatibility`) and the socket file exists."""
        return (
            self.messaging_socket is not None
            and self.compatibility.features["messaging"]
            and os.path.exists(self.messaging_socket)
        )


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _timestamp(data: Mapping[str, Any], key: str) -> datetime | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _read_process_environ(pid: int) -> dict[str, str] | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep:
            env[os.fsdecode(key)] = os.fsdecode(value)
    return env


def _settings_env(path: Path, key: str) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    env = data.get("env") if isinstance(data, dict) else None
    value = env.get(key) if isinstance(env, dict) else None
    return value if isinstance(value, str) and value else None


def _settings_tmpdir(profile: ClaudeProfile, cwd: Path) -> str | None:
    # Claude Code copies the "env" of its settings files into its own environment after it starts, so a value set
    # there is missing from /proc/<pid>/environ. Local project settings win over project settings, which win over
    # the user settings in the config dir. Managed policy settings are not read.
    for path in (
        cwd / ".claude" / "settings.local.json",
        cwd / ".claude" / "settings.json",
        profile.config_dir / "settings.json",
    ):
        value = _settings_env(path, "CLAUDE_CODE_TMPDIR")
        if value is not None:
            return value
    return None


def _started_at_ms(record: RecordFile) -> float | None:
    value = record.data.get("startedAt")
    return value if not isinstance(value, bool) and isinstance(value, int | float) and value > 0 else None


def _locate(
    session_id: str | None, cwd: Path | None, searched: Sequence[ProjectsDir]
) -> tuple[tuple[Path, ProjectsDir] | None, Path | None]:
    """Return the session's transcript with its projects dir, and its auth-switch start log, each ``None`` when
    missing."""
    if session_id is None or not is_session_id(session_id):
        return None, None
    # The transcript follows /cd, so the record's cwd names its project dir.
    found = find_transcript_file(session_id, searched, cwd)
    if found is not None:
        return found, found[0].with_suffix("") / PROFILE_STARTS_FILE
    # A session that has not written its transcript yet can already have the log, which its SessionStart hook writes.
    if cwd is not None and searched:
        for project in project_dirs_for(cwd, projects_dirs=[d.path for d in searched]):
            log = project / session_id / PROFILE_STARTS_FILE
            if log.is_file():
                return None, log
    return None, None


def _owner(
    members: Sequence[ClaudeProfile],
    record: RecordFile,
    log: Path | None,
    env: Mapping[str, str] | None,
    last_sessions: LastSessionIndex,
) -> tuple[ClaudeProfile, ProfileAttribution]:
    """Return the profile among ``members``, the profiles that share the record's sessions dir, that runs it.

    A session writes its record only into its own config dir's sessions dir, so an owner outside ``members`` is a
    stale log line or another process and is ignored.
    """
    if log is not None:
        start = start_of_process(read_profile_starts(log), record.pid, _started_at_ms(record))
        if start is not None:
            owner = profile_for_config_dir(start.config_dir, start.profile, members)
            if owner is not None:
                return owner, "log"
    if env is not None:
        owner = profile_for_config_dir(env.get("CLAUDE_CONFIG_DIR"), env.get("FP_CC_AUTH_SWITCH_PROFILE"), members)
        if owner is not None:
            return owner, "environ"
    if len(members) == 1:
        return members[0], "dir"
    # Only a shared dir needs .claude.json: in a dir of one profile it could name no other owner.
    session_id = _str(record.data, "sessionId")
    owner = last_sessions.owner(session_id, members) if session_id is not None else None
    if owner is not None:
        return owner, "claude_json"
    return members[0], "inferred"


def _scratchpad(
    profile: ClaudeProfile,
    record: RecordFile,
    launch_cwd: Path | None,
    cwd: Path | None,
    session_id: str | None,
    env: Mapping[str, str] | None,
    *,
    own_env: bool,
) -> tuple[Path | None, bool]:
    base = launch_cwd if launch_cwd is not None else cwd
    if base is None or session_id is None:
        return None, False
    if own_env:
        return scratchpad_dir(base, session_id), launch_cwd is None
    # Claude Code reads the project settings of the dir it starts in, so their env applies to the launch dir.
    tmpdir = _settings_tmpdir(profile, base)
    if tmpdir is None and env is not None:
        tmpdir = env.get("CLAUDE_CODE_TMPDIR") or node_tmpdir(env)
    return scratchpad_dir(base, session_id, tmpdir=tmpdir, uid=record.owner_uid), tmpdir is None or launch_cwd is None


def _to_session(
    members: Sequence[ClaudeProfile],
    record: RecordFile,
    searched: Sequence[ProjectsDir],
    env: Mapping[str, str] | None,
    *,
    own_profile: ClaudeProfile | None = None,
    last_sessions: LastSessionIndex | None = None,
) -> ActiveSession:
    """Build the session of ``record``. ``own_profile`` is set for this process's own session, whose environment and
    profile are known."""
    data = record.data
    cwd_value = _str(data, "cwd")
    cwd = Path(cwd_value) if cwd_value and cwd_value != "?" else None
    session_id = _str(data, "sessionId")
    socket_value = _str(data, "messagingSocketPath")
    features = data.get("peerFeatures")
    peer_features = frozenset(f for f in features if isinstance(f, str)) if isinstance(features, list) else frozenset()
    located, log = _locate(session_id, cwd, searched)
    transcript = located[0] if located is not None else None
    started_at_ms = _started_at_ms(record)
    launch_cwd = read_launch_cwd(transcript, started_at_ms) if transcript and started_at_ms is not None else None
    attribution: tuple[ClaudeProfile, ProfileAttribution] = (
        _owner(members, record, log, env, last_sessions or LastSessionIndex())
        if own_profile is None
        else (own_profile, "environ")
    )
    profile, profile_source = attribution
    scratchpad, inferred = _scratchpad(
        profile, record, launch_cwd, cwd, session_id, env, own_env=own_profile is not None
    )
    return ActiveSession(
        profile=profile,
        pid=record.pid,
        session_id=session_id,
        name=_str(data, "name"),
        name_source=_str(data, "nameSource"),
        cwd=cwd,
        launch_cwd=launch_cwd,
        kind=_str(data, "kind"),
        entrypoint=_str(data, "entrypoint"),
        status=_str(data, "status"),
        version=_str(data, "version"),
        started_at=_timestamp(data, "startedAt"),
        updated_at=_timestamp(data, "updatedAt"),
        messaging_socket=Path(socket_value) if socket_value else None,
        peer_protocol=_int(data, "peerProtocol"),
        peer_features=peer_features,
        tmux=_str(data, "tmux"),
        bridge_session_id=_str(data, "bridgeSessionId"),
        scratchpad_dir=scratchpad,
        scratchpad_dir_inferred=inferred,
        record_path=record.path,
        raw=MappingProxyType(data),
        profile_source=profile_source,
        transcript_path=transcript,
        _transcript_projects_dir=located[1] if located is not None else None,
    )


def _is_alive(record: RecordFile) -> bool:
    data = record.data
    return is_session_process_alive(record.pid, _str(data, "procStart"), _str(data, "pidDomain"))


def _sessions_dir_groups(selected: Sequence[ClaudeProfile], everyone: bool) -> list[list[ClaudeProfile]]:
    """Group the profiles by the realpath of their sessions dir, keeping only groups with a selected member.

    The groups span every known profile, not only the selected ones: a record in a shared dir may belong to a profile
    that was not selected, and must not be reported as a selected one's. Selected profiles come first in each group.
    """
    known = list(selected)
    if not everyone:
        with contextlib.suppress(AuthSwitchConfigError):
            known.extend(list_profiles())
    groups: dict[Path, list[ClaudeProfile]] = {}
    seen: set[Path] = set()
    for profile in known:
        if profile.config_dir not in seen:
            seen.add(profile.config_dir)
            groups.setdefault(Path(os.path.realpath(profile.sessions_dir)), []).append(profile)
    wanted = {p.config_dir for p in selected}
    return [members for members in groups.values() if members[0].config_dir in wanted]


def list_active_sessions(
    profiles: Iterable[str | ClaudeProfile] | None = None,
    *,
    check_liveness: bool = True,
) -> list[ActiveSession]:
    """List the sessions recorded in the registry of each profile.

    ``profiles`` defaults to :func:`list_profiles`; a string entry is looked up by profile name. With
    ``check_liveness``, records whose process has ended, or whose pid was reused by another process, are left out. The
    SDK never deletes such records; sweeping them is Claude Code's job. The result is ordered by profile, in the order
    given, then by start time.

    Profiles can share one sessions dir. Each dir is read once, so each session is listed once, under the profile that
    runs it; :attr:`~ActiveSession.profile_source` says how that profile was found. A session of a profile that was
    not selected is left out even when its record sits in a selected profile's dir.

    Each session's transcript is opened to find its :attr:`~ActiveSession.launch_cwd`, which takes a read of its start,
    or a few reads spread over it for a resumed session.

    Raises:
        ProfileNotFound: a string entry names no known profile.
    """
    selected = resolve_profiles(profiles)
    order: dict[Path, int] = {}
    for index, profile in enumerate(selected):
        order.setdefault(profile.config_dir, index)
    oldest = datetime.min.replace(tzinfo=UTC)
    result: list[tuple[int, ActiveSession]] = []
    last_sessions = LastSessionIndex()
    for members in _sessions_dir_groups(selected, profiles is None):
        searched = list_projects_dirs(members)
        for record in read_records(members[0].sessions_dir):
            alive = _is_alive(record)
            if check_liveness and not alive:
                continue
            # The environment of a reused pid belongs to another process.
            env = _read_process_environ(record.pid) if alive else None
            session = _to_session(members, record, searched, env, last_sessions=last_sessions)
            position = order.get(session.profile.config_dir)
            if position is not None:
                result.append((position, session))
    result.sort(key=lambda item: (item[0], item[1].started_at or oldest, item[1].pid))
    return [session for _, session in result]


# JavaScript's \s and String.prototype.trim() set. Unlike Python's, it includes U+FEFF.
_JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_WHITESPACE_RUN = re.compile(f"[{re.escape(_JS_WHITESPACE)}]+")


def _normalize_name(name: str) -> str:
    # Claude Code's own name comparison: NFKC, drop control and format characters except whitespace, trim,
    # lowercase, and turn each whitespace run into one "-".
    text = unicodedata.normalize("NFKC", name)
    text = "".join(ch for ch in text if ch in _JS_WHITESPACE or unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))
    return _WHITESPACE_RUN.sub("-", text.strip(_JS_WHITESPACE).lower())


def _match(query: str, sessions: Sequence[ActiveSession]) -> list[ActiveSession]:
    if query.startswith("pid:"):
        pid_text = query[4:]
        return [s for s in sessions if pid_text.isdecimal() and s.pid == int(pid_text)]
    by_id = [s for s in sessions if s.session_id == query]
    if by_id:
        return by_id
    normalized = _normalize_name(query)
    by_name = [s for s in sessions if s.name is not None and _normalize_name(s.name) == normalized]
    if by_name:
        return by_name
    return [s for s in sessions if s.session_id is not None and s.session_id.startswith(query)]


def find_session(query: str, *, sessions: Sequence[ActiveSession] | None = None) -> ActiveSession:
    """Find one session by address, name, session id, ``pid:<n>``, or a unique prefix of a session id.

    ``sessions`` defaults to :func:`list_active_sessions`. An address is ``<profile>/<rest>``, where ``<rest>`` is any
    of the other forms. Names are compared the way Claude Code compares them: Unicode NFKC, control characters
    removed, lowercase, whitespace turned into ``-``. An exact session id wins over a name, and a name wins over a
    prefix.

    Raises:
        SessionNotFound: nothing matches.
        AmbiguousSession: more than one session matches. Its ``candidates`` attribute lists them.
    """
    candidates = list_active_sessions() if sessions is None else sessions
    if not query:
        raise SessionNotFound("The session query is empty. Pass a name, a session id, pid:<n> or <profile>/<name>.")
    profile_name, sep, rest = query.partition("/")
    matches = []
    if sep and rest:
        matches = _match(rest, [s for s in candidates if s.profile.name == profile_name])
    if not matches:
        matches = _match(query, candidates)
    if not matches:
        raise SessionNotFound(f"No active Claude Code session matches {query!r}.")
    if len(matches) > 1:
        addresses = ", ".join(f"{s.address} (pid {s.pid})" for s in matches)
        raise AmbiguousSession(f"{len(matches)} sessions match {query!r}: {addresses}.", matches)
    return matches[0]


def current_session() -> ActiveSession | None:
    """Return the session this process runs under, or ``None`` when it does not run under one.

    Claude Code exports ``CLAUDE_PID``, ``CLAUDE_CODE_SESSION_ID`` and ``CLAUDE_CODE_MESSAGING_SOCKET`` to every
    process it starts, its subagents' processes included, so a tool started from any of them finds the main session.
    The record is looked up in the registry of :func:`current_profile`. The scratchpad path is computed from this
    process's own environment, which it inherited from the session.
    """
    pid_text = os.environ.get("CLAUDE_PID", "")
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    socket_path = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or None
    pid = int(pid_text) if pid_text.isdecimal() else None
    if pid is None and session_id is None and socket_path is None:
        return None
    profile = current_profile()
    if profile is None:
        return None

    records = [r for r in read_records(profile.sessions_dir) if _is_alive(r)]

    def pick() -> RecordFile | None:
        if pid is not None:
            for record in records:
                if record.pid == pid:
                    return record
        for key, value in (("messagingSocketPath", socket_path), ("sessionId", session_id)):
            if value is not None:
                for record in records:
                    if record.data.get(key) == value:
                        return record
        return None

    record = pick()
    if record is None:
        return None
    return _to_session([profile], record, list_projects_dirs([profile]), None, own_profile=profile)
