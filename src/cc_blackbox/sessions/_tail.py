"""Facts read from the end of a transcript, newest record first.

Claude Code appends records, so the newest record of a kind is the one nearest the end. Reading backwards from the end
finds it in the first chunk for an active transcript, however long the file is. A kind the file never holds costs a
read of the whole file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

_CHUNK = 64 * 1024

T = TypeVar("T")


def _lines_backward(path: Path) -> Iterator[bytes]:
    """Yield the lines of the file at ``path``, last first. A line still being written is yielded as it is."""
    with path.open("rb") as handle:
        position = os.fstat(handle.fileno()).st_size
        rest = b""
        while position > 0:
            step = min(_CHUNK, position)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + rest).split(b"\n")
            # The first piece may be the end of a line that starts in the previous chunk.
            rest = lines[0]
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line
        if rest.strip():
            yield rest


def last_record(path: Path, needles: tuple[bytes, ...], pick: Callable[[dict[str, Any]], T | None]) -> T | None:
    """Return ``pick`` of the newest record that ``pick`` accepts, or ``None``.

    Only lines that contain one of ``needles`` are decoded. An unreadable file counts as one without such a record.
    """
    try:
        for line in _lines_backward(path):
            if not any(needle in line for needle in needles):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                found = pick(record)
                if found is not None:
                    return found
    except OSError:
        return None
    return None
