"""Typed access to a running Claude Code: profiles, active sessions and cross-session messaging.

Everything importable from this package is the public API. Submodules are implementation details.
"""

from claude_code_blackbox.errors import AuthSwitchConfigError, ClaudeBlackboxError, ProfileNotFound
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

__all__ = [
    "AuthSwitchConfig",
    "AuthSwitchConfigError",
    "AuthSwitchProfile",
    "ClaudeBlackboxError",
    "ClaudeProfile",
    "ProfileNotFound",
    "ProfileSource",
    "claude_tmp_root",
    "current_profile",
    "get_profile",
    "list_profiles",
    "project_slug",
    "read_auth_switch_config",
    "scratchpad_dir",
]

__version__ = "0.1.0"
