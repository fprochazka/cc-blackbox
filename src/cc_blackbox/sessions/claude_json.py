"""Which profile a session last ended under, from ``projects[<dir>].lastSessionId`` in each profile's ``.claude.json``.

Claude Code writes ``lastSessionId`` when a session ends, one per directory per config dir, and the next session that
ends in that directory overwrites it. A running session is therefore missing unless an earlier run of it ended there.
The file is rewritten constantly by running sessions, so a read that fails is treated as "no data" for that profile.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from cc_blackbox.profiles import ClaudeProfile


def _read_last_session_ids(path: Path) -> frozenset[str]:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return frozenset()
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return frozenset()
    ids = (entry.get("lastSessionId") for entry in projects.values() if isinstance(entry, dict))
    return frozenset(value for value in ids if isinstance(value, str) and value)


class LastSessionIndex:
    """The ``lastSessionId`` values of each profile, read from its ``.claude.json`` on first use and then kept.

    Create one per listing, so a file is parsed at most once per call and a later call sees new values.
    """

    def __init__(self) -> None:
        self._ids: dict[Path, frozenset[str]] = {}

    def _of(self, profile: ClaudeProfile) -> frozenset[str]:
        path = profile.claude_json
        if path not in self._ids:
            self._ids[path] = _read_last_session_ids(path)
        return self._ids[path]

    def owner(self, session_id: str, candidates: Sequence[ClaudeProfile]) -> ClaudeProfile | None:
        """Return the one candidate whose ``.claude.json`` names ``session_id``, or ``None`` for none or several."""
        matches = [p for p in candidates if session_id in self._of(p)]
        return matches[0] if len(matches) == 1 else None
