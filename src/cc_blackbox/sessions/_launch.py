"""Where a session's transcript is."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from cc_blackbox.transcripts.layout import ProjectsDir, is_session_id, project_dirs_for


def project_dirs(projects_dir: ProjectsDir) -> list[Path]:
    """Return the dirs directly under ``projects_dir``, sorted."""
    try:
        return sorted(p for p in projects_dir.path.iterdir() if p.is_dir())
    except OSError:
        return []


def find_transcript_file(
    session_id: str, searched: Sequence[ProjectsDir], cwd: str | os.PathLike[str] | None
) -> tuple[Path, ProjectsDir] | None:
    """Return the main transcript of ``session_id`` and its projects dir, checking the project dirs of ``cwd`` first."""
    if not is_session_id(session_id):
        return None
    if cwd is not None:
        by_path = {d.path: d for d in searched}
        for project in project_dirs_for(cwd, projects_dirs=by_path):
            candidate = project / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate, by_path[project.parent]
    for projects_dir in searched:
        for project in project_dirs(projects_dir):
            candidate = project / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate, projects_dir
    return None
