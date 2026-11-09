from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from hook_payloads import payload
from records import live_record, make_profile_dir, write_record
from transcript_records import SESSION_ID, conversation, write_subagent, write_transcript

from cc_blackbox.hooks import SubagentStopInput, parse_hook_input


@pytest.fixture
def projects(home: Path) -> Path:
    make_profile_dir(home / ".claude")
    path = home / ".claude" / "projects"
    path.mkdir()
    return path


def test_transcript_and_agent_transcript(projects: Path) -> None:
    main = write_transcript(projects, conversation())
    agent = write_subagent(main, "def456", conversation())
    event = parse_hook_input(payload("SubagentStop", transcript_path=str(main), agent_transcript_path=str(agent)))
    assert isinstance(event, SubagentStopInput)
    transcript = event.transcript()
    assert transcript is not None and transcript.session_id == SESSION_ID
    subagent = event.agent_transcript()
    assert subagent is not None and subagent.agent_id == "def456"


def test_missing_transcript_is_none(projects: Path) -> None:
    assert parse_hook_input(payload("Stop", transcript_path=str(projects / "nope.jsonl"))).transcript() is None
    assert parse_hook_input(payload("Stop", transcript_path="")).transcript() is None
    event = parse_hook_input(payload("SubagentStop", transcript_path="", agent_transcript_path="/x"))
    assert isinstance(event, SubagentStopInput) and event.agent_transcript() is None


def test_active_session_through_the_inherited_environment(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_profile_dir(home / ".claude")
    me = os.getpid()
    write_record(home / ".claude", me, live_record(me))
    monkeypatch.setenv("CLAUDE_PID", str(me))
    session = parse_hook_input(payload("PreToolUse")).active_session()
    assert session is not None and session.pid == me and session.session_id == SESSION_ID


def test_active_session_found_in_the_registry_without_the_environment(home: Path) -> None:
    make_profile_dir(home / ".claude")
    me = os.getpid()
    write_record(home / ".claude", me, live_record(me))
    session = parse_hook_input(payload("PreToolUse")).active_session()
    assert session is not None and session.pid == me
    assert parse_hook_input(payload("PreToolUse", session_id="served:unknown")).active_session() is None


def test_parsing_does_not_load_the_session_code() -> None:
    code = (
        "import sys, json; from cc_blackbox.hooks import parse_hook_input; "
        "parse_hook_input({'hook_event_name': 'Stop', 'transcript_path': '/x'}); "
        "print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "cc_blackbox.sessions" not in out and "yaml" not in out
