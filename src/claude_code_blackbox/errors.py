"""Exceptions raised by the SDK. Every one of them derives from :class:`ClaudeBlackboxError`."""

from __future__ import annotations

__all__ = [
    "AuthSwitchConfigError",
    "ClaudeBlackboxError",
    "ProfileNotFound",
]


class ClaudeBlackboxError(Exception):
    """Base class for every error the SDK raises."""


class ProfileNotFound(ClaudeBlackboxError, LookupError):
    """No profile has the requested name."""


class AuthSwitchConfigError(ClaudeBlackboxError):
    """The auth-switch config file exists but cannot be read as a YAML mapping."""
