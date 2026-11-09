"""Find the sessions whose files contain literal text."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from cc_blackbox.sessions.stored import SessionTranscript

__all__ = ["sessions_containing"]


@dataclass(frozen=True, slots=True)
class _Term:
    forms: tuple[bytes, ...]
    """For an ASCII term: the lowercase bytes of the term and of its JSON-escaped form."""
    folded: tuple[str, ...]
    """For a non-ASCII term: the case-folded term and its JSON-escaped form."""


def _term(text: str) -> _Term:
    # A transcript is JSON, so a quote, a backslash or a newline in the text is stored escaped.
    escaped = json.dumps(text, ensure_ascii=False)[1:-1]
    variants = dict.fromkeys((text, escaped))
    if text.isascii():
        return _Term(tuple(v.lower().encode() for v in variants), ())
    return _Term((), tuple(v.casefold() for v in variants))


def _found_in(
    data: bytes, term: _Term, lowered: bytes | None, folded: str | None
) -> tuple[bool, bytes | None, str | None]:
    if term.forms:
        lowered = data.lower() if lowered is None else lowered
        return any(form in lowered for form in term.forms), lowered, folded
    folded = data.decode("utf-8", errors="replace").casefold() if folded is None else folded
    return any(form in folded for form in term.folded), lowered, folded


def _matches(files: Sequence[str], terms: Sequence[_Term], match_any: bool) -> bool:
    missing = list(terms)
    for path in files:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        lowered: bytes | None = None
        folded: str | None = None
        still_missing: list[_Term] = []
        for term in missing:
            hit, lowered, folded = _found_in(data, term, lowered, folded)
            if hit and match_any:
                return True
            if not hit:
                still_missing.append(term)
        missing = still_missing
        if not missing:
            return True
    return False


def _matches_packed(args: tuple[Sequence[str], Sequence[_Term], bool]) -> bool:
    return _matches(*args)


def sessions_containing(
    sessions: Iterable[SessionTranscript],
    terms: Sequence[str],
    *,
    match_any: bool = False,
    workers: int = 1,
) -> list[SessionTranscript]:
    """Return the sessions whose files contain every one of ``terms``, or any of them with ``match_any``.

    The search is literal and ignores case. It covers each session's main transcript, its subagent transcripts and its
    tool results (see :meth:`SessionTranscript.files`), and the terms may sit in different files of one session. It
    reads raw bytes rather than parsed records, so it also matches keys, ids and tool output; parse the entries of the
    sessions it returns to decide what a match means.

    An ASCII term ignores ASCII case only, which keeps the scan fast. A term with other characters is compared after
    Unicode case folding, at several times the cost. Blank terms are ignored; with none left, the result is empty.
    ``workers`` above 1 scans sessions in that many processes. Results keep the order of ``sessions``.
    """
    usable = [_term(t) for t in terms if t.strip()]
    candidates = list(sessions)
    if not usable or not candidates:
        return []
    jobs = [([os.fspath(p) for p in s.files()], usable, match_any) for s in candidates]
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            flags = list(pool.map(_matches_packed, jobs, chunksize=8))
    else:
        flags = [_matches_packed(job) for job in jobs]
    return [s for s, hit in zip(candidates, flags, strict=True) if hit]
