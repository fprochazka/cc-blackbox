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
from typing import Any

_CHUNK = 64 * 1024


def _lines_backward(path: Path) -> Iterator[bytes]:
    """Yield the lines of the file at ``path``, last first. A line still being written is yielded as it is."""
    with path.open("rb") as handle:
        position = os.fstat(handle.fileno()).st_size
        # The pieces of a line that started before the chunks read so far, last piece first. They are joined once the
        # line's start is found, so a line spanning many chunks costs a time linear in its length.
        pending: list[bytes] = []
        while position > 0:
            step = min(_CHUNK, position)
            position -= step
            handle.seek(position)
            chunk = handle.read(step)
            lines = chunk.split(b"\n")
            if len(lines) == 1:
                pending.append(chunk)
                continue
            last = lines[-1] + b"".join(reversed(pending))
            if last.strip():
                yield last
            for line in reversed(lines[1:-1]):
                if line.strip():
                    yield line
            # The first piece may be the end of a line that starts in an earlier chunk.
            pending = [lines[0]]
        rest = b"".join(reversed(pending))
        if rest.strip():
            yield rest


def last_record[T](path: Path, needles: tuple[bytes, ...], pick: Callable[[dict[str, Any]], T | None]) -> T | None:
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
