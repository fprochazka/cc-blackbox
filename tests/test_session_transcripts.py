from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from records import live_record, make_profile_dir, write_profile_start, write_record
from transcript_records import (
    CWD,
    OTHER_SESSION_ID,
    SESSION_ID,
    assistant,
    attachment,
    bookkeeping,
    conversation,
    envelope,
    jsonl,
    system,
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
    list_profiles,
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
    assert transcript.last_record_at == datetime(2026, 11, 1, 9, 1, tzinfo=UTC)
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
    assert listed[2].last_record_at is None


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


def test_cwd_within_a_symlinked_dir_finds_sessions_under_the_resolved_path(projects: Path, tmp_path: Path) -> None:
    real = (tmp_path / "real" / "example").resolve()
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real.parent)
    write_transcript(projects, [user("hi", cwd=str(real))], cwd=str(real))
    assert [t.cwd for t in list_session_transcripts(cwd_within=[link / "example"])] == [real]


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


def test_a_cwd_hint_checks_its_project_dirs_first_and_falls_back_to_the_scan(projects: Path, tmp_path: Path) -> None:
    real = (tmp_path / "real" / "repo").resolve()
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(real.parent)
    hinted = write_transcript(projects, conversation(), cwd=str(real))
    # The same session id under a project dir that sorts first, as after a resume from another dir.
    elsewhere = write_transcript(projects, conversation(), cwd="/a")

    assert find_session_transcript(SESSION_ID).path == elsewhere
    assert find_session_transcript(SESSION_ID, cwd=tmp_path / "link" / "repo").path == hinted
    assert find_session_transcript(SESSION_ID, cwd="/no/such/dir").path == elsewhere
    with pytest.raises(TranscriptNotFound):
        find_session_transcript(OTHER_SESSION_ID, cwd=real)


def test_a_lookup_scoped_to_one_config_dir_keeps_its_profile(tmp_path: Path) -> None:
    config_dir = make_profile_dir(tmp_path / "unlisted")
    path = write_transcript(config_dir / "projects", conversation())

    found = find_session_transcript(SESSION_ID, list_profiles([config_dir]), cwd=CWD)

    assert found.path == path.resolve()
    assert [p.config_dir for p in found.projects_dir.profiles] == [config_dir.resolve()]


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


