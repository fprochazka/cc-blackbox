from __future__ import annotations

import io
from typing import Any

import pytest
from hook_output_schema import problems

from cc_blackbox.hooks import (
    ConfigChangeReply,
    CwdChangedReply,
    ElicitationReply,
    ElicitationResultReply,
    FileChangedReply,
    Halt,
    HookReply,
    HookResponse,
    MessageDisplayReply,
    PermissionDeniedReply,
    PermissionRequestReply,
    PostModelSwitchReply,
    PostToolBatchReply,
    PostToolUseFailureReply,
    PostToolUseReply,
    PreCompactReply,
    PreModelSwitchReply,
    PreToolUseReply,
    RawReply,
    SessionStartReply,
    StopReply,
    SubagentStartReply,
    SubagentStopReply,
    TaskCompletedReply,
    TaskCreatedReply,
    TeammateIdleReply,
    UserPromptExpansionReply,
    UserPromptSubmitReply,
    WorktreeCreateReply,
    WorktreeRemoveReply,
)


def specific(event: str, **fields: Any) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, **fields}}


BLOCK = {"decision": "block", "reason": "No."}

# (reply, the event it answers, the JSON it must produce)
GOLDEN: list[tuple[HookReply, str, dict[str, Any]]] = [
    (
        PreToolUseReply.allow(reason="Known safe.", updated_input={"command": "ls -la"}, additional_context="ctx"),
        "PreToolUse",
        specific(
            "PreToolUse",
            permissionDecision="allow",
            permissionDecisionReason="Known safe.",
            updatedInput={"command": "ls -la"},
            additionalContext="ctx",
        ),
    ),
    (
        PreToolUseReply.deny("Use glab-pipeline inspect instead."),
        "PreToolUse",
        specific(
            "PreToolUse", permissionDecision="deny", permissionDecisionReason="Use glab-pipeline inspect instead."
        ),
    ),
    (
        PreToolUseReply.ask("Deploys need a human."),
        "PreToolUse",
        specific("PreToolUse", permissionDecision="ask", permissionDecisionReason="Deploys need a human."),
    ),
    (PreToolUseReply.defer(), "PreToolUse", specific("PreToolUse", permissionDecision="defer")),
    (
        PreToolUseReply.context("The target is staging.", updated_input={"command": "x"}),
        "PreToolUse",
        specific("PreToolUse", updatedInput={"command": "x"}, additionalContext="The target is staging."),
    ),
    (
        PermissionRequestReply.allow(
            updated_input={"command": "npm run lint"},
            updated_permissions=[{"type": "setMode", "mode": "acceptEdits", "destination": "session"}],
        ),
        "PermissionRequest",
        specific(
            "PermissionRequest",
            decision={
                "behavior": "allow",
                "updatedInput": {"command": "npm run lint"},
                "updatedPermissions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}],
            },
        ),
    ),
    (
        PermissionRequestReply.deny("Not on Fridays.", interrupt=True),
        "PermissionRequest",
        specific("PermissionRequest", decision={"behavior": "deny", "message": "Not on Fridays.", "interrupt": True}),
    ),
    (PermissionDeniedReply.allow_retry(), "PermissionDenied", specific("PermissionDenied", retry=True)),
    (
        PostToolUseReply.block("No.", additional_context="ctx"),
        "PostToolUse",
        {**BLOCK, **specific("PostToolUse", additionalContext="ctx")},
    ),
    (
        PostToolUseReply.context("Generated file.", classifier_context="Staging database."),
        "PostToolUse",
        specific("PostToolUse", additionalContext="Generated file.", classifierContext="Staging database."),
    ),
    (
        PostToolUseReply.replace_output({"stdout": "[redacted]", "stderr": "", "interrupted": False, "isImage": False}),
        "PostToolUse",
        specific(
            "PostToolUse",
            updatedToolOutput={"stdout": "[redacted]", "stderr": "", "interrupted": False, "isImage": False},
        ),
    ),
    (
        PostToolUseFailureReply.context("Run npm install."),
        "PostToolUseFailure",
        specific("PostToolUseFailure", additionalContext="Run npm install."),
    ),
    (PostToolBatchReply.block("No."), "PostToolBatch", BLOCK),
    (PostToolBatchReply.context("ctx"), "PostToolBatch", specific("PostToolBatch", additionalContext="ctx")),
    (
        UserPromptSubmitReply.block("No.", suppress_original_prompt=True),
        "UserPromptSubmit",
        {**BLOCK, **specific("UserPromptSubmit", suppressOriginalPrompt=True)},
    ),
    (
        UserPromptSubmitReply.context("ctx", session_title="title"),
        "UserPromptSubmit",
        specific("UserPromptSubmit", additionalContext="ctx", sessionTitle="title"),
    ),
    (UserPromptExpansionReply.block("No."), "UserPromptExpansion", BLOCK),
    (
        UserPromptExpansionReply.context("ctx"),
        "UserPromptExpansion",
        specific("UserPromptExpansion", additionalContext="ctx"),
    ),
    (
        SessionStartReply(
            additional_context="Branch: main.",
            initial_user_message="Start.",
            session_title="work",
            watch_paths=("/a/.envrc",),
            reload_skills=True,
        ),
        "SessionStart",
        specific(
            "SessionStart",
            additionalContext="Branch: main.",
            initialUserMessage="Start.",
            sessionTitle="work",
            watchPaths=["/a/.envrc"],
            reloadSkills=True,
        ),
    ),
    (SubagentStartReply.context("ctx"), "SubagentStart", specific("SubagentStart", additionalContext="ctx")),
    (StopReply.block("No."), "Stop", BLOCK),
    (StopReply.context("Run the tests."), "Stop", specific("Stop", additionalContext="Run the tests.")),
    (SubagentStopReply.block("No."), "SubagentStop", BLOCK),
    (SubagentStopReply.context("ctx"), "SubagentStop", specific("SubagentStop", additionalContext="ctx")),
    (TaskCreatedReply.block("No."), "TaskCreated", BLOCK),
    (
        TaskCompletedReply.block("Tests fail.").with_halt("Stop."),
        "TaskCompleted",
        {"continue": False, "stopReason": "Stop."},
    ),
    (TeammateIdleReply().with_halt(), "TeammateIdle", {"continue": False}),
    (MessageDisplayReply.display("plain"), "MessageDisplay", specific("MessageDisplay", displayContent="plain")),
    (ConfigChangeReply.block(), "ConfigChange", {"decision": "block"}),
    (CwdChangedReply.watch([]), "CwdChanged", specific("CwdChanged", watchPaths=[])),
    (FileChangedReply.watch(["/a/.env"]), "FileChanged", specific("FileChanged", watchPaths=["/a/.env"])),
    (PreCompactReply.block("No."), "PreCompact", BLOCK),
    (PreModelSwitchReply.allow(), "PreModelSwitch", specific("PreModelSwitch", permissionDecision="allow")),
    (
        PreModelSwitchReply.deny("Retired model."),
        "PreModelSwitch",
        specific("PreModelSwitch", permissionDecision="deny", permissionDecisionReason="Retired model."),
    ),
    (
        PreModelSwitchReply.ask("Re-sends 180k tokens."),
        "PreModelSwitch",
        specific("PreModelSwitch", permissionDecision="ask", permissionDecisionReason="Re-sends 180k tokens."),
    ),
    (
        PostModelSwitchReply.context("ctx"),
        "PostModelSwitch",
        specific("PostModelSwitch", additionalContext="ctx"),
    ),
    (
        ElicitationReply.accept({"project_key": "DOCS"}),
        "Elicitation",
        specific("Elicitation", action="accept", content={"project_key": "DOCS"}),
    ),
    (ElicitationReply.decline(), "Elicitation", specific("Elicitation", action="decline")),
    (ElicitationResultReply.cancel(), "ElicitationResult", specific("ElicitationResult", action="cancel")),
    (
        HookReply(system_message="Careful.", suppress_output=True, terminal_sequence="\x07").with_halt("Build failed."),
        "Notification",
        {
            "continue": False,
            "stopReason": "Build failed.",
            "suppressOutput": True,
            "systemMessage": "Careful.",
            "terminalSequence": "\x07",
        },
    ),
    (HookReply(), "SessionEnd", {}),
]


