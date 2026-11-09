"""Paths that Claude Code derives from its environment.

Each function here reimplements one path rule of Claude Code 2.1.x. The rules are not documented by Anthropic; they
were read from the Claude Code bundle, so a future release can change them.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

__all__ = [
    "claude_tmp_root",
    "default_config_dir",
    "env_config_dir",
    "node_tmpdir",
    "project_slug",
    "scratchpad_dir",
    "untruncated_slug",
]

_SLUG_MAX_LENGTH = 200
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def default_config_dir() -> Path:
    """Return ``~/.claude``, the config dir Claude Code uses when ``CLAUDE_CONFIG_DIR`` is not set.

    Claude Code has no XDG or ``~/.config`` fallback on any platform.
    """
    return Path.home() / ".claude"


def env_config_dir() -> Path:
    """Return the config dir the current process would use: ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``.

    The value is user-expanded and made absolute, but symlinks are kept.
    """
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    if value:
        return Path(os.path.abspath(os.path.expanduser(value)))
    return default_config_dir()


def _java_string_hash(value: str) -> int:
    # Java String.hashCode over UTF-16 code units, wrapped to a signed 32-bit int like JavaScript's `| 0`.
    utf16 = value.encode("utf-16-le")
    h = 0
    for i in range(0, len(utf16), 2):
        h = (h * 31 + int.from_bytes(utf16[i : i + 2], "little")) & 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = []
    while value:
        value, rem = divmod(value, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


def untruncated_slug(cwd: str | os.PathLike[str]) -> str:
    """Return :func:`project_slug` of ``cwd`` before it is cut to 200 characters.

    A dir under ``cwd`` has a slug that starts with this one, followed by ``-``. That holds for the first 200
    characters of a cut slug too, which is what lets a caller find the project dirs under a directory by name.
    """
    return "".join("-" * (len(ch.encode("utf-16-le")) // 2) if _NON_ALNUM.match(ch) else ch for ch in os.fspath(cwd))


def project_slug(cwd: str | os.PathLike[str]) -> str:
    """Return the directory name Claude Code uses for ``cwd`` under ``projects/`` and in the scratchpad path.

    Every character outside ``[A-Za-z0-9]`` becomes ``-``. A character outside the Basic Multilingual Plane becomes
    two dashes, because JavaScript counts it as two UTF-16 code units. A slug longer than 200 characters is cut to
    200 and gets ``-`` plus the base-36 absolute value of the Java ``String.hashCode`` of the original path.
    """
    path = os.fspath(cwd)
    slug = untruncated_slug(path)
    if len(slug) <= _SLUG_MAX_LENGTH:
        return slug
    return f"{slug[:_SLUG_MAX_LENGTH]}-{_base36(abs(_java_string_hash(path)))}"


def node_tmpdir(environ: Mapping[str, str] | None = None) -> str:
    """Return what Node's ``os.tmpdir()`` gives for ``environ``, which defaults to this process's environment.

    On POSIX that is ``$TMPDIR``, then ``$TMP``, then ``$TEMP``, then ``/tmp``, without a trailing slash. Python's
    ``tempfile.gettempdir()`` differs: it checks ``TEMP`` before ``TMP`` and skips dirs it cannot write to.
    """
    env = os.environ if environ is None else environ
    if sys.platform == "win32":
        path = env.get("TEMP") or env.get("TMP") or os.path.join(env.get("SystemRoot", r"C:\Windows"), "temp")
        if len(path) > 1 and path.endswith("\\") and not path.endswith(":\\"):
            path = path[:-1]
        return path
    path = env.get("TMPDIR") or env.get("TMP") or env.get("TEMP") or "/tmp"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def claude_tmp_root(*, tmpdir: str | os.PathLike[str] | None = None, uid: int | None = None) -> Path:
    """Return Claude Code's per-user temp root, ``realpath(<tmpdir>/claude-<uid>)``.

    ``tmpdir`` defaults to ``$CLAUDE_CODE_TMPDIR`` of this process, else to :func:`node_tmpdir`. Pass it explicitly
    when computing the path for another process, whose environment can differ. ``uid`` defaults to the current uid,
    and to 0 on Windows, where Claude Code also uses 0.
    """
    base = os.fspath(tmpdir) if tmpdir is not None else (os.environ.get("CLAUDE_CODE_TMPDIR") or node_tmpdir())
    if uid is None:
        uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(os.path.realpath(os.path.join(base, f"claude-{uid}")))


def scratchpad_dir(
    cwd: str | os.PathLike[str],
    session_id: str,
    *,
    tmpdir: str | os.PathLike[str] | None = None,
    uid: int | None = None,
) -> Path:
    """Return the scratchpad dir of the session ``session_id`` launched in ``cwd``.

    ``cwd`` must be the session's launch directory, not the current directory of its shell. Claude Code creates the
    directory on first use, so the returned path may not exist yet. ``tmpdir`` and ``uid`` work as in
    :func:`claude_tmp_root`.
    """
    return claude_tmp_root(tmpdir=tmpdir, uid=uid) / project_slug(cwd) / session_id / "scratchpad"
