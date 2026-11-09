from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from records import live_record, make_profile_dir, write_record
from transcript_records import (
    CWD,
    OTHER_SESSION_ID,
    SESSION_ID,
    assistant,
    bookkeeping,
    conversation,
    text,
    user,
    write_subagent,
    write_tool_result_file,
    write_transcript,
)

from cc_blackbox import (
    ActiveSession,
    TranscriptNotFound,
    UserEntry,
    active_session_for,
    find_session_transcript,
    list_session_transcripts,
    read_session_transcript,
    transcript_for,
)
from cc_blackbox.sessions.active import list_active_sessions


@pytest.fixture
def projects(home: Path) -> Path:
    make_profile_dir(home / ".claude")
    path = home / ".claude" / "projects"
    path.mkdir()
    return path


def session_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def test_a_listing_reads_metadata_from_the_transcript(projects: Path) -> None:
    write_transcript(projects, conversation(title="Example walkthrough"))

    (transcript,) = list_session_transcripts()

    assert transcript.session_id == SESSION_ID
    assert transcript.cwd == Path(CWD)
    assert transcript.git_branch == "main"
    assert transcript.version == "2.1.319"
    assert transcript.entrypoint == "cli"
    assert transcript.started_at == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert transcript.last_activity_at == datetime(2026, 11, 1, 9, 1, tzinfo=UTC)
    assert transcript.title == "Example walkthrough"
    assert transcript.title_source == "ai"
    assert transcript.first_prompt == "Explain the example module."
    assert [p.name for p in transcript.projects_dir.profiles] == ["default"]
    assert os.fspath(transcript) == str(transcript.path)


def test_files_of_other_tools_are_not_sessions(projects: Path) -> None:
    main = write_transcript(projects, conversation())
    (main.parent / f"{SESSION_ID}-ftoc.jsonl").write_text('{"type":"read"}\n', encoding="utf-8")
    (main.parent / "memory").mkdir()
    assert [t.session_id for t in list_session_transcripts()] == [SESSION_ID]


def test_the_newest_custom_title_wins_over_generated_titles(projects: Path) -> None:
    records = [
        *conversation(title="First generated title"),
        bookkeeping("custom-title", customTitle="set by hand"),
        bookkeeping("ai-title", aiTitle="Second generated title"),
        bookkeeping("custom-title", customTitle="renamed by hand"),
    ]
    write_transcript(projects, records)
    (transcript,) = list_session_transcripts()
    assert (transcript.title, transcript.title_source) == ("renamed by hand", "custom")


def test_a_session_without_titles_has_only_its_first_prose_prompt(projects: Path) -> None:
    write_transcript(
        projects,
        [
            user("<command-name>/clear</command-name>"),
            user("Base directory for this skill: /opt/skills/x", origin=None, isMeta=True),
            user("Rename the helper.", minutes=1),
        ],
    )
    (transcript,) = list_session_transcripts()
    assert transcript.title is None and transcript.title_source is None
    assert transcript.first_prompt == "Rename the helper."


def test_the_newest_activity_comes_first_and_stubs_last(projects: Path) -> None:
    write_transcript(projects, [user("old", minutes=0)], session_id=session_id(1))
    write_transcript(projects, [user("new", minutes=60)], session_id=session_id(2))
    stub = write_transcript(
        projects, [bookkeeping("ai-title", aiTitle="stub", session_id=session_id(3))], session_id=session_id(3)
    )
    os.utime(stub, (2_000_000_000, 2_000_000_000))

    listed = list_session_transcripts()

    assert [t.session_id for t in listed] == [session_id(2), session_id(1), session_id(3)]
    assert listed[2].last_activity_at is None


def test_cwd_within_keeps_the_repo_its_subdirs_and_worktrees(projects: Path) -> None:
    root = "/home/user/projects/example"
    cases = {
        1: root,
        2: f"{root}/.worktrees/feature-x",
        3: f"{root}/src",
        4: f"{root}-extra",
        5: "/home/user/other",
        6: f"{root}-worktrees/legacy-y",
    }
    for n, cwd in cases.items():
        write_transcript(projects, [user("hi", minutes=n, cwd=cwd)], session_id=session_id(n), cwd=cwd)

    scoped = list_session_transcripts(cwd_within=[root])
    with_legacy = list_session_transcripts(cwd_within=[root, f"{root}-worktrees"])

    assert sorted(t.session_id for t in scoped) == [session_id(1), session_id(2), session_id(3)]
    assert sorted(t.session_id for t in with_legacy) == [session_id(n) for n in (1, 2, 3, 6)]


def test_cwd_within_finds_dirs_whose_slug_was_cut_to_200_characters(projects: Path) -> None:
    root = "/home/user/" + "x" * 230
    cwd = root + "/sub_dir"
    path = write_transcript(projects, [user("hi", cwd=cwd)], cwd=cwd)
    assert len(path.parent.name) > 200
    assert [t.cwd for t in list_session_transcripts(cwd_within=[root])] == [Path(cwd)]


def test_a_transcript_without_cwd_is_kept_only_in_the_exact_dir(projects: Path) -> None:
    root = "/home/user/projects/example"
    write_transcript(projects, [bookkeeping("ai-title", aiTitle="t")], session_id=session_id(1), cwd=root)
    write_transcript(projects, [bookkeeping("ai-title", aiTitle="t")], session_id=session_id(2), cwd=root + "-extra")
    assert [t.session_id for t in list_session_transcripts(cwd_within=[root])] == [session_id(1)]


