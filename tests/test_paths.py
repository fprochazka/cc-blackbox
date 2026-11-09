from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_code_blackbox import claude_tmp_root, project_slug, scratchpad_dir
from claude_code_blackbox.paths import default_config_dir, env_config_dir, node_tmpdir

# The expected slugs below were produced by running Claude Code's own slug function under Node.


def test_slug_replaces_every_non_alphanumeric_character() -> None:
    assert project_slug("/home/user/projects/my_app.v2") == "-home-user-projects-my-app-v2"


def test_slug_of_a_typical_checkout_path() -> None:
    cwd = "/home/user/devel/libs/github.com/example/claude-code-blackbox-sdk"
    assert project_slug(cwd) == "-home-user-devel-libs-github-com-example-claude-code-blackbox-sdk"


def test_slug_counts_astral_characters_as_two_utf16_units() -> None:
    assert project_slug("/home/user/proj ěš 😀/x") == "-home-user-proj-------x"


def test_slug_of_exactly_200_characters_has_no_hash() -> None:
    path = "/" + "a" * 199
    assert project_slug(path) == "-" + "a" * 199


@pytest.mark.parametrize(
    ("path", "tail"),
    [
        # a negative hash: its absolute value is encoded
        ("/home/user/projects/" + "deeply-nested-directory/" * 9 + "example", "neste-l5u63w"),
        ("/x/" + "a" * 250, "aaaaa-bjrb96"),
        # a positive hash, computed over the original path including its surrogate pair
        ("/home/user/😀/" + "b" * 220, "bbbbb-g8dj61"),
    ],
)
def test_long_slug_is_cut_to_200_and_gets_java_hash_suffix(path: str, tail: str) -> None:
    slug = project_slug(path)
    assert slug.endswith(tail)
    assert slug[200] == "-"
    assert len(slug) == 201 + len(tail.split("-")[1])


def test_slug_accepts_path_objects() -> None:
    assert project_slug(Path("/srv/app")) == "-srv-app"


def test_scratchpad_dir_layout(tmp_path: Path) -> None:
    result = scratchpad_dir("/home/user/projects/example", "0b7f-session", tmpdir=tmp_path, uid=1234)
    assert result == tmp_path.resolve() / "claude-1234" / "-home-user-projects-example" / "0b7f-session" / "scratchpad"


def test_tmp_root_resolves_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "claude-7").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert claude_tmp_root(tmpdir=link, uid=7) == real.resolve() / "claude-7"


def test_tmp_root_prefers_claude_code_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "cc"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "other"))
    assert claude_tmp_root(uid=5) == tmp_path.resolve() / "cc" / "claude-5"


def test_tmp_root_falls_back_to_node_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path) + "/")
    assert claude_tmp_root(uid=5) == tmp_path.resolve() / "claude-5"


@pytest.mark.skipif(os.name == "nt", reason="POSIX lookup order")
def test_node_tmpdir_lookup_order() -> None:
    assert node_tmpdir({"TMP": "/b", "TEMP": "/c"}) == "/b"
    assert node_tmpdir({"TEMP": "/c"}) == "/c"
    assert node_tmpdir({}) == "/tmp"
    assert node_tmpdir({"TMPDIR": "/a/"}) == "/a"


def test_config_dir_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert default_config_dir() == tmp_path / ".claude"
    assert env_config_dir() == tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-profiles/work")
    assert env_config_dir() == tmp_path / ".claude-profiles" / "work"