@pytest.mark.parametrize(("reply", "event", "expected"), GOLDEN, ids=[f"{e}-{i}" for i, (_, e, _) in enumerate(GOLDEN)])
def test_golden_json(reply: HookReply, event: str, expected: dict[str, Any]) -> None:
    assert reply.to_json() == expected
    assert reply.EVENT in (None, event)
    assert problems(reply.to_json(), event) == []


def test_the_schema_check_catches_what_claude_code_rejects() -> None:
    assert problems(specific("TaskCreated", additionalContext="x"), "TaskCreated") == [
        "no hookSpecificOutput variant for 'TaskCreated'"
    ]
    assert problems(specific("Stop", additionalContext="x"), "SubagentStop") == [
        "hookEventName 'Stop' differs from the event 'SubagentStop'"
    ]
    assert problems({"decision": "deny"}, "PostToolUse") == ["bad value for decision: 'deny'"]


def test_emit_writes_one_compact_line_and_returns_the_exit_code() -> None:
    out, err = io.StringIO(), io.StringIO()
    assert PreToolUseReply.deny("Grüße").emit(out, err) == 0
    assert out.getvalue() == (
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",'
        '"permissionDecisionReason":"Gr\\u00fc\\u00dfe"}}\n'
    )
    assert err.getvalue() == ""


