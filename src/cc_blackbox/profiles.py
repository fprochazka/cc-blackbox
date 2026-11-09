"""Claude Code profiles: the config dirs that sessions run under.

Claude Code itself has no notion of a profile. It reads ``$CLAUDE_CONFIG_DIR``, or ``~/.claude`` when that is unset, so
a profile here is any directory used as a config dir. The SDK finds them in ``~/.claude``, in the config of the
claude-code-auth-switch tool, under that tool's profiles dir, and in ``$CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml

from cc_blackbox.errors import AuthSwitchConfigError, ProfileNotFound
from cc_blackbox.paths import default_config_dir

__all__ = [
    "AuthSwitchConfig",
    "AuthSwitchProfile",
    "ClaudeProfile",
    "ProfileSource",
    "current_profile",
    "get_profile",
    "list_profiles",
    "read_auth_switch_config",
]

ProfileSource = Literal["default", "auth-switch", "auth-switch-orphan", "env", "explicit"]
"""Where :func:`list_profiles` found a profile.

``auth-switch-orphan`` is a dir under the auth-switch profiles dir that the auth-switch config no longer lists. It can
still hold sessions and transcripts.
"""

DEFAULT_PROFILE_NAME = "default"
_MARKERS = ("sessions", ".claude.json", "settings.json")


@dataclass(frozen=True, slots=True)
class ClaudeProfile:
    """One Claude Code config dir.

    ``name`` is unique within one :func:`list_profiles` result and stable across runs, so it can be used as an address.
    """

    name: str
    config_dir: Path
    """Absolute and symlink-resolved, so profiles reached through different symlinks compare equal.

    Claude Code uses ``$CLAUDE_CONFIG_DIR`` as given, without resolving symlinks. Passing this path or a symlink to it
    makes Claude Code read and write the same files, and the project slugs do not change, because Claude Code derives
    them from the session's cwd alone. Only the paths Claude Code reports start differently, such as the
    ``transcript_path`` a hook receives. Setting ``CLAUDE_CONFIG_DIR`` to the ``default`` profile's dir is not the same
    as leaving it unset, see :attr:`claude_json`.
    """
    source: ProfileSource
    description: str | None = None
    """The description from the auth-switch config, if any."""
    auth_switch_name: str | None = None
    """The profile key in the auth-switch config, if the profile is listed there."""

    @property
    def sessions_dir(self) -> Path:
        """The dir that holds the ``<pid>.json`` records of the profile's running sessions."""
        return self.config_dir / "sessions"

    @property
    def projects_dir(self) -> Path:
        """The dir that holds the profile's transcripts, one subdir per project slug."""
        return self.config_dir / "projects"

    @property
    def claude_json(self) -> Path:
        """The profile's global ``.claude.json``.

        For ``~/.claude`` this is ``~/.claude.json``, next to the dir, because Claude Code puts it there when
        ``CLAUDE_CONFIG_DIR`` is unset. For every other dir it is inside the dir. A Claude Code started with
        ``CLAUDE_CONFIG_DIR=~/.claude`` reads ``~/.claude/.claude.json`` instead, so it does not see the default
        profile's login and settings from ``~/.claude.json``.
        """
        if self.config_dir == _realpath(default_config_dir()):
            return Path.home() / ".claude.json"
        return self.config_dir / ".claude.json"


@dataclass(frozen=True, slots=True)
class AuthSwitchProfile:
    """One entry of ``profiles`` in the auth-switch config."""

    name: str
    config_dir: Path
    """``<profiles_dir>/<name>``, user-expanded but not symlink-resolved, exactly as auth-switch builds it."""
    description: str | None = None


@dataclass(frozen=True, slots=True)
class AuthSwitchConfig:
    """The parts of the claude-code-auth-switch config that locate profiles. Every other key is ignored."""

    path: Path
    source_dir: Path
    profiles_dir: Path
    profiles: Mapping[str, AuthSwitchProfile] = field(default_factory=lambda: MappingProxyType({}))


