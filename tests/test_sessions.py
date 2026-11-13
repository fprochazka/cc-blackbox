from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from records import SAMPLE_RECORD, dead_pid, live_record, make_profile_dir, write_record
from transcript_records import BASE_TIME, CWD, envelope, user, write_transcript

from cc_blackbox import (
    ActiveSession,
    AmbiguousSession,
    ClaudeProfile,
    ProfileNotFound,
    SessionNotFound,
    current_session,
    find_session,
    list_active_sessions,
    list_profiles,
    scratchpad_dir,
)
from cc_blackbox.sessions.liveness import own_pid_domain

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")

STARTED_AT_MS = int(BASE_TIME.timestamp() * 1000)
PROJECT_A = "/home/user/project-a"
PROJECT_B = "/home/user/project-b"


def write_launch_transcript(config: Path, *, cwd: str = CWD, launch_cwd: str | None = None) -> Path:
    """Write the transcript of a session started at :data:`STARTED_AT_MS` in ``launch_cwd`` and now in ``cwd``.

    Like Claude Code, it stamps the first record a few milliseconds before the registry record's startedAt, and stores
    the file under the project dir of the current ``cwd``.
    """
    launched = launch_cwd if launch_cwd is not None else cwd
    records = [
        envelope("attachment", minutes=-0.005 / 60, cwd=launched, attachment={"type": "hook_success"}),
        user("first prompt", minutes=1, cwd=launched),
    ]
    if launched != cwd:
        records.append(user("/cd", minutes=2, cwd=cwd))
    return write_transcript(config / "projects", records, cwd=cwd)


@pytest.fixture
def profiles(home: Path) -> list[ClaudeProfile]:
    make_profile_dir(home / ".claude")
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  personal: {}\n  work: {}\n", encoding="utf-8")
    make_profile_dir(home / ".claude-profiles" / "personal")
    make_profile_dir(home / ".claude-profiles" / "work")
    return list_profiles()


def config_dir(profiles: list[ClaudeProfile], name: str) -> Path:
    return next(p.config_dir for p in profiles if p.name == name)


def test_full_record_is_parsed(profiles: list[ClaudeProfile]) -> None:
    path = write_record(config_dir(profiles, "personal"), 4242, {**SAMPLE_RECORD, "pid": 4242})

    [session] = list_active_sessions(check_liveness=False)

    assert session.profile.name == "personal"
    assert session.pid == 4242
    assert session.session_id == "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11"
    assert session.name == "example-project-a1"
    assert session.name_source == "derived"
    assert session.cwd == Path("/home/user/projects/example")
    assert session.kind == "interactive"
    assert session.entrypoint == "cli"
    assert session.status == "idle"
    assert session.version == "2.1.319"
    assert session.started_at == datetime.fromtimestamp(1794100000.0, tz=UTC)
    assert session.updated_at == datetime.fromtimestamp(1794100360.0, tz=UTC)
    assert session.messaging_socket == Path("/run/user/1000/cc-socks/0.sock")
    assert session.peer_protocol == 1
    assert session.peer_features == frozenset({"notify_idle", "reply_across_default_dirs", "artifact_yield"})
    assert session.tmux == "example:@1.%1"
    assert session.bridge_session_id == "session_01ExampleBridgeId000000000"
    assert session.record_path == path
    assert session.raw["asksForConvertedSkills"] is False
    assert session.address == "personal/example-project-a1"
    assert session.scratchpad_dir is not None
    assert session.scratchpad_dir.parts[-3:] == (
        "-home-user-projects-example",
        "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11",
        "scratchpad",
    )


def test_partial_and_odd_records_are_tolerated(profiles: list[ClaudeProfile]) -> None:
    personal = config_dir(profiles, "personal")
    write_record(personal, 1, {})
    write_record(
        personal,
        2,
        {
            "cwd": "?",
            "startedAt": 0,
            "status": "something-new",
            "kind": 7,
            "peerProtocol": True,
            "peerFeatures": ["ok", 3],
            "messagingSocketPath": "",
            "futureField": {"nested": True},
        },
    )
    write_record(personal, 3, '{"pid": 3, "sessionId": "trunc')  # written half-way
    write_record(personal, 4, "[1, 2]")
    write_record(personal, 5, b"\xff\xfe".decode("latin-1"))
    sessions_dir = personal / "sessions"
    (sessions_dir / "007.json").write_text("{}", encoding="utf-8")  # not a canonical pid
    (sessions_dir / "8.0123abcd.key").write_text("not read", encoding="utf-8")
    (sessions_dir / ".fleetview-heartbeat").write_text("", encoding="utf-8")

    first, second = list_active_sessions(["personal"], check_liveness=False)

    assert first.pid == 1
    assert first.session_id is None
    assert first.cwd is None
    assert first.scratchpad_dir is None
    assert first.address == "personal/pid:1"
    assert first.can_receive_messages is False
    assert second.pid == 2
    assert second.cwd is None
    assert second.started_at is None
    assert second.status == "something-new"
    assert second.kind is None
    assert second.peer_protocol is None
    assert second.peer_features == frozenset({"ok"})
    assert second.messaging_socket is None
    assert second.raw["futureField"] == {"nested": True}


