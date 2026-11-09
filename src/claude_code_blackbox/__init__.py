"""Typed access to a running Claude Code: profiles, active sessions and cross-session messaging.

Everything importable from this package is the public API. Submodules are implementation details.
"""

from claude_code_blackbox.errors import (
    AmbiguousSession,
    AuthSwitchConfigError,
    ClaudeBlackboxError,
    ProfileNotFound,
    SessionNotFound,
)
from claude_code_blackbox.paths import claude_tmp_root, project_slug, scratchpad_dir
from claude_code_blackbox.profiles import (
    AuthSwitchConfig,
    AuthSwitchProfile,
    ClaudeProfile,
    ProfileSource,
    current_profile,
    get_profile,
    list_profiles,
    read_auth_switch_config,
)
from claude_code_blackbox.sessions import (
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
    "ProfileNotFound",
    "ProfileSource",
    "SessionKind",
    "SessionNotFound",
    "SessionStatus",
    "claude_tmp_root",
    "current_profile",
    "current_session",
    "find_session",
    "get_profile",
    "list_active_sessions",
    "list_profiles",
    "project_slug",
    "read_auth_switch_config",
    "scratchpad_dir",
]

__version__ = "0.1.0"
