"""Typed access to a running Claude Code: profiles, active sessions and cross-session messaging.

Everything importable from this package is the public API. Submodules are implementation details.
"""

from claude_code_blackbox.paths import claude_tmp_root, project_slug, scratchpad_dir

__all__ = [
    "claude_tmp_root",
    "project_slug",
    "scratchpad_dir",
]

__version__ = "0.1.0"