def test_an_empty_reply_writes_nothing() -> None:
    out = io.StringIO()
    assert HookReply().emit(out, io.StringIO()) == 0
    assert StopReply().emit(out, io.StringIO()) == 0
    assert out.getvalue() == ""


def test_exit_code_and_plain_text_replies() -> None:
    assert TaskCompletedReply.block("Tests fail.").render() == HookResponse(stderr="Tests fail.\n", exit_code=2)
    assert TeammateIdleReply.keep_working("Build first.").render() == HookResponse(stderr="Build first.\n", exit_code=2)
    assert WorktreeRemoveReply.failed("Uncommitted work.").render() == HookResponse(
        stderr="Uncommitted work.\n", exit_code=1
    )
    assert WorktreeRemoveReply().render() == HookResponse()
    assert WorktreeCreateReply(path="/w/feature").render() == HookResponse(stdout="/w/feature\n")
    assert PreCompactReply.instructions("Keep the test names.").render() == HookResponse(stdout="Keep the test names.")
    assert PreCompactReply.block("No.").render().stdout == '{"decision":"block","reason":"No."}\n'


def test_raw_reply_merges_the_common_fields() -> None:
    reply = RawReply(payload={"hookSpecificOutput": {"hookEventName": "Stop", "futureField": 1}})
    assert reply.with_system_message("hi").to_json() == {
        "hookSpecificOutput": {"hookEventName": "Stop", "futureField": 1},
        "systemMessage": "hi",
    }
    assert reply.event_name == "Stop"
    assert RawReply(payload={}).event_name is None


def test_halt_without_reason() -> None:
    assert HookReply(halt=Halt()).to_json() == {"continue": False}


@pytest.mark.parametrize(
    "build",
    [
        lambda: PreToolUseReply.deny(""),
        lambda: PreToolUseReply(decision="deny", reason="r", updated_input={}),
        lambda: PreToolUseReply(decision="defer", updated_input={"a": 1}),
        lambda: PreToolUseReply(decision="defer", additional_context="x"),
        lambda: PermissionRequestReply(behavior="allow", message="m"),
        lambda: PermissionRequestReply(behavior="deny", updated_input={}),
        lambda: PostToolUseReply.block(""),
        lambda: PostToolUseReply.replace_output(None),
        lambda: UserPromptSubmitReply(suppress_original_prompt=True),
        lambda: UserPromptExpansionReply(suppress_original_prompt=True),
        lambda: StopReply.block(""),
        lambda: TaskCompletedReply.block(""),
        lambda: TeammateIdleReply.keep_working(""),
        lambda: PreModelSwitchReply(decision="deny"),
        lambda: PreCompactReply(block_reason="a", instructions_text="b"),
        lambda: PreCompactReply.instructions("x").with_system_message("s"),
        lambda: WorktreeCreateReply(path="/w", system_message="s"),
        lambda: ElicitationReply(action="decline", content={}),
    ],
)
def test_combinations_claude_code_rejects_or_ignores_raise(build: Any) -> None:
    with pytest.raises(ValueError):
        build()
