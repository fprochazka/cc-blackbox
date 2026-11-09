from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from cc_blackbox import (
    AuthSwitchConfigError,
    ClaudeProfile,
    ProfileNotFound,
    current_profile,
    get_profile,
    list_profiles,
    read_auth_switch_config,
)

AUTH_SWITCH_YAML = """\
source_dir: ~/.claude
bin_dir: ~/.local/bin

profiles:
  personal:
    description: "Personal account"
    model: "some-model"

  work:
    description: "Work subscription"
    add_dirs:
      - ~/projects/work
    attribution:
      commit: "Generated"
    env:
      SOME_FLAG: "1"

  never-installed:
    description: "Listed but its dir was never created"

  bare:
"""


def make_config_dir(path: Path, marker: str = "sessions") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if marker == "sessions":
        (path / "sessions").mkdir(exist_ok=True)
    else:
        (path / marker).write_text("{}", encoding="utf-8")
    return path


def summary(profiles: list[ClaudeProfile]) -> list[tuple[str, str, str]]:
    return [(p.name, p.source, str(p.config_dir)) for p in profiles]


def hash_name(path: Path) -> str:
    return "cfg-" + hashlib.sha256(os.fsencode(path)).hexdigest()[:8]


def test_no_profiles_on_an_empty_machine() -> None:
    assert list_profiles() == []
    assert read_auth_switch_config() is None
    assert current_profile() is None


def test_default_location_lists_installed_profiles_and_orphans(home: Path) -> None:
    make_config_dir(home / ".claude")
    (home / ".claude" / "auth-switch.yaml").write_text(AUTH_SWITCH_YAML, encoding="utf-8")
    profiles_dir = home / ".claude-profiles"
    make_config_dir(profiles_dir / "personal", ".claude.json")
    make_config_dir(profiles_dir / "work")
    make_config_dir(profiles_dir / "bare", "settings.json")
    make_config_dir(profiles_dir / "old-experiment")  # removed from the yaml, dir stayed behind
    (profiles_dir / "not-a-profile").mkdir()  # no marker inside
    (profiles_dir / "stray-file.txt").write_text("x", encoding="utf-8")

    assert summary(list_profiles()) == [
        ("default", "default", str(home / ".claude")),
        ("personal", "auth-switch", str(profiles_dir / "personal")),
        ("work", "auth-switch", str(profiles_dir / "work")),
        ("bare", "auth-switch", str(profiles_dir / "bare")),
        ("old-experiment", "auth-switch-orphan", str(profiles_dir / "old-experiment")),
    ]
    work = get_profile("work")
    assert work.description == "Work subscription"
    assert work.auth_switch_name == "work"
    assert get_profile("bare").description is None
    assert get_profile("old-experiment").auth_switch_name is None


def test_legacy_location_is_read_when_the_default_one_is_missing(home: Path) -> None:
    profiles_dir = home / ".claude-profiles"
    profiles_dir.mkdir()
    (profiles_dir / "config.yaml").write_text(AUTH_SWITCH_YAML, encoding="utf-8")
    make_config_dir(profiles_dir / "personal")

    config = read_auth_switch_config()
    assert config is not None
    assert config.path == profiles_dir / "config.yaml"
    assert summary(list_profiles()) == [("personal", "auth-switch", str(profiles_dir / "personal"))]


