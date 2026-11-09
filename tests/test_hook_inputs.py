from __future__ import annotations

import io
import json
from typing import Any

import pytest
from hook_payloads import CWD, EVENTS, SESSION_ID, TRANSCRIPT, payload

from cc_blackbox import HookInputError
from cc_blackbox.hooks import (
    KNOWN_HOOK_EVENTS,
    AgentInput,
    EditInput,
    GlobInput,
    GrepInput,
    HookInput,
    PermissionRequestInput,
    PostToolBatchInput,
    PreToolUseInput,
    ReadInput,
    SessionStartInput,
    ShellInput,
    StopInput,
    SubagentStopInput,
    ToolHookInput,
    UnknownHookInput,
    WebFetchInput,
    WebSearchInput,
    WriteInput,
    parse_hook_input,
    read_hook_input,
)

# Event-specific attributes checked for each event, beyond the class and the common fields.
EXPECTED: dict[str, dict[str, Any]] = {
    "PreToolUse": {"tool_name": "Bash", "tool_use_id": "toolu_01EXAMPLE", "mcp_server": None},
    "PermissionRequest": {"tool_name": "Bash"},
    "PermissionDenied": {"reason": "[Irreversible Local Destruction]", "permission_mode": "auto"},
    "PostToolUse": {"tool_response": {"filePath": "/path/to/file.txt", "type": "create"}, "duration_ms": 12},
    "PostToolUseFailure": {"error": "Exit code 1\nError: Cannot find module 'express'", "is_interrupt": False},
    "PostToolBatch": {},
    "UserPromptSubmit": {"prompt": "Write a function to calculate the factorial of a number", "source": None},
    "UserPromptExpansion": {
        "command_name": "example-skill",
        "command_args": "arg1 arg2",
        "expansion_type": "slash_command",
    },
    "Stop": {"stop_hook_active": True, "last_assistant_message": "I've completed the refactoring."},
    "SubagentStop": {"agent_id": "def456", "agent_type": "Explore", "stop_hook_active": False},
    "StopFailure": {"error": "rate_limit", "error_details": "429 Too Many Requests"},
    "SubagentStart": {"agent_id": "agent-abc123", "agent_type": "Explore"},
    "MessageDisplay": {"index": 0, "final": False, "delta": "Here is the plan:\n"},
    "Notification": {"notification_type": "permission_prompt", "title": "Permission needed"},
    "TaskCreated": {"task_id": "task-001", "teammate_name": "implementer"},
    "TaskCompleted": {"task_subject": "Implement user authentication"},
    "TeammateIdle": {"teammate_name": "researcher"},
    "SessionStart": {"source": "resume", "seconds_since_last_response": 5400.0, "context_tokens": 182340},
    "SessionEnd": {"reason": "other"},
    "Setup": {"trigger": "init"},
    "InstructionsLoaded": {"globs": ("src/**",), "load_reason": "path_glob_match", "parent_file_path": None},
    "ConfigChange": {"source": "project_settings"},
    "CwdChanged": {"new_cwd": f"{CWD}/src"},
    "DirectoryAdded": {"directory": "/home/user/projects/other"},
    "FileChanged": {"event": "change"},
    "WorktreeCreate": {"name": "feature-auth"},
    "WorktreeRemove": {"worktree_path": f"{CWD}/.claude/worktrees/feature-auth"},
    "PreCompact": {"trigger": "manual", "custom_instructions": None},
    "PostCompact": {"compact_summary": "Summary of the compacted conversation."},
    "PreModelSwitch": {"to_model": "claude-opus-5", "prompt_cache_warm": True, "estimated_cache_write_usd": 1.1396},
    "PostModelSwitch": {"requested_model": None, "source": "auto", "estimated_cache_write_usd": 0.0},
    "Elicitation": {"mcp_server_name": "my-mcp-server", "mode": "form", "url": None},
    "ElicitationResult": {"action": "accept", "content": {"project_key": "DOCS"}},
}


def test_every_documented_event_has_a_payload_and_a_class() -> None:
    assert set(EVENTS) == set(KNOWN_HOOK_EVENTS) == set(EXPECTED)
    assert len(KNOWN_HOOK_EVENTS) == 33


