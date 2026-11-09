"""Exceptions raised by the SDK. Every one of them derives from :class:`ClaudeBlackboxError`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cc_blackbox.sessions.active import ActiveSession

__all__ = [
    "AmbiguousSession",
    "AuthSwitchConfigError",
    "ClaudeBlackboxError",
    "MessageTooLarge",
    "MessagingError",
    "MessagingUnavailable",
    "ProfileNotFound",
    "SessionGone",
    "SessionNotFound",
    "TranscriptNotFound",
    "UnsupportedProtocol",
]


class ClaudeBlackboxError(Exception):
    """Base class for every error the SDK raises."""


class ProfileNotFound(ClaudeBlackboxError, LookupError):
    """No profile has the requested name."""


class AuthSwitchConfigError(ClaudeBlackboxError):
    """The auth-switch config file exists but cannot be read as a YAML mapping."""


class SessionNotFound(ClaudeBlackboxError, LookupError):
    """No active session matches the query."""


class TranscriptNotFound(ClaudeBlackboxError, LookupError):
    """No transcript matches the session id or path."""


class AmbiguousSession(ClaudeBlackboxError, LookupError):
    """More than one active session matches the query. ``candidates`` holds all of them."""

    def __init__(self, message: str, candidates: Sequence[ActiveSession]) -> None:
        super().__init__(message)
        self.candidates: tuple[ActiveSession, ...] = tuple(candidates)


class MessagingError(ClaudeBlackboxError):
    """A message or control frame could not be written to a session."""


class MessagingUnavailable(MessagingError):
    """The session cannot receive messages: its record names no socket, or the platform is not supported."""


class SessionGone(MessagingError):
    """Nobody listens on the session's socket, so the session has most likely ended."""


class MessageTooLarge(MessagingError, ValueError):
    """The frame exceeds the receiver's 1 MiB line limit. Nothing was sent."""


class UnsupportedProtocol(MessagingError):
    """The session speaks a peer protocol version this SDK does not know."""