def test_profile_starts_and_started_by(home: Path, projects: Path) -> None:
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  work: {}\n  lab: {}\n", encoding="utf-8")
    for name in ("work", "lab"):
        make_profile_dir(home / ".claude-profiles" / name)
        (home / ".claude-profiles" / name / "projects").symlink_to(projects)
    work_dir = str(home / ".claude-profiles" / "work")
    write_transcript(projects, conversation(), session_id=session_id(1))
    write_profile_start(projects, session_id(1), CWD, ts=1_794_100_000_000, pid=10, profile="work", config_dir=work_dir)
    log = write_profile_start(projects, session_id(1), CWD, ts=1_794_100_900_000, pid=11, source="resume")
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"v": 2, "ts": 1}\nnot json\n{"v": 1, "pid": 12}\n{"v": 1, "ts": 1794100000')
    write_transcript(projects, conversation(), session_id=session_id(2))

    transcript = find_session_transcript(session_id(1))
    first, second = transcript.profile_starts()

    assert (first.pid, first.source, first.profile, first.config_dir) == (10, "startup", "work", Path(work_dir))
    assert first.started_at == datetime.fromtimestamp(1_794_100_000, tz=UTC)
    owner = first.find_profile()
    assert owner is not None and owner.name == "work"
    assert (second.pid, second.source, second.profile, second.config_dir) == (11, "resume", None, None)
    owner = second.find_profile()
    assert owner is not None and owner.name == "default"
    assert find_session_transcript(session_id(2)).profile_starts() == []

    assert [t.session_id for t in list_session_transcripts(started_by=["work"])] == [session_id(1)]
    assert [t.session_id for t in list_session_transcripts(started_by=["default"])] == [session_id(1)]
    assert list_session_transcripts(started_by=["lab"]) == []


def test_the_owner_of_a_transcript(home: Path, projects: Path) -> None:
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  work: {}\n  lab: {}\n", encoding="utf-8")
    for name in ("work", "lab"):
        make_profile_dir(home / ".claude-profiles" / name)
        (home / ".claude-profiles" / name / "projects").symlink_to(projects)
    lab_dir = home / ".claude-profiles" / "lab"
    own = make_profile_dir(home / "own") / "projects"
    own.mkdir()
    for n in (1, 2, 3):
        write_transcript(projects, conversation(), session_id=session_id(n))
    write_transcript(own, conversation(), session_id=session_id(4))
    # 1 has a start log that names lab, while work's .claude.json says it ended there last.
    write_profile_start(
        projects, session_id(1), CWD, ts=1_794_100_000_000, pid=10, profile="lab", config_dir=str(lab_dir)
    )
    (home / ".claude-profiles" / "work" / ".claude.json").write_text(
        json.dumps({"projects": {"/a": {"lastSessionId": session_id(1)}, "/b": {"lastSessionId": session_id(2)}}}),
        encoding="utf-8",
    )
    (home / ".claude.json").write_text("{not json", encoding="utf-8")

    def owner(sid: str, profiles: list[str] | None = None) -> tuple[str, str] | None:
        found = find_session_transcript(sid).owner(profiles)
        return (found[0].name, found[1]) if found is not None else None

    assert owner(session_id(1)) == ("lab", "log")
    assert owner(session_id(2)) == ("work", "claude_json")
    assert owner(session_id(2), ["lab", "default"]) is None
    assert owner(session_id(3)) is None
    own_transcript = find_session_transcript(session_id(4), list_profiles([home / "own"]))
    found = own_transcript.owner(list_profiles([home / "own"]))
    assert found is not None and found[1] == "dir"

    assert [t.session_id for t in list_session_transcripts(started_by=["work"])] == [session_id(2)]
    assert [t.session_id for t in list_session_transcripts(started_by=["lab"])] == [session_id(1)]


def at(minutes: float) -> datetime:
    return datetime(2026, 11, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def test_model_activity_ignores_records_written_without_the_model(projects: Path) -> None:
    write_transcript(
        projects,
        [
            *conversation(),
            # An API error that Claude Code shows as a reply, with no response behind it.
            assistant([text("API Error")], minutes=2, model="<synthetic>"),
            system("local_command", minutes=3000, content="<command-name>/login</command-name>"),
            user("The session was renamed.", minutes=3001, origin=None, isMeta=True),
            attachment({"type": "date_change"}, minutes=3002),
            envelope("pr-link", minutes=3003, prUrl="https://example.com/pr/1"),
            bookkeeping("bridge-session"),
        ],
    )
    (transcript,) = list_session_transcripts()

    assert "last_model_activity_at" not in transcript.__dict__
    assert transcript.last_model_activity_at == at(1)
    assert transcript.last_record_at == at(3003)


def test_model_activity_is_found_far_from_the_end_and_across_a_long_line(projects: Path) -> None:
    filler = [bookkeeping("api-request-blob", blob="x" * 50_000) for _ in range(5)]
    write_transcript(projects, [user("go", minutes=0), assistant([text("y" * 200_000)], minutes=5), *filler])
    (transcript,) = list_session_transcripts()
    assert transcript.last_model_activity_at == at(5)


def test_model_activity_counts_subagents(projects: Path) -> None:
    main = write_transcript(projects, conversation())
    write_subagent(main, "a1b2c3d4e5f60718", [user("x", minutes=2), assistant([text("busy")], minutes=30)])
    write_subagent(main, "b2c3d4e5f6071829", [user("y", minutes=2), assistant([text("done")], minutes=3)])
    (transcript,) = list_session_transcripts()

    assert transcript.last_model_activity_at == at(30)
    assert [s.last_model_activity_at for s in transcript.subagents()] == [at(30), at(3)]


def test_a_session_without_responses_has_no_model_activity(projects: Path) -> None:
    write_transcript(projects, [user("hello", minutes=0)])
    (transcript,) = list_session_transcripts()
    assert transcript.last_model_activity_at is None
    assert transcript.cost_state is None


def test_the_newest_cost_state_is_parsed(projects: Path) -> None:
    def cost(total: float) -> dict[str, object]:
        return bookkeeping(
            "cost-state",
            totalCostUSD=total,
            totalAPIDuration=61_500,
            totalAPIDurationWithoutRetries=60_000,
            totalToolDuration=2_000,
            totalLinesAdded=12,
            totalLinesRemoved=3,
            totalDuration=3_600_000,
            startTime=1_793_523_600_000,
            modelUsage={
                "claude-opus-5": {
                    "inputTokens": 40,
                    "outputTokens": 500,
                    "thinkingTokens": 100,
                    "cacheReadInputTokens": 9_000,
                    "cacheCreationInputTokens": 800,
                    "webSearchRequests": 0,
                    "costUSD": total,
                },
                "broken": "not an object",
            },
            hasUnknownModelCost=False,
        )

    write_transcript(projects, [*conversation(), cost(0.5), bookkeeping("last-prompt"), cost(1.25)])
    (transcript,) = list_session_transcripts()

    state = transcript.cost_state
    assert state is not None
    assert (state.total_cost_usd, state.has_unknown_model_cost) == (1.25, False)
    assert state.started_at == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert (state.duration, state.api_duration, state.api_duration_without_retries, state.tool_duration) == (
        timedelta(hours=1),
        timedelta(seconds=61.5),
        timedelta(minutes=1),
        timedelta(seconds=2),
    )
    assert (state.lines_added, state.lines_removed) == (12, 3)
    (model,) = state.model_usage.values()
    assert list(state.model_usage) == ["claude-opus-5"]
    assert (model.input_tokens, model.output_tokens, model.thinking_tokens) == (40, 500, 100)
    assert (model.cache_read_input_tokens, model.cache_creation_input_tokens, model.web_search_requests) == (
        9_000,
        800,
        0,
    )
    assert model.cost_usd == 1.25


def test_the_name_and_the_last_location_come_from_the_end(projects: Path) -> None:
    write_transcript(
        projects,
        [
            *conversation(),
            bookkeeping("custom-title", customTitle="first-name"),
            bookkeeping("agent-name", agentName="first-name"),
            user("moved", minutes=5, cwd="/home/user/projects/example/.worktrees/w1", gitBranch="w1"),
            bookkeeping("custom-title", customTitle=" second-name "),
            bookkeeping("mode", mode="normal"),
        ],
    )
    (transcript,) = list_session_transcripts()

    assert transcript.name == "second-name"
    assert transcript.last_cwd == Path("/home/user/projects/example/.worktrees/w1")
    assert transcript.cwd == Path(CWD)
    assert transcript.last_git_branch == "w1"


def test_a_transcript_keeps_what_it_read_first(projects: Path) -> None:
    path = write_transcript(projects, conversation())
    (transcript,) = list_session_transcripts()
    assert transcript.last_model_activity_at == at(1)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(jsonl([assistant([text("later")], minutes=9)]))

    assert transcript.last_model_activity_at == at(1)
    assert read_session_transcript(path).last_model_activity_at == at(9)


def test_lazy_facts_are_found_in_json_with_spaces(projects: Path) -> None:
    # Other tools, and json.dumps with its defaults, put a space after each colon; Claude Code does not.
    path = projects / "-home-user-projects-example" / f"{SESSION_ID}.jsonl"
    path.parent.mkdir(parents=True)
    records = [
        *conversation(),
        bookkeeping("cost-state", totalCostUSD=0.75),
        bookkeeping("agent-name", agentName="spaced"),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    (transcript,) = list_session_transcripts()

    assert transcript.last_model_activity_at == at(1)
    assert transcript.cost_state is not None and transcript.cost_state.total_cost_usd == 0.75
    assert transcript.name == "spaced"
    assert transcript.last_cwd == Path(CWD)
