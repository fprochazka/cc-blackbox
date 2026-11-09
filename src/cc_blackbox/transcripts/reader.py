"""Stream the records of a transcript file."""

from __future__ import annotations

import json
import os
from collections.abc import Collection, Iterator

from cc_blackbox.transcripts.entries import Entry, ReadDiagnostics, parse_entry

__all__ = ["iter_entries"]


def iter_entries(
    source: str | os.PathLike[str],
    *,
    types: Collection[str] | None = None,
    start_offset: int = 0,
    diagnostics: ReadDiagnostics | None = None,
) -> Iterator[Entry]:
    """Yield the records of the transcript ``source`` in file order, reading one line at a time.

    ``source`` is a path, or anything with a path such as a :class:`~cc_blackbox.SessionTranscript`. A line that is not
    a JSON object with a string ``type`` is counted in ``diagnostics`` and skipped. A last line without a newline that
    does not parse is a record Claude Code is still writing; it is left for the next read and not counted.

    With ``types``, only records of those types are yielded. Lines that cannot hold one of them are skipped before
    they are decoded, which makes reading prompts or tool calls much cheaper than reading everything. Diagnostics then
    cover only the decoded lines.

    ``start_offset`` is a byte offset where reading starts, normally the ``end_offset`` of the last entry a previous
    read yielded, so a reader can follow a growing file. Line numbers then count from that offset.

    Raises:
        OSError: the file cannot be opened.
    """
    diag = diagnostics if diagnostics is not None else ReadDiagnostics()
    wanted = frozenset(types) if types is not None else None
    # A record of type t holds the JSON string "t" somewhere, however the JSON is spaced. Checking for that string is
    # a cheap necessary condition; the parsed type decides.
    needles = tuple(json.dumps(t).encode() for t in wanted) if wanted is not None else ()
    with open(os.fspath(source), "rb") as handle:
        handle.seek(start_offset)
        offset = start_offset
        line_number = 0
        for line in handle:
            line_number += 1
            begin, offset = offset, offset + len(line)
            if not line.strip():
                continue
            diag.lines += 1
            if needles and not any(needle in line for needle in needles):
                continue
            complete = line.endswith(b"\n")
            try:
                data = json.loads(line)
            except ValueError:
                if complete:
                    diag.malformed_lines += 1
                else:
                    diag.lines -= 1
                continue
            if not isinstance(data, dict):
                diag.malformed_lines += 1
                continue
            entry = parse_entry(data, line_number=line_number, offset=begin, end_offset=offset, diagnostics=diag)
            if entry is not None and (wanted is None or entry.type in wanted):
                yield entry