def test_missing_sessions_dir_gives_no_sessions(home: Path) -> None:
    (home / ".claude").mkdir()
    assert list_active_sessions(check_liveness=False) == []


def test_sessions_are_ordered_by_profile_then_start(profiles: list[ClaudeProfile]) -> None:
    personal, work = config_dir(profiles, "personal"), config_dir(profiles, "work")
    write_record(work, 30, {**SAMPLE_RECORD, "pid": 30, "startedAt": 1000})
    write_record(personal, 20, {**SAMPLE_RECORD, "pid": 20, "startedAt": 3000})
    write_record(personal, 10, {**SAMPLE_RECORD, "pid": 10, "startedAt": 2000})
    write_record(config_dir(profiles, "default"), 40, {**SAMPLE_RECORD, "pid": 40})

    assert [s.pid for s in list_active_sessions(check_liveness=False)] == [40, 10, 20, 30]
    assert [s.pid for s in list_active_sessions(["work", "personal"], check_liveness=False)] == [30, 10, 20]
    with pytest.raises(ProfileNotFound):
        list_active_sessions(["nope"])


def test_liveness_drops_dead_and_reused_pids(profiles: list[ClaudeProfile]) -> None:
    personal = config_dir(profiles, "personal")
    me = os.getpid()
    write_record(personal, me, live_record(me))
    gone = dead_pid()
    write_record(personal, gone, live_record(me, pid=gone))
    other_domain = me + 1 if me + 1 != gone else me + 2
    write_record(personal, other_domain, live_record(me, pid=other_domain, pidDomain="linux:another-host:pid:[1]"))

    alive = list_active_sessions()
    assert [s.pid for s in alive] == [me]
    assert len(list_active_sessions(check_liveness=False)) == 3


@pytest.mark.parametrize("pid_domain", ["darwin", "linux", "linux:another-host:pid:[1]"])
def test_liveness_ignores_the_pid_domain_outside_linux(
    profiles: list[ClaudeProfile], monkeypatch: pytest.MonkeyPatch, pid_domain: str
) -> None:
    # Claude Code on macOS writes "darwin" in some versions and "linux" in others; macOS has no pid namespaces.
    monkeypatch.setattr(sys, "platform", "darwin")
    own_pid_domain.cache_clear()
    personal = config_dir(profiles, "personal")
    me = os.getpid()
    write_record(personal, me, {**live_record(me), "pidDomain": pid_domain})

    try:
        assert [s.pid for s in list_active_sessions()] == [me]
    finally:
        own_pid_domain.cache_clear()


@linux_only
def test_liveness_compares_proc_start(profiles: list[ClaudeProfile]) -> None:
    personal = config_dir(profiles, "personal")
    me = os.getpid()
    write_record(personal, me, live_record(me, procStart="1"))
    assert list_active_sessions() == []


@linux_only
def test_scratchpad_uses_the_tmpdir_from_the_target_process_environment(
    profiles: list[ClaudeProfile], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_tmp = tmp_path / "target-tmp"
    env = {**os.environ, "CLAUDE_CODE_TMPDIR": str(target_tmp)}
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "own-tmp"))
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=env) as child:
        try:
            personal = config_dir(profiles, "personal")
            write_record(personal, child.pid, live_record(child.pid, startedAt=STARTED_AT_MS))
            write_launch_transcript(personal)
            [session] = list_active_sessions()
        finally:
            child.kill()

    assert session.scratchpad_dir_inferred is False
    assert session.scratchpad_dir == scratchpad_dir(
        "/home/user/projects/example", "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11", tmpdir=target_tmp
    )