@pytest.mark.parametrize("event", EVENTS)
def test_each_event_parses_into_its_class(event: str) -> None:
    parsed = parse_hook_input(payload(event))
    assert type(parsed).__name__ == f"{event}Input"
    assert parsed.EVENT == event == parsed.hook_event_name
    assert parsed.session_id == SESSION_ID
    assert parsed.transcript_path == TRANSCRIPT
    assert parsed.cwd == CWD
    assert parsed.effort_level == "high"
    assert parsed.type_mismatches == ()
    for name, value in EXPECTED[event].items():
        assert getattr(parsed, name) == value, name


def test_tool_fields() -> None:
    parsed = parse_hook_input(payload("PreToolUse", mcp_server={"name": "docs", "source": "plugin"}))
    assert isinstance(parsed, PreToolUseInput) and isinstance(parsed, ToolHookInput)
    assert parsed.tool_input["command"] == "npm test"
    assert parsed.mcp_server is not None and (parsed.mcp_server.name, parsed.mcp_server.source) == ("docs", "plugin")
    with pytest.raises(TypeError):
        parsed.tool_input["command"] = "x"  # type: ignore[index]
    assert parsed.tool_input_with({"command": "npm test -- --bail"}) == {
        "command": "npm test -- --bail",
        "description": "Run test suite",
        "timeout": 120000,
        "run_in_background": False,
    }


def test_nested_shapes() -> None:
    batch = parse_hook_input(payload("PostToolBatch"))
    assert isinstance(batch, PostToolBatchInput)
    assert [c.tool_use_id for c in batch.tool_calls] == ["toolu_01", "toolu_02"]
    assert batch.tool_calls[0].tool_input["file_path"] == "/path/to/accounts.py"

    stop = parse_hook_input(payload("Stop"))
    assert isinstance(stop, StopInput)
    (task,) = stop.background_tasks
    assert (task.id, task.type, task.status) == ("task-001", "shell", "running")
    assert task.raw["command"] == "tail -f x"
    (cron,) = stop.session_crons
    assert (cron.schedule, cron.recurring) == ("0 9 * * 1-5", True)

    request = parse_hook_input(payload("PermissionRequest"))
    assert isinstance(request, PermissionRequestInput)
    assert request.permission_suggestions[0]["destination"] == "localSettings"


def test_unknown_fields_stay_in_raw_and_wrong_types_read_as_none() -> None:
    parsed = parse_hook_input(payload("SessionStart", brand_new={"a": 1}, context_tokens="many", effort={"level": 3}))
    assert isinstance(parsed, SessionStartInput)
    assert parsed.raw["brand_new"] == {"a": 1}
    assert parsed.context_tokens is None
    assert parsed.effort_level is None
    assert parsed.type_mismatches == ("effort.level", "context_tokens")


def test_a_bool_is_not_an_int_and_an_int_is_a_float() -> None:
    parsed = parse_hook_input(payload("SessionStart", context_tokens=True, estimated_cache_write_usd=2))
    assert isinstance(parsed, SessionStartInput)
    assert parsed.context_tokens is None and parsed.estimated_cache_write_usd == 2.0
    assert parsed.type_mismatches == ("context_tokens",)


def test_list_entries_that_are_not_objects_are_dropped() -> None:
    parsed = parse_hook_input(payload("Stop", background_tasks=["oops", {"id": "t"}]))
    assert isinstance(parsed, StopInput)
    assert [t.id for t in parsed.background_tasks] == ["t"]
    assert parsed.type_mismatches == ("background_tasks",)


def test_empty_paths_read_as_none() -> None:
    parsed = parse_hook_input(payload("SubagentStop", transcript_path="", agent_transcript_path="", cwd=""))
    assert isinstance(parsed, SubagentStopInput)
    assert (parsed.transcript_path, parsed.agent_transcript_path, parsed.cwd) == (None, None, None)
    assert parsed.type_mismatches == ()


def test_a_remote_call_keeps_its_served_session_id() -> None:
    parsed = parse_hook_input(payload("PreToolUse", session_id="served:unknown", transcript_path=""))
    assert parsed.session_id == "served:unknown" and parsed.transcript_path is None


