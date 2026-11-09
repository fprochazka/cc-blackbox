"""Read the ``<config_dir>/sessions/<pid>.json`` records that running Claude Code sessions write.

A session writes its record at startup, rewrites it on events such as a status change or a rename, and deletes it on
a clean exit. A killed session leaves its record behind until another session sweeps it. The record shape changes
between Claude Code versions, so only the pid, taken from the file name, is required here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Claude Code accepts only a canonical decimal pid as the file name; `.key` files and other entries are skipped.
_RECORD_NAME = re.compile(r"^(0|[1-9][0-9]*)\.json$")


@dataclass(frozen=True, slots=True)
class RecordFile:
    pid: int
    path: Path
    owner_uid: int | None
    data: dict[str, Any]


def read_records(sessions_dir: Path) -> list[RecordFile]:
    """Return the parseable records in ``sessions_dir``, sorted by pid.

    A missing dir gives an empty list. A record that vanishes while being read, holds partial JSON, or is not a JSON
    object is skipped.
    """
    try:
        entries = list(sessions_dir.iterdir())
    except OSError:
        return []
    records: list[RecordFile] = []
    for entry in entries:
        match = _RECORD_NAME.match(entry.name)
        if match is None:
            continue
        try:
            stat = entry.stat()
            text = entry.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        owner_uid = stat.st_uid if hasattr(stat, "st_uid") else None
        records.append(RecordFile(int(match.group(1)), entry, owner_uid, data))
    records.sort(key=lambda r: r.pid)
    return records
