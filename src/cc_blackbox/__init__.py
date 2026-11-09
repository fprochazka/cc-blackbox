"""Typed access to a running Claude Code (cc): profiles, active sessions and cross-session messaging.

Unofficial and not affiliated with or endorsed by Anthropic. The package relies on undocumented Claude Code internals
that can change in any Claude Code release.

Everything importable from this package is the public API. Submodules are implementation details.
"""

from cc_blackbox.errors import (
    AmbiguousSession,
    AuthSwitchConfigError,
    ClaudeBlackboxError,
    MessageTooLarge,
    MessagingError,
    MessagingUnavailable,
    ProfileNotFound,
    SessionGone,
    SessionNotFound,
    UnsupportedProtocol,
)
from cc_blackbox.messaging import (
    MessagingTarget,
    Priority,
    RateLimitedSender,
    SentMessage,
    build_envelope,
    percent_encode_address,
    rename_session,
    send_message,
)
from cc_blackbox.paths import claude_tmp_root, project_slug, scratchpad_dir
from cc_blackbox.profiles import (
    AuthSwitchConfig,
    AuthSwitchProfile,
    ClaudeProfile,
    ProfileSource,
    current_profile,
    get_profile,
    list_profiles,
    read_auth_switch_config,
)
from cc_blackbox.sessions import (
    ActiveSession,
    SessionKind,
    SessionStatus,
    current_session,
    find_session,
    list_active_sessions,
)

__all__ = [
    "ActiveSession",
    "AmbiguousSession",
    "AuthSwitchConfig",
    "AuthSwitchConfigError",
    "AuthSwitchProfile",
    "ClaudeBlackboxError",
    "ClaudeProfile",
    "MessageTooLarge",
    "MessagingError",
    "MessagingTarget",
    "MessagingUnavailable",
    "Priority",
    "ProfileNotFound",
    "ProfileSource",
    "RateLimitedSender",
    "SentMessage",
    "SessionGone",
    "SessionKind",
    "SessionNotFound",
    "SessionStatus",
    "UnsupportedProtocol",
    "build_envelope",
    "claude_tmp_root",
    "current_profile",
    "current_session",
    "find_session",
    "get_profile",
    "list_active_sessions",
    "list_profiles",
    "percent_encode_address",
    "project_slug",
    "read_auth_switch_config",
    "rename_session",
    "scratchpad_dir",
    "send_message",
]

__version__ = "0.1.0"