def test_active_since_skips_old_files(projects: Path) -> None:
    old = write_transcript(projects, [user("a")], session_id=session_id(1))
    write_transcript(projects, [user("b")], session_id=session_id(2))
    os.utime(old, (1_000_000_000, 1_000_000_000))
    since = datetime(2020, 1, 1, tzinfo=UTC)
    assert [t.session_id for t in list_session_transcripts(active_since=since)] == [session_id(2)]


def test_every_profile_is_listed_once_per_projects_dir(home: Path, projects: Path) -> None:
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  work: {}\n  lab: {}\n", encoding="utf-8")
    make_profile_dir(home / ".claude-profiles" / "work")
    (home / ".claude-profiles" / "work" / "projects").symlink_to(projects)
    lab = make_profile_dir(home / ".claude-profiles" / "lab") / "projects"
    lab.mkdir()
    write_transcript(projects, conversation(), session_id=session_id(1))
    write_transcript(lab, conversation(), session_id=session_id(2))

    listed = list_session_transcripts()

    assert sorted(t.session_id for t in listed) == [session_id(1), session_id(2)]
    by_id = {t.session_id: [p.name for p in t.projects_dir.profiles] for t in listed}
    assert by_id == {session_id(1): ["default", "work"], session_id(2): ["lab"]}
    assert [t.session_id for t in list_session_transcripts(["lab"])] == [session_id(2)]


def test_explicit_projects_dirs(tmp_path: Path) -> None:
    write_transcript(tmp_path / "p", conversation())
    (transcript,) = list_session_transcripts(projects_dirs=[tmp_path / "p"])
    assert transcript.projects_dir.profiles == ()


def test_find_and_read_a_transcript(projects: Path) -> None:
    path = write_transcript(projects, conversation(), cwd="/somewhere/else")
    assert find_session_transcript(SESSION_ID).path == path
    with pytest.raises(TranscriptNotFound):
        find_session_transcript(OTHER_SESSION_ID)
    with pytest.raises(TranscriptNotFound):
        find_session_transcript("../../etc/passwd")
    read = read_session_transcript(path)
    assert read.session_id == SESSION_ID
    assert [p.name for p in read.projects_dir.profiles] == ["default"]
    with pytest.raises(TranscriptNotFound):
        read_session_transcript(path.with_name("missing.jsonl"))


def test_subagents_and_sidecar_files(projects: Path) -> None:
    main = write_transcript(projects, conversation())
    child = write_subagent(
        main,
        "a1b2c3d4e5f60718",
        [user("Find the config loader.", isSidechain=True, agentId="a1b2c3d4e5f60718")],
        meta={"agentType": "Explore", "description": "Find loader", "toolUseId": "toolu_7", "spawnDepth": 1},
    )
    nested = write_subagent(
        main,
        "b2c3d4e5f6071829",
        [user("x")],
        meta={"agentType": "Plan", "parentAgentId": "a1b2c3d4e5f60718", "spawnDepth": 2},
    )
    legacy = write_subagent(main, "c3d4e5f607182930", [user("y")])
    spilled = write_tool_result_file(main, "toolu_7.txt", "long output")
    (transcript,) = list_session_transcripts()

    first, second, third = transcript.subagents()

    assert (first.path, first.agent_type, first.description, first.tool_use_id, first.spawn_depth) == (
        child,
        "Explore",
        "Find loader",
        "toolu_7",
        1,
    )
    assert (second.path, second.parent_agent_id, second.spawn_depth) == (nested, "a1b2c3d4e5f60718", 2)
    assert (third.path, third.agent_type, dict(third.meta)) == (legacy, None, {})
    assert first.session_id == SESSION_ID
    (entry,) = first.entries()
    assert isinstance(entry, UserEntry) and entry.is_sidechain and entry.agent_id == "a1b2c3d4e5f60718"
    assert transcript.subagent("a1b2c3d4e5f60718") == first
    assert transcript.subagent("missing") is None
    assert transcript.files() == [main, child, nested, legacy, spilled]


def test_a_transcript_links_to_its_running_session(home: Path, projects: Path) -> None:
    write_transcript(projects, conversation())
    pid = os.getpid()
    write_record(home / ".claude", pid, live_record(pid, sessionId=SESSION_ID))
    (transcript,) = list_session_transcripts()

    active = active_session_for(transcript)

    assert isinstance(active, ActiveSession) and active.pid == pid
    assert transcript_for(active) == transcript
    assert active_session_for(transcript, sessions=[]) is None


def test_a_running_session_without_a_transcript(home: Path, projects: Path) -> None:
    pid = os.getpid()
    write_record(home / ".claude", pid, live_record(pid, sessionId=OTHER_SESSION_ID))
    (active,) = list_active_sessions()
    assert transcript_for(active) is None


def test_an_assistant_reply_does_not_count_as_a_first_prompt(projects: Path) -> None:
    write_transcript(
        projects, [assistant([text("hello")]), user("<task-notification>x</task-notification>", origin=None)]
    )
    (transcript,) = list_session_transcripts()
    assert transcript.first_prompt is None
