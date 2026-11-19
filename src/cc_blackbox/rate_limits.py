"""The rate limits of a profile's account, as the sessions started through claude-code-auth-switch observed them.

Claude Code sends the account's rate limits to the status line command it runs, and to nothing else that a tool can
read without credentials. The launchers of claude-code-auth-switch put their own status line in front of the user's,
and it merges the ``rate_limits`` of every session of the profile into ``<config dir>/auth-switch-rate-limits.json``.
This module reads that file. Its format is a contract kept by auth-switch, versioned by ``v``.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cc_blackbox.profiles import ClaudeProfile

__all__ = ["RATE_LIMITS_FILE", "RateLimitWindow", "RateLimits", "read_rate_limits"]

RATE_LIMITS_FILE = "auth-switch-rate-limits.json"
"""The file in a profile's config dir that claude-code-auth-switch writes the rate limits to."""

_FORMAT_VERSION = 1
# Claude Code's resets_at is in epoch seconds; values outside 2000-2100 are treated as drifted data.
_RESETS_AT_RANGE = (946_684_800, 4_102_444_800)


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    """One rate limit window of an account, as a session of the profile last reported it.

    The value is a lower bound: the account can also be used on another machine or on claude.ai, which this machine's
    sessions do not see until their next response. It stays valid, however old, until the window resets.
    """

    name: str
    """``five_hour`` or ``seven_day`` for the windows of a claude.ai subscription, ``spend_limit`` for the spend
    limit of a Claude apps gateway, or a window a newer Claude Code added."""
    used_percent: float
    """How much of the window was used, 0 to 100, as last observed. A spend limit can go above 100."""
    resets_at: datetime | None
    """When the window resets, in UTC. ``None`` when the file has no usable reset time for it."""
    measured_at: datetime
    """The last time a session reported this value."""
    changed_at: datetime | None
    """When the stored value last changed."""
    session_id: str | None
    """The session that reported the value."""
    raw: Mapping[str, Any]
    """The window object from the file. A spend limit has ``used_usd``, ``limit_usd`` and ``period`` here when Claude
    Code reported them."""

    def is_reset(self, now: datetime | None = None) -> bool:
        """Tell whether the window has reset since it was measured: :attr:`resets_at` is at or before ``now``.

        :attr:`used_percent` is then the usage from before the reset, and the current usage is unknown until a session
        reports the new window; it is not known to be 0, because the account may have been used elsewhere since. A
        window without :attr:`resets_at` is never reported as reset. ``now`` defaults to the current time and must be
        timezone-aware.

        Raises:
            ValueError: ``now`` is a naive datetime.
        """
        moment = _aware_now(now)
        return self.resets_at is not None and self.resets_at <= moment


@dataclass(frozen=True, slots=True)
class RateLimits:
    """The rate limits of one profile's account, from the file that claude-code-auth-switch's status line writes.

    The file is per profile, not per session: every session of the profile writes to it, and a stale session cannot
    lower a value or bring back an older window. There is no expiry. With no session running the values stay as they
    were last measured, until their windows reset; :attr:`measured_at` says how old they are. The object is a snapshot
    of the file; :meth:`RateLimitWindow.is_reset` compares with the current time on every call.
    """

    path: Path
    profile_name: str | None
    """The profile name the writer recorded, from ``FP_CC_AUTH_SWITCH_PROFILE``."""
    written_at: datetime
    measured_at: datetime | None
    """The newest :attr:`RateLimitWindow.measured_at` of any window, or ``None`` without windows."""
    session_id: str | None
    """The session that wrote the file last."""
    model: str | None
    """The model of the session that wrote the file last."""
    claude_version: str | None
    """The Claude Code version of the session that wrote the file last."""
    windows: Mapping[str, RateLimitWindow]
    """Every window in the file, by name, the ones this version does not know included."""
    raw: Mapping[str, Any]
    """The whole file."""

    @property
    def five_hour(self) -> RateLimitWindow | None:
        """The rolling five-hour window of a claude.ai subscription."""
        return self.windows.get("five_hour")

    @property
    def seven_day(self) -> RateLimitWindow | None:
        """The weekly window of a claude.ai subscription."""
        return self.windows.get("seven_day")

    @property
    def spend_limit(self) -> RateLimitWindow | None:
        """The spend limit of a Claude apps gateway."""
        return self.windows.get("spend_limit")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _ms_time(value: object) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        return datetime.fromtimestamp(number / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _aware_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be a timezone-aware datetime, such as datetime.now(UTC); got the naive {now!r}")
    return now


def _window(name: str, data: object) -> RateLimitWindow | None:
    if not isinstance(data, Mapping):
        return None
    used = _number(data.get("used_percentage"))
    measured_at = _ms_time(data.get("observed_at"))
    if used is None or used < 0 or measured_at is None:
        return None
    resets_at = None
    seconds = _number(data.get("resets_at"))
    if seconds is not None and _RESETS_AT_RANGE[0] <= seconds <= _RESETS_AT_RANGE[1]:
        resets_at = datetime.fromtimestamp(seconds, tz=UTC)
    session_id = data.get("session_id")
    return RateLimitWindow(
        name=name,
        used_percent=used,
        resets_at=resets_at,
        measured_at=measured_at,
        changed_at=_ms_time(data.get("changed_at")),
        session_id=session_id if isinstance(session_id, str) and session_id else None,
        raw=MappingProxyType(dict(data)),
    )


def read_rate_limits(config_dir: str | os.PathLike[str] | ClaudeProfile) -> RateLimits | None:
    """Read the rate limits that claude-code-auth-switch recorded for a profile.

    ``config_dir`` is a profile or its config dir. Reads the file on every call; the result is a snapshot, and
    :meth:`RateLimitWindow.is_reset` tells whether a window has reset since.

    Returns ``None`` when the file is missing, cannot be read, is not a JSON object, or has a format version other than
    1. A window with an invalid value is left out, and the others are still returned.
    """
    base = config_dir.config_dir if not isinstance(config_dir, str | os.PathLike) else Path(config_dir)
    path = base / RATE_LIMITS_FILE
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != _FORMAT_VERSION:
        return None
    written_at = _ms_time(data.get("written_at"))
    if written_at is None:
        return None
    windows: dict[str, RateLimitWindow] = {}
    raw_windows = data.get("windows")
    if isinstance(raw_windows, dict):
        for name, raw in raw_windows.items():
            if isinstance(name, str) and (window := _window(name, raw)) is not None:
                windows[name] = window
    return RateLimits(
        path=path,
        profile_name=_str(data, "profile"),
        written_at=written_at,
        measured_at=max((w.measured_at for w in windows.values()), default=None),
        session_id=_str(data, "session_id"),
        model=_str(data, "model"),
        claude_version=_str(data, "claude_version"),
        windows=MappingProxyType(windows),
        raw=MappingProxyType(data),
    )