def test_unknown_event() -> None:
    parsed = parse_hook_input({**payload("Stop"), "hook_event_name": "BrandNewEvent", "x": 1})
    assert type(parsed) is UnknownHookInput
    assert parsed.hook_event_name == "BrandNewEvent" and parsed.EVENT is None
    assert parsed.session_id == SESSION_ID and parsed.raw["x"] == 1


def test_missing_event_name_uses_the_assumed_event() -> None:
    data = {"tool_input": {"command": "glab ci view"}}
    assert type(parse_hook_input(data)) is UnknownHookInput
    parsed = parse_hook_input(data, event="PreToolUse")
    assert isinstance(parsed, PreToolUseInput) and parsed.hook_event_name == "PreToolUse"
    assert parsed.tool_input["command"] == "glab ci view"
    # A payload that names its event wins over the assumption.
    assert isinstance(parse_hook_input(payload("Stop"), event="PreToolUse"), StopInput)


@pytest.mark.parametrize("data", ["[]", '"text"', "not json", b"\xff\xfe", "", "null"])
def test_a_payload_that_is_not_an_object_raises(data: str | bytes) -> None:
    with pytest.raises(HookInputError):
        parse_hook_input(data)


def test_read_from_text_and_binary_streams() -> None:
    raw = json.dumps(payload("UserPromptSubmit", prompt="Grüße")).encode("utf-8")
    assert read_hook_input(io.BytesIO(raw)).raw["prompt"] == "Grüße"
    # A text stream with a binary buffer is read through the buffer as UTF-8, whatever its own encoding.
    wrapper = io.TextIOWrapper(io.BytesIO(raw), encoding="cp1252")
    assert read_hook_input(wrapper).raw["prompt"] == "Grüße"
    assert isinstance(read_hook_input(io.StringIO(raw.decode()), event="Stop"), HookInput)


TOOL_VIEWS: list[tuple[str, dict[str, Any], object]] = [
    (
        "Bash",
        {"command": "ls", "description": "List", "timeout": 5, "run_in_background": True},
        ShellInput(command="ls", description="List", timeout_ms=5, run_in_background=True),
    ),
    (
        "PowerShell",
        {"command": "dir"},
        ShellInput(command="dir", description=None, timeout_ms=None, run_in_background=None),
    ),
    ("Read", {"file_path": "/a", "offset": 10, "limit": 5}, ReadInput(file_path="/a", offset=10, limit=5)),
    ("Write", {"file_path": "/a", "content": "x"}, WriteInput(file_path="/a", content="x")),
    (
        "Edit",
        {"file_path": "/a", "old_string": "x", "new_string": "y", "replace_all": False},
        EditInput(file_path="/a", old_string="x", new_string="y", replace_all=False),
    ),
    ("Glob", {"pattern": "**/*.py"}, GlobInput(pattern="**/*.py", path=None)),
    (
        "Grep",
        {"pattern": "TODO", "-i": True, "output_mode": "count"},
        GrepInput(pattern="TODO", path=None, glob=None, output_mode="count", case_insensitive=True, multiline=None),
    ),
    (
        "WebFetch",
        {"url": "https://example.com", "prompt": "p"},
        WebFetchInput(url="https://example.com", prompt="p", offset=None),
    ),
    (
        "WebSearch",
        {"query": "q", "allowed_domains": ["example.com"]},
        WebSearchInput(query="q", allowed_domains=("example.com",), blocked_domains=None, mode=None),
    ),
    (
        "Agent",
        {"prompt": "Find it", "subagent_type": "Explore"},
        AgentInput(prompt="Find it", description=None, subagent_type="Explore", model=None),
    ),
    ("Bash", {"command": 42}, None),
    ("NotebookEdit", {"notebook_path": "/a.ipynb"}, None),
    ("mcp__docs__search", {"query": "q"}, None),
]


@pytest.mark.parametrize(("tool", "tool_input", "expected"), TOOL_VIEWS)
def test_typed_tool_input(tool: str, tool_input: dict[str, Any], expected: object) -> None:
    parsed = parse_hook_input(payload("PreToolUse", tool_name=tool, tool_input=tool_input))
    assert isinstance(parsed, PreToolUseInput)
    assert parsed.typed_tool_input() == expected