def make_sessions(profiles: list[ClaudeProfile]) -> list[ActiveSession]:
    personal, work = config_dir(profiles, "personal"), config_dir(profiles, "work")
    write_record(personal, 11, {**SAMPLE_RECORD, "pid": 11, "sessionId": "aaaa1111-0000", "name": "Release Notes"})
    write_record(personal, 12, {**SAMPLE_RECORD, "pid": 12, "sessionId": "aaaa2222-0000", "name": "example-b2"})
    write_record(work, 21, {**SAMPLE_RECORD, "pid": 21, "sessionId": "bbbb1111-0000", "name": "example-b2"})
    write_record(work, 22, {**SAMPLE_RECORD, "pid": 22, "sessionId": "release notes"})
    return list_active_sessions(check_liveness=False)


@pytest.mark.parametrize(
    ("query", "pid"),
    [
        ("personal/example-b2", 12),
        ("work/example-b2", 21),
        ("work/pid:21", 21),
        ("pid:11", 11),
        ("aaaa2222-0000", 12),
        ("bbbb", 21),
        ("release-notes", 11),
        ("  RELEASE\tNotes ", 11),
        ("release notes", 22),  # an exact session id wins over a name
        ("personal/aaaa1", 11),
    ],
)
def test_find_session(profiles: list[ClaudeProfile], query: str, pid: int) -> None:
    sessions = make_sessions(profiles)
    assert find_session(query, sessions=sessions).pid == pid


def test_find_session_reports_ambiguity(profiles: list[ClaudeProfile]) -> None:
    sessions = make_sessions(profiles)
    with pytest.raises(AmbiguousSession) as error:
        find_session("example-b2", sessions=sessions)
    assert sorted(s.pid for s in error.value.candidates) == [12, 21]
    with pytest.raises(AmbiguousSession):
        find_session("aaaa", sessions=sessions)


@pytest.mark.parametrize("query", ["", "nothing", "pid:99", "pid:abc", "work/Release Notes", "nope/example-b2"])
def test_find_session_not_found(profiles: list[ClaudeProfile], query: str) -> None:
    sessions = make_sessions(profiles)
    with pytest.raises(SessionNotFound):
        find_session(query, sessions=sessions)


def test_name_normalisation_matches_claude_code(profiles: list[ClaudeProfile]) -> None:
    personal = config_dir(profiles, "personal")
    write_record(personal, 1, {**SAMPLE_RECORD, "pid": 1, "name": "ｆｕｌｌ\u200bwidth"})
    sessions = list_active_sessions(check_liveness=False)
    assert find_session("fullwidth", sessions=sessions).pid == 1


def test_current_session_outside_claude_code(profiles: list[ClaudeProfile]) -> None:
    assert current_session() is None


def test_current_session_by_pid(
    profiles: list[ClaudeProfile], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    work = config_dir(profiles, "work")
    me = os.getpid()
    write_record(work, me, live_record(me, cwd=PROJECT_B, startedAt=STARTED_AT_MS))
    write_launch_transcript(work, cwd=PROJECT_B, launch_cwd=PROJECT_A)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude-profiles" / "work"))
    monkeypatch.setenv("CLAUDE_PID", str(me))
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "cc-tmp"))

    session = current_session()

    assert session is not None
    assert session.pid == me
    assert session.profile.name == "work"
    assert session.cwd == Path(PROJECT_B)
    assert session.launch_cwd == Path(PROJECT_A)
    assert session.scratchpad_dir_inferred is False
    assert session.scratchpad_dir == scratchpad_dir(
        PROJECT_A, "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11", tmpdir=tmp_path / "cc-tmp"
    )


def test_current_session_by_socket(profiles: list[ClaudeProfile], monkeypatch: pytest.MonkeyPatch) -> None:
    me = os.getpid()
    write_record(config_dir(profiles, "default"), me, live_record(me))
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", f"/run/user/1000/cc-socks/{me}.sock")
    session = current_session()
    assert session is not None
    assert session.pid == me
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/run/user/1000/cc-socks/1.sock")
    assert current_session() is None


def test_can_receive_messages(profiles: list[ClaudeProfile], tmp_path: Path) -> None:
    socket_file = tmp_path / "fake.sock"
    socket_file.touch()
    personal = config_dir(profiles, "personal")
    write_record(personal, 1, {**SAMPLE_RECORD, "pid": 1, "messagingSocketPath": str(socket_file)})
    write_record(personal, 2, {**SAMPLE_RECORD, "pid": 2, "messagingSocketPath": str(socket_file), "peerProtocol": 2})
    write_record(personal, 3, {**SAMPLE_RECORD, "pid": 3, "messagingSocketPath": str(tmp_path / "missing.sock")})
    assert [s.can_receive_messages for s in list_active_sessions(check_liveness=False)] == [True, False, False]


