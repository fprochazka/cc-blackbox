"""The start log that claude-code-auth-switch keeps next to each transcript, which says which profile ran a session.

Neither a registry record nor a transcript names the config dir its session runs under, and profiles can share both
``projects/`` and ``sessions/``. auth-switch therefore installs a SessionStart hook that appends one JSON line per
start to ``<slug>/<session id>/auth-switch-profile-starts.jsonl``, next to the transcript::

    {"v": 1, "ts": <epoch ms>, "session_id": ..., "pid": ..., "source": ...,
     "profile": ..., "config_dir": ..., "cwd": ...,
     "role": ..., "role_prompt": ..., "role_prompt_mode": ...}

``source`` is the SessionStart source: ``startup``, ``resume``, ``clear``, ``compact`` or ``fork``. ``profile`` and
``config_dir`` are ``FP_CC_AUTH_SWITCH_PROFILE`` and ``CLAUDE_CONFIG_DIR``, ``null`` when unset. ``pid`` is the
Claude Code process, the one named by ``sessions/<pid>.json``, or ``null`` when the hook could not find it. A resumed
session gets a line per run, each with its own pid and possibly another profile.

``role``, ``role_prompt`` and ``role_prompt_mode`` are ``FP_CC_AUTH_SWITCH_ROLE``, ``FP_CC_AUTH_SWITCH_ROLE_PROMPT``
and ``FP_CC_AUTH_SWITCH_ROLE_PROMPT_MODE``: the auth-switch role the run was started with, the file holding its prompt
and how that prompt reached the session. Lines written before auth-switch had roles lack them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cc_blackbox.profiles import ClaudeProfile, list_profiles, profile_for_config_dir
from cc_blackbox.sessions._launch import _START_TOLERANCE_MS

__all__ = ["PROFILE_STARTS_FILE", "ProfileStart", "read_profile_starts"]

PROFILE_STARTS_FILE = "auth-switch-profile-starts.jsonl"
_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ProfileStart:
    """One start of a session, as the claude-code-auth-switch SessionStart hook logged it."""

    started_at: datetime
    """When the hook ran, a moment after the process started."""
    started_at_ms: int
    """:attr:`started_at` in epoch milliseconds, as logged, to compare with a registry record's ``startedAt``."""
    session_id: str | None
    pid: int | None
    """The Claude Code process, or ``None`` when the hook could not find it."""
    source: str | None
    """``startup``, ``resume``, ``clear``, ``compact`` or ``fork``."""
    profile: str | None
    """The auth-switch profile name from ``FP_CC_AUTH_SWITCH_PROFILE``, or ``None`` outside an auth-switch wrapper."""
    config_dir: Path | None
    """``CLAUDE_CONFIG_DIR`` as the process got it, or ``None`` when it was unset, which means ``~/.claude``."""
    cwd: Path | None
    role: str | None
    """The auth-switch role the run was started with, or ``None`` when it had none or the line predates roles."""
    role_prompt: Path | None
    """The file holding the role's prompt, or ``None`` when the role has no prompt."""
    role_prompt_mode: str | None
    """How the role's prompt reached the session: ``append`` or ``replace`` put the file into the system prompt,
    ``none`` leaves it to a hook to inject."""
    raw: Mapping[str, Any]

    def find_profile(self, profiles: Sequence[ClaudeProfile] | None = None) -> ClaudeProfile | None:
        """Return the profile of ``profiles``, which defaults to :func:`~cc_blackbox.list_profiles`, that this start
        ran under: the one with :attr:`config_dir`, else the one named :attr:`profile`. ``None`` when neither is
        listed."""
        candidates = list_profiles() if profiles is None else profiles
        return profile_for_config_dir(self.config_dir, self.profile, candidates)


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse(line: bytes) -> ProfileStart | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or _int(data, "v") != _FORMAT_VERSION:
        return None
    ts = _int(data, "ts")
    if ts is None or ts <= 0:
        return None
    try:
        started_at = datetime.fromtimestamp(ts / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    config_dir, cwd, role_prompt = _str(data, "config_dir"), _str(data, "cwd"), _str(data, "role_prompt")
    return ProfileStart(
        started_at=started_at,
        started_at_ms=ts,
        session_id=_str(data, "session_id"),
        pid=_int(data, "pid"),
        source=_str(data, "source"),
        profile=_str(data, "profile"),
        config_dir=Path(config_dir) if config_dir else None,
        cwd=Path(cwd) if cwd else None,
        role=_str(data, "role"),
        role_prompt=Path(role_prompt) if role_prompt else None,
        role_prompt_mode=_str(data, "role_prompt_mode"),
        raw=data,
    )


def read_profile_starts(path: Path) -> list[ProfileStart]:
    """Return the starts logged in ``path``, in file order.

    A missing file gives an empty list. A line that is not JSON, has another format version ``v`` or lacks ``ts`` is
    skipped, as is a line cut short by a concurrent append.
    """
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return []
    return [start for line in lines if line.strip() and (start := _parse(line)) is not None]


def start_of_process(starts: Sequence[ProfileStart], pid: int, started_at_ms: float | None) -> ProfileStart | None:
    """Return the newest start of the process ``pid``, ignoring starts older than ``started_at_ms`` minus a tolerance.

    A resumed session's log holds the starts of earlier processes, and a pid can be reused, so the pid alone is not
    enough. The tolerance is the one used for transcripts, see :mod:`cc_blackbox.sessions._launch`.
    """
    threshold = started_at_ms - _START_TOLERANCE_MS if started_at_ms is not None else None
    matching = [s for s in starts if s.pid == pid and (threshold is None or s.started_at_ms >= threshold)]
    return max(reversed(matching), key=lambda s: s.started_at_ms, default=None)
