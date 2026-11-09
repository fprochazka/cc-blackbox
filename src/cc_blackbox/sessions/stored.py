"""Sessions as their transcripts record them, running or long finished."""

from __future__ import annotations

import json
import os
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from cc_blackbox.errors import AuthSwitchConfigError, TranscriptNotFound
from cc_blackbox.paths import project_slug, untruncated_slug
from cc_blackbox.profiles import ClaudeProfile
from cc_blackbox.sessions.active import ActiveSession, list_active_sessions
from cc_blackbox.transcripts.entries import Entry, ReadDiagnostics, parse_entry, parse_timestamp
from cc_blackbox.transcripts.layout import (
    ProjectsDir,
    cwd_spellings,
    is_session_file_name,
    is_session_id,
    list_projects_dirs,
    project_dirs_for,
)
from cc_blackbox.transcripts.prompts import prompt_from
from cc_blackbox.transcripts.reader import iter_entries

__all__ = [
    "SessionTranscript",
    "SubagentTranscript",
    "TitleSource",
    "active_session_for",
    "find_session_transcript",
    "list_session_transcripts",
    "read_session_transcript",
    "transcript_for",
]

TitleSource = Literal["custom", "ai", "summary", "last-prompt"]
"""Where :attr:`SessionTranscript.title` came from: a title the user set, the generated title, the summary older
Claude Code versions wrote, or the resume picker's label for the last prompt."""

# A listing reads the start and the end of each transcript. The start holds the launch cwd and the first prompt; the
# end holds the newest timestamp and the current title, because Claude Code re-appends titles when they change.
_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 128 * 1024
_SLUG_PREFIX_LENGTH = 200

# Prompts that open with these are commands or notices rather than prose, so they make poor titles.
_NOT_PROSE = (
    "<local-command-caveat",
    "<local-command-stdout",
    "<local-command-stderr",
    "<bash-input",
    "<bash-stdout",
    "<bash-stderr",
    "<bash-exit-code",
    "<command-message",
    "<command-name",
    "<command-args",
    "<task-notification",
)
_TITLE_ORDER: tuple[TitleSource, ...] = ("custom", "ai", "summary", "last-prompt")
_TITLE_KEYS: dict[str, tuple[str, TitleSource]] = {
    "custom-title": ("customTitle", "custom"),
    "ai-title": ("aiTitle", "ai"),
    "summary": ("summary", "summary"),
    "last-prompt": ("lastPrompt", "last-prompt"),
}


@dataclass(frozen=True, slots=True)
class SubagentTranscript:
    """The transcript of one subagent, under ``<session id>/subagents/`` next to the main transcript."""

    session_id: str
    """The id of the main session. A subagent has no session id of its own; its records carry the parent's."""
    agent_id: str
    path: Path
    agent_type: str | None
    description: str | None
    tool_use_id: str | None
    """The id of the Agent tool call that spawned the subagent, in the main transcript or in a parent subagent's."""
    parent_agent_id: str | None
    """Set for a subagent spawned by another subagent. Every subagent of a session sits in one flat dir."""
    spawn_depth: int | None
    meta: Mapping[str, Any]
    """The raw ``.meta.json``. Empty for older subagents, which have none."""

    def __fspath__(self) -> str:
        return os.fspath(self.path)

    def entries(
        self,
        *,
        types: Collection[str] | None = None,
        start_offset: int = 0,
        diagnostics: ReadDiagnostics | None = None,
    ) -> Iterator[Entry]:
        """Stream the subagent's records; see :func:`~cc_blackbox.iter_entries`."""
        return iter_entries(self.path, types=types, start_offset=start_offset, diagnostics=diagnostics)