def test_scratchpad_tmpdir_from_settings_files(profiles: list[ClaudeProfile], tmp_path: Path) -> None:
    personal = config_dir(profiles, "personal")
    gone = dead_pid()
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    write_record(personal, gone, {**SAMPLE_RECORD, "pid": gone, "cwd": str(project), "startedAt": STARTED_AT_MS})
    write_launch_transcript(personal, cwd=str(project))
    session_id = SAMPLE_RECORD["sessionId"]

    [session] = list_active_sessions(check_liveness=False)
    assert session.scratchpad_dir_inferred is True  # the process is gone and no settings name a temp dir

    (personal / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": str(tmp_path / "user")}}))
    [session] = list_active_sessions(check_liveness=False)
    assert session.scratchpad_dir_inferred is False
    assert session.scratchpad_dir == scratchpad_dir(project, session_id, tmpdir=tmp_path / "user")

    (project / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"CLAUDE_CODE_TMPDIR": str(tmp_path / "proj")}})
    )
    [session] = list_active_sessions(check_liveness=False)
    assert session.scratchpad_dir == scratchpad_dir(project, session_id, tmpdir=tmp_path / "proj")


def test_scratchpad_follows_the_launch_cwd_after_cd(profiles: list[ClaudeProfile], tmp_path: Path) -> None:
    personal = config_dir(profiles, "personal")
    (personal / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": str(tmp_path / "cc-tmp")}}))
    write_record(personal, 4242, {**SAMPLE_RECORD, "pid": 4242, "cwd": PROJECT_B, "startedAt": STARTED_AT_MS})
    write_launch_transcript(personal, cwd=PROJECT_B, launch_cwd=PROJECT_A)

    [session] = list_active_sessions(check_liveness=False)

    assert session.cwd == Path(PROJECT_B)
    assert session.launch_cwd == Path(PROJECT_A)
    assert session.scratchpad_dir_inferred is False
    assert session.scratchpad_dir == scratchpad_dir(PROJECT_A, SAMPLE_RECORD["sessionId"], tmpdir=tmp_path / "cc-tmp")


@pytest.mark.parametrize("earlier_records", [3, 3000])
def test_launch_cwd_of_a_resumed_session_skips_the_earlier_runs(
    profiles: list[ClaudeProfile], tmp_path: Path, earlier_records: int
) -> None:
    # 3000 records make the transcript long enough to be bisected rather than read line by line.
    personal = config_dir(profiles, "personal")
    (personal / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": str(tmp_path / "cc-tmp")}}))
    write_record(personal, 4242, {**SAMPLE_RECORD, "pid": 4242, "cwd": PROJECT_B, "startedAt": STARTED_AT_MS})
    earlier = [user(f"earlier prompt {n:04d}", minutes=-600 + n / 100, cwd=PROJECT_A) for n in range(earlier_records)]
    write_transcript(
        personal / "projects",
        [*earlier, {"type": "last-prompt", "lastPrompt": "earlier"}, user("resumed", minutes=0.5, cwd=PROJECT_B)],
        cwd=PROJECT_B,
    )

    [session] = list_active_sessions(check_liveness=False)

    assert session.launch_cwd == Path(PROJECT_B)
    assert session.scratchpad_dir_inferred is False
    assert session.scratchpad_dir == scratchpad_dir(PROJECT_B, SAMPLE_RECORD["sessionId"], tmpdir=tmp_path / "cc-tmp")


@pytest.mark.parametrize("transcript", ["missing", "only-earlier-runs"])
def test_scratchpad_falls_back_to_the_record_cwd(
    profiles: list[ClaudeProfile], tmp_path: Path, transcript: str
) -> None:
    personal = config_dir(profiles, "personal")
    (personal / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": str(tmp_path / "cc-tmp")}}))
    write_record(personal, 4242, {**SAMPLE_RECORD, "pid": 4242, "cwd": PROJECT_B, "startedAt": STARTED_AT_MS})
    if transcript == "only-earlier-runs":
        write_transcript(personal / "projects", [user("earlier", minutes=-600, cwd=PROJECT_A)], cwd=PROJECT_B)

    [session] = list_active_sessions(check_liveness=False)

    assert session.launch_cwd is None
    assert session.scratchpad_dir_inferred is True
    assert session.scratchpad_dir == scratchpad_dir(PROJECT_B, SAMPLE_RECORD["sessionId"], tmpdir=tmp_path / "cc-tmp")
