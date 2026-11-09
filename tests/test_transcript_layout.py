from __future__ import annotations

from pathlib import Path

import pytest
from records import make_profile_dir
from transcript_records import SESSION_ID

from cc_blackbox import (
    ProfileNotFound,
    is_session_file_name,
    is_session_id,
    list_profiles,
    list_projects_dirs,
    session_id_for_path,
)


def test_only_a_canonical_uuid_names_a_session_file() -> None:
    assert is_session_file_name(f"{SESSION_ID}.jsonl")
    assert not is_session_file_name(f"{SESSION_ID}-ftoc.jsonl")
    assert not is_session_file_name(f".{SESSION_ID}-auto-permissions.json")
    assert not is_session_file_name(f"{SESSION_ID}.json")
    assert not is_session_file_name("agent-a1b2c3d4e5f6a7b8.jsonl")
    assert is_session_id(SESSION_ID)
    assert not is_session_id(f"../{SESSION_ID}")


def test_session_id_for_path_folds_sidecars_into_their_session(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "-home-user-projects-example"
    assert session_id_for_path(project / f"{SESSION_ID}.jsonl", projects) == SESSION_ID
    assert session_id_for_path(project / SESSION_ID / "subagents" / "agent-a1.jsonl", projects) == SESSION_ID
    assert session_id_for_path(project / SESSION_ID / "subagents" / "agent-a1.meta.json", projects) == SESSION_ID
    assert session_id_for_path(project / SESSION_ID / "tool-results" / "b1.txt", projects) == SESSION_ID


def test_session_id_for_path_ignores_files_of_other_tools(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "-home-user-projects-example"
    assert session_id_for_path(project / f"{SESSION_ID}-ftoc.jsonl", projects) is None
    assert session_id_for_path(project / "memory" / "MEMORY.md", projects) is None
    assert session_id_for_path(project, projects) is None
    assert session_id_for_path(tmp_path / "elsewhere" / "x" / f"{SESSION_ID}.jsonl", projects) is None


def test_profiles_that_share_a_projects_dir_share_one_entry(home: Path) -> None:
    make_profile_dir(home / ".claude")
    (home / ".claude" / "projects").mkdir()
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  personal: {}\n  work: {}\n  lab: {}\n", "utf-8")
    for name in ("personal", "work"):
        make_profile_dir(home / ".claude-profiles" / name)
        (home / ".claude-profiles" / name / "projects").symlink_to(home / ".claude" / "projects")
    make_profile_dir(home / ".claude-profiles" / "lab")
    (home / ".claude-profiles" / "lab" / "projects").mkdir()

    dirs = list_projects_dirs()

    assert [d.path for d in dirs] == [home / ".claude" / "projects", home / ".claude-profiles" / "lab" / "projects"]
    assert [p.name for p in dirs[0].profiles] == ["default", "personal", "work"]
    assert [p.name for p in dirs[1].profiles] == ["lab"]


def test_a_profile_without_projects_is_left_out(home: Path) -> None:
    make_profile_dir(home / ".claude")
    assert list_projects_dirs() == []
    assert list_projects_dirs(list_profiles()) == []


def test_explicit_paths_have_no_profiles(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    dirs = list_projects_dirs(paths=[tmp_path / "a", tmp_path / "a", tmp_path / "missing"])
    assert len(dirs) == 1
    assert dirs[0].path == (tmp_path / "a").resolve()
    assert dirs[0].profiles == ()


def test_an_unknown_profile_name_raises(home: Path) -> None:
    make_profile_dir(home / ".claude")
    with pytest.raises(ProfileNotFound):
        list_projects_dirs(["nope"])