@dataclass(frozen=True, slots=True)
class SessionTranscript:
    """A session's main transcript, with what a listing needs read from its first and last records.

    An :class:`~cc_blackbox.ActiveSession` is a session that also has a live registry record. Every field except
    ``session_id``, ``path``, ``projects_dir``, ``size`` and ``modified_at`` is ``None`` when the transcript lacks it.
    """

    session_id: str
    path: Path
    projects_dir: ProjectsDir
    cwd: Path | None
    """The directory the session was launched in, from its first record that has one. Unlike the registry record's
    ``cwd``, it does not change when the session moves into a worktree."""
    git_branch: str | None
    version: str | None
    """The Claude Code version of the first record. Kept as information; nothing compares it."""
    entrypoint: str | None
    """``cli`` for an interactive session; ``sdk-cli`` or ``sdk-ts`` for headless runs."""
    started_at: datetime | None
    last_activity_at: datetime | None
    """The newest record timestamp. ``None`` for a stub that has no timestamped record."""
    title: str | None
    title_source: TitleSource | None
    first_prompt: str | None
    """The text of the first prose prompt typed by a human, for callers that derive a title of their own. Slash
    commands, shell commands and notices are skipped."""
    size: int
    modified_at: datetime
    """The file's mtime. Claude Code bumps it when it appends a title, so it does not say when the session was used."""

    def __fspath__(self) -> str:
        return os.fspath(self.path)

    @property
    def sidecar_dir(self) -> Path:
        """``<slug>/<session id>/``, which holds subagent transcripts and tool results. It may not exist."""
        return self.path.with_suffix("")

    def entries(
        self,
        *,
        types: Collection[str] | None = None,
        start_offset: int = 0,
        diagnostics: ReadDiagnostics | None = None,
    ) -> Iterator[Entry]:
        """Stream the main transcript's records; see :func:`~cc_blackbox.iter_entries`."""
        return iter_entries(self.path, types=types, start_offset=start_offset, diagnostics=diagnostics)

    def subagents(self) -> list[SubagentTranscript]:
        """List the session's subagent transcripts, sorted by file name. Reads the dir on every call."""
        folder = self.sidecar_dir / "subagents"
        try:
            files = sorted(f for f in folder.iterdir() if f.name.startswith("agent-") and f.name.endswith(".jsonl"))
        except OSError:
            return []
        return [_subagent(self.session_id, f) for f in files if f.is_file()]

    def subagent(self, agent_id: str) -> SubagentTranscript | None:
        """Return the transcript of the subagent ``agent_id``, or ``None`` when the session has none by that id."""
        path = self.sidecar_dir / "subagents" / f"agent-{agent_id}.jsonl"
        if "/" in agent_id or os.sep in agent_id or not path.is_file():
            return None
        return _subagent(self.session_id, path)

    def files(self) -> list[Path]:
        """Return every file that holds the session's content: the main transcript, its subagents' transcripts and
        its tool results."""
        found = [self.path, *(s.path for s in self.subagents())]
        results = self.sidecar_dir / "tool-results"
        if results.is_dir():
            found.extend(sorted(p for p in results.rglob("*") if p.is_file()))
        return found


