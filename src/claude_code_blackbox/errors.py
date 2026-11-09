"""Exceptions raised by the SDK. Every one of them derives from :class:`ClaudeBlackboxError`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_code_blackbox.sessions.active import ActiveSession

__all__ = [
    "AmbiguousSession",
    "AuthSwitchConfigError",
    "ClaudeBlackboxError",
    "ProfileNotFound",
    "SessionNotFound",
]


class ClaudeBlackboxError(Exception):
    """Base class for every error the SDK raises."""


class ProfileNotFound(ClaudeBlackboxError, LookupError):
    """No profile has the requested name."""


class AuthSwitchConfigError(ClaudeBlackboxError):
    """The auth-switch config file exists but cannot be read as a YAML mapping."""


class SessionNotFound(ClaudeBlackboxError, LookupError):
    """No active session matches the query."""


class AmbiguousSession(ClaudeBlackboxError, LookupError):
    """More than one active session matches the query. ``candidates`` holds all of them."""

    def __init__(self, message: str, candidates: Sequence[ActiveSession]) -> None:
        super().__init__(message)
        self.candidates: tuple[ActiveSession, ...] = tuple(candidates)
