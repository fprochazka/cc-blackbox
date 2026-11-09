"""Where Claude Code keeps session transcripts, and which files under a projects dir belong to which session.

Claude Code 2.1.x writes this layout under ``<config dir>/projects/``::

    <slug>/<session id>.jsonl                          the session's main transcript
    <slug>/<session id>/subagents/agent-<id>.jsonl     one transcript per subagent, flat at any nesting depth
    <slug>/<session id>/subagents/agent-<id>.meta.json what spawned the subagent; missing for older ones
    <slug>/<session id>/tool-results/                  tool output too large to keep in the transcript
    <slug>/memory/                                     the project's auto memory, which belongs to no session

``<slug>`` is :func:`cc_blackbox.project_slug` of the directory the session was launched in. Other tools write files
next to transcripts too, for example ``<session id>-ftoc.jsonl``, so only a file named by a canonical UUID is a session.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cc_blackbox.profiles import ClaudeProfile, resolve_profiles

__all__ = [
    "ProjectsDir",
    "is_session_file_name",
    "is_session_id",
    "list_projects_dirs",
    "session_id_for_path",
]

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SESSION_ID = re.compile(rf"^{_UUID}$", re.IGNORECASE)
_SESSION_FILE = re.compile(rf"^({_UUID})\.jsonl$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ProjectsDir:
    """One ``projects/`` dir, with every profile that uses it.

    Profiles can share a projects dir through a symlink, and a transcript does not record which profile wrote it, so a
    session is attributed to its projects dir and to all of that dir's profiles.
    """

    path: Path
    """Absolute and symlink-resolved."""
    profiles: tuple[ClaudeProfile, ...]
    """The profiles whose ``projects_dir`` resolves to :attr:`path`, in the order they were given. Empty for a dir given
    by path."""


def is_session_id(value: str) -> bool:
    """Return whether ``value`` is a canonical UUID, the only form Claude Code uses for session ids."""
    return _SESSION_ID.match(value) is not None


def is_session_file_name(name: str) -> bool:
    """Return whether ``name`` is the file name of a main transcript: ``<session id>.jsonl``."""
    return _SESSION_FILE.match(name) is not None


def list_projects_dirs(
    profiles: Iterable[str | ClaudeProfile] | None = None,
    *,
    paths: Iterable[str | os.PathLike[str]] | None = None,
) -> list[ProjectsDir]:
    """List the distinct projects dirs of ``profiles``, which defaults to :func:`cc_blackbox.list_profiles`.

    Profiles whose projects dirs resolve to the same directory share one entry. A profile without a projects dir is
    left out. With ``paths``, only those dirs are listed, each with no profiles, and ``profiles`` is ignored.

    Raises:
        ProfileNotFound: a string entry of ``profiles`` names no known profile.
    """
    if paths is not None:
        dirs: dict[Path, ProjectsDir] = {}
        for raw in paths:
            real = Path(os.path.realpath(os.path.expanduser(os.fspath(raw))))
            if real.is_dir() and real not in dirs:
                dirs[real] = ProjectsDir(real, ())
        return list(dirs.values())

    grouped: dict[Path, list[ClaudeProfile]] = {}
    for profile in resolve_profiles(profiles):
        real = Path(os.path.realpath(profile.projects_dir))
        if real.is_dir():
            grouped.setdefault(real, []).append(profile)
    return [ProjectsDir(path, tuple(members)) for path, members in grouped.items()]


def cwd_spellings(cwd: str | os.PathLike[str]) -> list[Path]:
    """Return the spellings of ``cwd`` that can name its project dir: the symlink-resolved path, then ``cwd`` as given
    when that differs.

    Claude Code names the project dir after its ``process.cwd()``, which the OS reports with symlinks resolved, so a
    session started in a symlinked dir sits under the slug of the resolved path. Callers often hold the path through
    the symlink. The given spelling stays a candidate because not every way of setting a session's cwd is known to
    resolve it. Both are user-expanded and absolute.
    """
    lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(cwd))))
    real = Path(os.path.realpath(lexical))
    return [real] if real == lexical else [real, lexical]


def session_id_for_path(path: str | os.PathLike[str], projects_dir: str | os.PathLike[str]) -> str | None:
    """Return the id of the session that owns ``path``, a file or dir under ``projects_dir``.

    A main transcript belongs to its own session. Anything under ``<slug>/<session id>/``, such as a subagent
    transcript or a tool result, belongs to that session. Anything else returns ``None``: files of other tools, memory
    dirs, and paths outside ``projects_dir``. Both paths are made absolute but symlinks are not resolved, so pass them
    in the same form.
    """
    rel = os.path.relpath(os.path.abspath(os.fspath(path)), os.path.abspath(os.fspath(projects_dir)))
    parts = [part for part in rel.split(os.sep) if part and part != "."]
    if len(parts) < 2 or parts[0] == "..":
        return None
    if len(parts) == 2:
        match = _SESSION_FILE.match(parts[1])
        return match.group(1) if match else None
    return parts[1] if is_session_id(parts[1]) else None