def _subagent(session_id: str, path: Path) -> SubagentTranscript:
    agent_id = path.name[len("agent-") : -len(".jsonl")]
    meta: dict[str, Any] = {}
    try:
        data = json.loads(path.with_name(f"agent-{agent_id}.meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        meta = data

    def text(key: str) -> str | None:
        value = meta.get(key)
        return value if isinstance(value, str) and value else None

    depth = meta.get("spawnDepth")
    return SubagentTranscript(
        session_id=session_id,
        agent_id=agent_id,
        path=path,
        agent_type=text("agentType"),
        description=text("description"),
        tool_use_id=text("toolUseId"),
        parent_agent_id=text("parentAgentId"),
        spawn_depth=depth if isinstance(depth, int) and not isinstance(depth, bool) else None,
        meta=MappingProxyType(meta),
    )


def _read_slices(path: Path, size: int) -> list[dict[str, Any]]:
    """Return the records of the whole file when it is small, else of its head and tail, in file order."""
    with path.open("rb") as handle:
        if size <= _HEAD_BYTES + _TAIL_BYTES:
            lines = handle.read().split(b"\n")
        else:
            # Each slice cuts one line in two; that partial line is dropped.
            lines = handle.read(_HEAD_BYTES).split(b"\n")[:-1]
            handle.seek(size - _TAIL_BYTES)
            lines += handle.read().split(b"\n")[1:]
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _first_str(records: Iterable[Mapping[str, Any]], key: str) -> str | None:
    for record in records:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_prompt(records: Iterable[dict[str, Any]]) -> str | None:
    for record in records:
        if record.get("type") not in ("user", "attachment"):
            continue
        entry = parse_entry(record)
        prompt = prompt_from(entry) if entry is not None else None
        if prompt is not None and prompt.origin == "human" and not prompt.text.lstrip().startswith(_NOT_PROSE):
            return prompt.text
    return None


def _summarize(path: Path, projects_dir: ProjectsDir) -> SessionTranscript:
    stat = path.stat()
    records = _read_slices(path, stat.st_size)
    timestamps = [t for r in records if (t := parse_timestamp(r.get("timestamp"))) is not None]
    titles: dict[TitleSource, str] = {}
    for record in records:
        key = _TITLE_KEYS.get(record.get("type") or "")
        value = record.get(key[0]) if key else None
        if key and isinstance(value, str) and value.strip():
            titles[key[1]] = value.strip()
    source: TitleSource | None = next((s for s in _TITLE_ORDER if s in titles), None)
    cwd = _first_str(records, "cwd")
    return SessionTranscript(
        session_id=path.stem,
        path=path,
        projects_dir=projects_dir,
        cwd=Path(cwd) if cwd else None,
        git_branch=_first_str(records, "gitBranch"),
        version=_first_str(records, "version"),
        entrypoint=_first_str(records, "entrypoint"),
        started_at=min(timestamps) if timestamps else None,
        last_activity_at=max(timestamps) if timestamps else None,
        title=titles[source] if source is not None else None,
        title_source=source,
        first_prompt=_first_prompt(records),
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
    )


def _abspath(path: str | os.PathLike[str]) -> Path:
    # Lexical, like the cwd Claude Code records: symlinks are kept.
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _may_hold(dir_name: str, slug: str) -> bool:
    if len(slug) >= _SLUG_PREFIX_LENGTH:
        return dir_name[:_SLUG_PREFIX_LENGTH] == slug[:_SLUG_PREFIX_LENGTH]
    return dir_name == slug or dir_name.startswith(slug + "-")


def _project_dirs(projects_dir: ProjectsDir) -> list[Path]:
    try:
        return sorted(p for p in projects_dir.path.iterdir() if p.is_dir())
    except OSError:
        return []


def list_session_transcripts(
    profiles: Iterable[str | ClaudeProfile] | None = None,
    *,
    projects_dirs: Iterable[str | os.PathLike[str]] | None = None,
    cwd_within: Iterable[str | os.PathLike[str]] | None = None,
    active_since: datetime | None = None,
) -> list[SessionTranscript]:
    """List the main transcripts in the projects dirs of ``profiles``, newest activity first.

    ``profiles`` and ``projects_dirs`` select the projects dirs as in :func:`~cc_blackbox.list_projects_dirs`. Stubs
    without a timestamped record come last, by mtime.

    ``cwd_within`` keeps sessions launched in one of the given dirs or below it. A dir reached through a symlink also
    matches under its resolved path, which is the one Claude Code records. Only project dirs whose name can be the slug
    of such a dir are read, which makes a listing for one repo cheap. A session whose transcript sits in a
    project dir of another name, for example one resumed from elsewhere, is therefore not found this way; list
    without ``cwd_within`` and filter on :attr:`SessionTranscript.cwd` to find those. A transcript without a ``cwd``
    is kept when its dir is exactly the slug of one of the dirs.

    ``active_since`` skips transcripts not written to since then. Transcripts are append-only, so the mtime is enough.

    Raises:
        ProfileNotFound: a string entry of ``profiles`` names no known profile.
    """
    within = list(dict.fromkeys(s for p in cwd_within for s in cwd_spellings(p))) if cwd_within is not None else None
    slugs = [untruncated_slug(p) for p in within] if within is not None else None
    exact = {project_slug(p) for p in within} if within is not None else set()
    since = active_since.timestamp() if active_since is not None else None

    found: list[SessionTranscript] = []
    for projects_dir in list_projects_dirs(profiles, paths=projects_dirs):
        for project in _project_dirs(projects_dir):
            if slugs is not None and not any(_may_hold(project.name, slug) for slug in slugs):
                continue
            try:
                files = sorted(f for f in project.iterdir() if is_session_file_name(f.name))
            except OSError:
                continue
            for file in files:
                try:
                    if since is not None and file.stat().st_mtime < since:
                        continue
                    transcript = _summarize(file, projects_dir)
                except OSError:
                    continue
                if within is not None:
                    if transcript.cwd is None:
                        if project.name not in exact:
                            continue
                    elif not any(_abspath(transcript.cwd).is_relative_to(p) for p in within):
                        continue
                found.append(transcript)

    found.sort(key=lambda t: (t.last_activity_at is not None, t.last_activity_at or t.modified_at), reverse=True)
    return found


def find_session_transcript(
    session_id: str,
    profiles: Iterable[str | ClaudeProfile] | None = None,
    *,
    projects_dirs: Iterable[str | os.PathLike[str]] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> SessionTranscript:
    """Find the main transcript of ``session_id`` in the projects dirs of ``profiles``, whatever dir it was launched in.

    ``profiles`` and ``projects_dirs`` select the projects dirs as in :func:`~cc_blackbox.list_projects_dirs`. To
    search one profile, pass ``[profile]`` or its name. To search a config dir that no listed profile uses, pass
    ``list_profiles([config_dir])``, which keeps the profile on :attr:`SessionTranscript.projects_dir`.

    Without ``cwd``, every project dir is checked for the file. ``cwd`` is a hint, the dir the session was most likely
    launched in: its project dirs from :func:`~cc_blackbox.project_dirs_for` are checked first, and the rest only when
    they do not hold the session. A shared projects dir can have thousands of project dirs, so the hint saves a stat
    of each.

    The path is never derived from a registry record's ``cwd``, which changes when a session enters a worktree.

    Raises:
        TranscriptNotFound: ``session_id`` is not a session id, or no projects dir holds its transcript.
        ProfileNotFound: a string entry of ``profiles`` names no known profile.
    """
    if not is_session_id(session_id):
        raise TranscriptNotFound(f"{session_id!r} is not a session id. A session id is a UUID.")
    searched = list_projects_dirs(profiles, paths=projects_dirs)
    if cwd is not None:
        by_path = {d.path: d for d in searched}
        for project in project_dirs_for(cwd, projects_dirs=by_path):
            candidate = project / f"{session_id}.jsonl"
            if candidate.is_file():
                return _summarize(candidate, by_path[project.parent])
    for projects_dir in searched:
        for project in _project_dirs(projects_dir):
            candidate = project / f"{session_id}.jsonl"
            if candidate.is_file():
                return _summarize(candidate, projects_dir)
    where = ", ".join(str(d.path) for d in searched) or "no projects dir"
    raise TranscriptNotFound(f"No transcript of session {session_id} in {where}.")


def read_session_transcript(path: str | os.PathLike[str], projects_dir: ProjectsDir | None = None) -> SessionTranscript:
    """Read the transcript at ``path``, for example the ``transcript_path`` a hook receives.

    The session id is the file name without ``.jsonl``. ``projects_dir`` defaults to the dir two levels up, with the
    profiles that use it.

    Raises:
        TranscriptNotFound: no file is at ``path``.
    """
    file = Path(os.path.abspath(os.fspath(path)))
    if not file.is_file():
        raise TranscriptNotFound(f"No transcript at {file}.")
    if projects_dir is None:
        real = Path(os.path.realpath(file.parent.parent))
        try:
            known = list_projects_dirs()
        except AuthSwitchConfigError:
            known = []
        projects_dir = next((d for d in known if d.path == real), ProjectsDir(real, ()))
    try:
        return _summarize(file, projects_dir)
    except OSError as e:
        raise TranscriptNotFound(f"Cannot read the transcript {file}: {e}") from e


def transcript_for(session: ActiveSession) -> SessionTranscript | None:
    """Return the transcript of a running session, or ``None`` when it has no session id or no transcript yet."""
    if session.session_id is None:
        return None
    try:
        return find_session_transcript(session.session_id, [session.profile])
    except TranscriptNotFound:
        return None


def active_session_for(
    transcript: SessionTranscript, *, sessions: Sequence[ActiveSession] | None = None
) -> ActiveSession | None:
    """Return the running session that writes ``transcript``, or ``None`` when no live registry record names it.

    ``sessions`` defaults to :func:`~cc_blackbox.list_active_sessions` of the transcript's profiles. A match needs the
    same session id and a profile that uses the transcript's projects dir.
    """
    dirs = {p.config_dir for p in transcript.projects_dir.profiles}
    candidates = sessions
    if candidates is None:
        candidates = list_active_sessions(transcript.projects_dir.profiles) if dirs else []
    return next(
        (s for s in candidates if s.session_id == transcript.session_id and s.profile.config_dir in dirs),
        None,
    )