def test_default_location_wins_over_legacy(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  new: {}\n", encoding="utf-8")
    (home / ".claude-profiles").mkdir()
    (home / ".claude-profiles" / "config.yaml").write_text("profiles:\n  old: {}\n", encoding="utf-8")

    config = read_auth_switch_config()
    assert config is not None
    assert list(config.profiles) == ["new"]


def test_custom_profiles_dir(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text(
        "profiles_dir: ~/cc/profiles\nprofiles:\n  personal:\n    description: Mine\n", encoding="utf-8"
    )
    make_config_dir(home / "cc" / "profiles" / "personal")
    make_config_dir(home / "cc" / "profiles" / "leftover")
    make_config_dir(home / ".claude-profiles" / "ignored")  # the default profiles dir is not scanned

    config = read_auth_switch_config()
    assert config is not None
    assert config.profiles_dir == home / "cc" / "profiles"
    assert config.profiles["personal"].config_dir == home / "cc" / "profiles" / "personal"
    assert summary(list_profiles()) == [
        ("default", "default", str(home / ".claude")),
        ("personal", "auth-switch", str(home / "cc" / "profiles" / "personal")),
        ("leftover", "auth-switch-orphan", str(home / "cc" / "profiles" / "leftover")),
    ]


def test_explicit_config_path(home: Path) -> None:
    path = home / "elsewhere.yaml"
    path.write_text("source_dir: /opt/claude\nprofiles:\n  a: {description: 7}\n  ../evil: {}\n", encoding="utf-8")
    config = read_auth_switch_config(path)
    assert config is not None
    assert config.source_dir == Path("/opt/claude")
    assert list(config.profiles) == ["a"]
    assert config.profiles["a"].description is None  # not a string, ignored
    assert read_auth_switch_config(home / "missing.yaml") is None


@pytest.mark.parametrize("content", ["profiles: [unclosed", "- just\n- a list\n"])
def test_unreadable_config_raises(home: Path, content: str) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(AuthSwitchConfigError):
        list_profiles()


def test_config_without_profiles_key_is_tolerated(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text("", encoding="utf-8")
    make_config_dir(home / ".claude-profiles" / "personal")
    assert summary(list_profiles()) == [
        ("default", "default", str(home / ".claude")),
        ("personal", "auth-switch-orphan", str(home / ".claude-profiles" / "personal")),
    ]


def test_env_config_dir_is_added_once(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_config_dir(home / ".claude")
    make_config_dir(home / "custom" / "my-cc")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "custom" / "my-cc"))
    assert summary(list_profiles()) == [
        ("default", "default", str(home / ".claude")),
        ("my-cc", "env", str(home / "custom" / "my-cc")),
    ]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    assert summary(list_profiles()) == [("default", "default", str(home / ".claude"))]


def test_duplicates_are_removed_by_realpath(home: Path) -> None:
    make_config_dir(home / ".claude")
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  alias: {}\n", encoding="utf-8")
    (home / ".claude-profiles").mkdir()
    (home / ".claude-profiles" / "alias").symlink_to(home / ".claude")
    assert summary(list_profiles()) == [("default", "default", str(home / ".claude"))]


def test_clashing_basenames_get_hash_names(home: Path) -> None:
    a = make_config_dir(home / "a" / "cc")
    b = make_config_dir(home / "b" / "cc")
    unique = make_config_dir(home / "c" / "unique")
    reserved = make_config_dir(home / "d" / "default")
    profiles = list_profiles([a, b, unique, reserved, home / "a" / "cc"])
    assert summary(profiles) == [
        (hash_name(a), "explicit", str(a)),
        (hash_name(b), "explicit", str(b)),
        ("unique", "explicit", str(unique)),
        (hash_name(reserved), "explicit", str(reserved)),
    ]
    assert all(len(p.name) == 12 for p in profiles if p.name.startswith("cfg-"))


def test_env_dir_named_like_an_auth_switch_profile_gets_hash_name(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text(
        "profiles_dir: ~/p\nprofiles:\n  work: {}\n  other: {}\n", encoding="utf-8"
    )
    make_config_dir(home / "p" / "other")
    make_config_dir(home / "custom" / "work")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "custom" / "work"))
    names = [p.name for p in list_profiles()]
    assert names == ["default", "other", hash_name(home / "custom" / "work")]


def test_explicit_default_dir_keeps_default_name(home: Path) -> None:
    make_config_dir(home / ".claude")
    assert summary(list_profiles([home / ".claude"])) == [("default", "explicit", str(home / ".claude"))]


def test_profile_paths(home: Path) -> None:
    make_config_dir(home / ".claude")
    make_config_dir(home / ".claude-profiles" / "work")
    default, work = list_profiles([home / ".claude", home / ".claude-profiles" / "work"])
    assert default.claude_json == home / ".claude.json"
    assert work.claude_json == home / ".claude-profiles" / "work" / ".claude.json"
    assert work.sessions_dir == home / ".claude-profiles" / "work" / "sessions"
    assert work.projects_dir == home / ".claude-profiles" / "work" / "projects"


def test_get_profile_raises_with_known_names(home: Path) -> None:
    make_config_dir(home / ".claude")
    with pytest.raises(ProfileNotFound, match="Known profiles: default"):
        get_profile("nope")


def test_current_profile(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_config_dir(home / ".claude")
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  work: {}\n", encoding="utf-8")
    make_config_dir(home / ".claude-profiles" / "work")

    current = current_profile()
    assert current is not None
    assert current.name == "default"

    monkeypatch.setenv("FP_CC_AUTH_SWITCH_PROFILE", "work")
    current = current_profile()
    assert current is not None
    assert current.name == "work"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude-profiles" / "work"))
    monkeypatch.setenv("FP_CC_AUTH_SWITCH_PROFILE", "something-else")
    current = current_profile()
    assert current is not None
    assert current.name == "work"

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "missing"))
    assert current_profile() is None