def _realpath(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def _expand(value: object, default: str) -> Path:
    return Path(os.path.expanduser(value if isinstance(value, str) and value else default))


def read_auth_switch_config(path: str | os.PathLike[str] | None = None) -> AuthSwitchConfig | None:
    """Read the claude-code-auth-switch config and return the profile locations it defines.

    Without ``path`` the function reads ``~/.claude/auth-switch.yaml``, and falls back to the legacy
    ``~/.claude-profiles/config.yaml``, the same order auth-switch uses. It returns ``None`` when no config file exists.
    Only ``source_dir``, ``profiles_dir`` and ``profiles.<name>.description`` are read; a malformed entry is skipped,
    so new auth-switch keys don't break the reader.

    Raises:
        AuthSwitchConfigError: the file exists but is not valid YAML or its top level is not a mapping.
    """
    if path is None:
        home = Path.home()
        candidates = [home / ".claude" / "auth-switch.yaml", home / ".claude-profiles" / "config.yaml"]
        config_path = next((c for c in candidates if c.is_file()), None)
        if config_path is None:
            return None
    else:
        config_path = Path(os.path.expanduser(os.fspath(path)))
        if not config_path.is_file():
            return None

    try:
        data: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise AuthSwitchConfigError(f"Cannot read the auth-switch config {config_path}: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AuthSwitchConfigError(f"The auth-switch config {config_path} must be a YAML mapping at the top level.")

    profiles_dir = _expand(data.get("profiles_dir"), "~/.claude-profiles")
    profiles: dict[str, AuthSwitchProfile] = {}
    raw_profiles = data.get("profiles")
    if isinstance(raw_profiles, dict):
        for name, entry in raw_profiles.items():
            if not isinstance(name, str) or not name or "/" in name or name in (".", ".."):
                continue
            description = entry.get("description") if isinstance(entry, dict) else None
            profiles[name] = AuthSwitchProfile(
                name=name,
                config_dir=profiles_dir / name,
                description=description if isinstance(description, str) else None,
            )

    return AuthSwitchConfig(
        path=config_path,
        source_dir=_expand(data.get("source_dir"), "~/.claude"),
        profiles_dir=profiles_dir,
        profiles=MappingProxyType(profiles),
    )


def _looks_like_config_dir(path: Path) -> bool:
    return any((path / marker).exists() for marker in _MARKERS)


@dataclass(slots=True)
class _Candidate:
    config_dir: Path
    source: ProfileSource
    name: str | None = None
    description: str | None = None
    auth_switch_name: str | None = None


def _hash_name(config_dir: Path) -> str:
    return "cfg-" + hashlib.sha256(os.fsencode(config_dir)).hexdigest()[:8]


def _assign_names(candidates: list[_Candidate], reserved_names: Iterable[str] = ()) -> list[ClaudeProfile]:
    # Every auth-switch key stays reserved, installed or not, so a dir's name doesn't change once the profile is.
    reserved = {DEFAULT_PROFILE_NAME, *reserved_names} | {c.name for c in candidates if c.name is not None}
    basenames = Counter(c.config_dir.name for c in candidates if c.name is None)
    profiles: list[ClaudeProfile] = []
    for c in candidates:
        name = c.name
        if name is None:
            base = c.config_dir.name
            name = base if base and basenames[base] == 1 and base not in reserved else _hash_name(c.config_dir)
        profiles.append(ClaudeProfile(name, c.config_dir, c.source, c.description, c.auth_switch_name))
    return profiles


def list_profiles(config_dirs: Iterable[str | os.PathLike[str]] | None = None) -> list[ClaudeProfile]:
    """List the Claude Code profiles on this machine.

    With ``config_dirs``, only those dirs are listed, all with source ``explicit``, whether they exist or not.
    Otherwise the function looks in this order, and the first source to claim a dir (compared by realpath) wins:

    1. ``~/.claude``, named ``default``, if it exists.
    2. Each profile in the auth-switch config (see :func:`read_auth_switch_config`) whose dir exists, named by its key.
    3. Every other dir directly under the auth-switch profiles dir that has ``sessions/``, ``.claude.json`` or
       ``settings.json`` in it.
    4. ``$CLAUDE_CONFIG_DIR``, if it is set and exists.

    ``~/.claude`` is always named ``default`` and an auth-switch profile keeps its key. Any other dir is named by its
    basename when that is unique and is neither ``default`` nor an auth-switch key, and
    ``cfg-<8 hex chars of sha256(realpath)>`` otherwise.

    Raises:
        AuthSwitchConfigError: the auth-switch config exists but cannot be parsed.
    """
    default_dir = _realpath(default_config_dir())
    candidates: list[_Candidate] = []
    seen: set[Path] = set()

    def claim(candidate: _Candidate) -> None:
        if candidate.config_dir not in seen:
            seen.add(candidate.config_dir)
            candidates.append(candidate)

    if config_dirs is not None:
        for raw in config_dirs:
            real = _realpath(raw)
            claim(_Candidate(real, "explicit", DEFAULT_PROFILE_NAME if real == default_dir else None))
        return _assign_names(candidates)

    if default_dir.is_dir():
        claim(_Candidate(default_dir, "default", DEFAULT_PROFILE_NAME))

    config = read_auth_switch_config()
    if config is not None:
        for entry in config.profiles.values():
            real = _realpath(entry.config_dir)
            if real.is_dir() and entry.name != DEFAULT_PROFILE_NAME:
                claim(_Candidate(real, "auth-switch", entry.name, entry.description, entry.name))
        try:
            children = sorted(config.profiles_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and _looks_like_config_dir(child):
                claim(_Candidate(_realpath(child), "auth-switch-orphan"))

    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_dir:
        real = _realpath(env_dir)
        if real.is_dir():
            claim(_Candidate(real, "env"))

    return _assign_names(candidates, config.profiles if config is not None else ())


def resolve_profiles(profiles: Iterable[str | ClaudeProfile] | None) -> list[ClaudeProfile]:
    """Return ``profiles`` with each name looked up, or every profile from :func:`list_profiles` when it is ``None``.

    Raises:
        ProfileNotFound: a string entry names no known profile.
    """
    if profiles is None:
        return list_profiles()
    known: list[ClaudeProfile] | None = None
    selected: list[ClaudeProfile] = []
    for entry in profiles:
        if isinstance(entry, str):
            known = list_profiles() if known is None else known
            selected.append(get_profile(entry, profiles=known))
        else:
            selected.append(entry)
    return selected


def get_profile(name: str, *, profiles: Sequence[ClaudeProfile] | None = None) -> ClaudeProfile:
    """Return the profile called ``name`` from ``profiles``, which defaults to :func:`list_profiles`.

    Raises:
        ProfileNotFound: no profile has that name.
    """
    candidates = list_profiles() if profiles is None else profiles
    for profile in candidates:
        if profile.name == name:
            return profile
    known = ", ".join(p.name for p in candidates) or "none"
    raise ProfileNotFound(f"No Claude Code profile is called {name!r}. Known profiles: {known}.")


def current_profile(*, profiles: Sequence[ClaudeProfile] | None = None) -> ClaudeProfile | None:
    """Return the profile this process runs under, or ``None`` if its config dir is not among ``profiles``.

    The config dir is ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``. When ``CLAUDE_CONFIG_DIR`` is not set but
    ``FP_CC_AUTH_SWITCH_PROFILE`` is, that auth-switch profile name is tried first.
    """
    candidates = list_profiles() if profiles is None else profiles
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not env_dir:
        hint = os.environ.get("FP_CC_AUTH_SWITCH_PROFILE")
        for profile in candidates:
            if hint and profile.auth_switch_name == hint:
                return profile
    target = _realpath(env_dir) if env_dir else _realpath(default_config_dir())
    return next((p for p in candidates if p.config_dir == target), None)
