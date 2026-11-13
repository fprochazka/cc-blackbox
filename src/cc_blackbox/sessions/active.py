"""Running Claude Code sessions, as their registry records describe them."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from cc_blackbox.compatibility import Compatibility, check_compatibility
from cc_blackbox.errors import AmbiguousSession, SessionNotFound
from cc_blackbox.paths import node_tmpdir, scratchpad_dir
from cc_blackbox.profiles import ClaudeProfile, current_profile, resolve_profiles
from cc_blackbox.sessions._launch import find_transcript_file, read_launch_cwd
from cc_blackbox.sessions.liveness import is_session_process_alive
from cc_blackbox.sessions.registry import RecordFile, read_records
from cc_blackbox.transcripts.layout import list_projects_dirs

__all__ = [
    "ActiveSession",
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


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """A Claude Code session whose registry record exists, and whose process runs if liveness was checked.

    Every field except ``profile``, ``pid`` and ``record_path`` is ``None`` when the record lacks it or holds a value of
    an unexpected type. ``raw`` keeps the whole record for fields the SDK does not model.
    """

    profile: ClaudeProfile
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


def _launch_cwd(profile: ClaudeProfile, record: RecordFile, cwd: Path | None, session_id: str | None) -> Path | None:
    started_at = record.data.get("startedAt")
    if session_id is None or isinstance(started_at, bool) or not isinstance(started_at, int | float) or started_at <= 0:
        return None
    # The transcript follows /cd, so the record's cwd names its project dir.
    found = find_transcript_file(session_id, list_projects_dirs([profile]), cwd)
    return read_launch_cwd(found[0], started_at) if found is not None else None


def _scratchpad(
    profile: ClaudeProfile,
    record: RecordFile,
    launch_cwd: Path | None,
    cwd: Path | None,
    session_id: str | None,
    *,
    own_env: bool,
) -> tuple[Path | None, bool]:
    base = launch_cwd if launch_cwd is not None else cwd
    if base is None or session_id is None:
        return None, False
    if own_env:
        return scratchpad_dir(base, session_id), launch_cwd is None
    env = _read_process_environ(record.pid)
    # Claude Code reads the project settings of the dir it starts in, so their env applies to the launch dir.
    tmpdir = _settings_tmpdir(profile, base)
    if tmpdir is None and env is not None:
        tmpdir = env.get("CLAUDE_CODE_TMPDIR") or node_tmpdir(env)
    return scratchpad_dir(base, session_id, tmpdir=tmpdir, uid=record.owner_uid), tmpdir is None or launch_cwd is None


def _to_session(profile: ClaudeProfile, record: RecordFile, *, own_env: bool = False) -> ActiveSession:
    data = record.data
    cwd_value = _str(data, "cwd")
    cwd = Path(cwd_value) if cwd_value and cwd_value != "?" else None
    session_id = _str(data, "sessionId")
    socket_value = _str(data, "messagingSocketPath")
    features = data.get("peerFeatures")
    peer_features = frozenset(f for f in features if isinstance(f, str)) if isinstance(features, list) else frozenset()
    launch_cwd = _launch_cwd(profile, record, cwd, session_id)
    scratchpad, inferred = _scratchpad(profile, record, launch_cwd, cwd, session_id, own_env=own_env)
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
    )


def _is_alive(record: RecordFile) -> bool:
    data = record.data
    return is_session_process_alive(record.pid, _str(data, "procStart"), _str(data, "pidDomain"))


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

    Each session's transcript is opened to find its :attr:`~ActiveSession.launch_cwd`, which takes a read of its start,
    or a few reads spread over it for a resumed session.

    Raises:
        ProfileNotFound: a string entry names no known profile.
    """
    selected = resolve_profiles(profiles)
    oldest = datetime.min.replace(tzinfo=UTC)
    result: list[tuple[int, ActiveSession]] = []
    for index, profile in enumerate(selected):
        for record in read_records(profile.sessions_dir):
            if check_liveness and not _is_alive(record):
                continue
            result.append((index, _to_session(profile, record)))
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
    return _to_session(profile, record, own_env=True)
