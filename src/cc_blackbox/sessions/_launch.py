"""Where a session's transcript is, and the directory its running process was launched in.

Claude Code 2.1.323 derives the scratchpad from the launch directory, which it does not write to the registry record.
The record's ``cwd`` follows ``/cd``, and so does the transcript file, but the transcript's records keep the ``cwd``
each was written in. The first record of the running process therefore carries its launch directory.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from cc_blackbox.transcripts.entries import parse_timestamp
from cc_blackbox.transcripts.layout import ProjectsDir, is_session_id, project_dirs_for

# Claude Code stamps the first records of a new process up to a few milliseconds before the startedAt of its registry
# record. A resumed session's older records are older than that by at least the time between the two runs.
_START_TOLERANCE_MS = 2000
# Below this many bytes between the bounds, the bisection gives way to reading line by line.
_SCAN_WINDOW = 64 * 1024
# How many records of the running process to read for one that carries a cwd.
_MAX_RECORDS_AFTER_START = 200


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


def _parse(line: bytes) -> tuple[float | None, str | None]:
    """Return the timestamp in epoch milliseconds and the ``cwd`` of one transcript line."""
    try:
        record = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(record, dict):
        return None, None
    moment = parse_timestamp(record.get("timestamp"))
    cwd = record.get("cwd")
    return (
        moment.timestamp() * 1000 if moment is not None else None,
        cwd if isinstance(cwd, str) and cwd else None,
    )


def _probe(handle: BinaryIO, offset: int, limit: int) -> tuple[int, float] | None:
    """Return the end offset and the timestamp of the first timestamped line that starts in ``[offset, limit)``."""
    handle.seek(max(offset - 1, 0))
    if offset > 0:
        handle.readline()  # the rest of the line that holds offset - 1, so the next read starts on a line
    while handle.tell() < limit:
        line = handle.readline()
        if not line:
            return None
        moment, _ = _parse(line)
        if moment is not None:
            return handle.tell(), moment
    return None


def _scan_start(handle: BinaryIO, size: int, threshold: float) -> int:
    """Return a line start at most about :data:`_SCAN_WINDOW` bytes before the first line stamped at ``threshold`` or
    later.

    Claude Code appends records in the order it writes them. A resumed session appends to the transcript of its earlier
    runs, whose records all predate the new process, so the new run starts deep in the file. Bisecting the file by
    offset finds it without reading the earlier runs. Timestamps within one run can be out of order by minutes, which
    does not move the boundary between runs.
    """
    first = _probe(handle, 0, size)
    if first is None or first[1] >= threshold:
        return 0
    low, high = first[0], size
    while high - low > _SCAN_WINDOW:
        middle = (low + high) // 2
        probe = _probe(handle, middle, high)
        if probe is None or probe[1] >= threshold:
            high = middle
        else:
            low = probe[0]
    return low


def read_launch_cwd(path: Path, started_at_ms: float) -> Path | None:
    """Return the ``cwd`` of the first record in the transcript at ``path`` stamped at ``started_at_ms`` or later.

    ``None`` when the file cannot be read or the process has written no record with a ``cwd`` yet.
    """
    threshold = started_at_ms - _START_TOLERANCE_MS
    try:
        with path.open("rb") as handle:
            handle.seek(_scan_start(handle, os.fstat(handle.fileno()).st_size, threshold))
            seen = 0
            for line in handle:
                moment, cwd = _parse(line)
                if moment is None or moment < threshold:
                    continue
                if cwd is not None:
                    return Path(cwd)
                seen += 1
                if seen >= _MAX_RECORDS_AFTER_START:
                    return None
    except OSError:
        return None
    return None
