from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from hook_payloads import payload

from cc_blackbox.hooks import (
    FailurePolicy,
    HookInput,
    HookReply,
    PreToolUseInput,
    PreToolUseReply,
    RawReply,
    ShellInput,
    StopReply,
    handle_hook,
)

HOOK_SCRIPT = textwrap.dedent(
    """
    import sys
    from cc_blackbox.hooks import PreToolUseInput, PreToolUseReply, ShellInput, run_hook

    def guard(event: PreToolUseInput) -> PreToolUseReply | None:
        view = event.typed_tool_input()
        if isinstance(view, ShellInput) and view.command.startswith("glab ci view"):
            return PreToolUseReply.deny("Use glab-pipeline inspect instead.")
        if isinstance(view, ShellInput) and view.command == "boom":
            raise RuntimeError("the rules file is broken")
        return None

    run_hook(guard, expect=PreToolUseInput, on_error=sys.argv[1], name="guard")
    """
)


def run_script(tmp_path: Path, stdin: str, policy: str = "pass") -> subprocess.CompletedProcess[str]:
    script = tmp_path / "guard.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), policy], input=stdin, capture_output=True, text=True, timeout=30
    )


def bash(command: str, **overrides: Any) -> str:
    return json.dumps(payload("PreToolUse", tool_input={"command": command}, **overrides))


def test_subprocess_deny(tmp_path: Path) -> None:
    result = run_script(tmp_path, bash("glab ci view 1000"))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Use glab-pipeline inspect instead.",
        }
    }


def test_subprocess_no_opinion_prints_nothing(tmp_path: Path) -> None:
    result = run_script(tmp_path, bash("git status"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_subprocess_partial_payload_is_read_as_the_expected_event(tmp_path: Path) -> None:
    result = run_script(tmp_path, json.dumps({"tool_name": "Bash", "tool_input": {"command": "glab ci view"}}))
    assert result.returncode == 0 and "deny" in result.stdout


def test_subprocess_crash_fails_open_with_exit_1(tmp_path: Path) -> None:
    result = run_script(tmp_path, bash("boom"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[0] == "guard hook failed: RuntimeError: the rules file is broken"
    assert "Traceback" in result.stderr


def test_subprocess_crash_fails_closed_when_asked(tmp_path: Path) -> None:
    result = run_script(tmp_path, bash("boom"), policy="block")
    assert result.returncode == 0
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert "guard hook failed (RuntimeError: the rules file is broken)" in specific["permissionDecisionReason"]

    result = run_script(tmp_path, bash("boom"), policy="ask")
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_subprocess_unreadable_input(tmp_path: Path) -> None:
    result = run_script(tmp_path, "not json")
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr.startswith("guard hook failed: HookInputError")
    # With expect=, the event is known even when the payload is not, so a fail-closed hook can still deny.
    result = run_script(tmp_path, "not json", policy="block")
    assert result.returncode == 0 and '"permissionDecision":"deny"' in result.stdout


def test_subprocess_other_event_is_ignored(tmp_path: Path) -> None:
    result = run_script(tmp_path, json.dumps(payload("Stop")), policy="block")
    assert (result.returncode, result.stdout) == (0, "")
    assert "handles PreToolUse but received Stop" in result.stderr


def run(*handlers: Any, data: str, policy: FailurePolicy = "pass", expect: Any = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = handle_hook(
        *handlers, expect=expect, on_error=policy, name="t", stdin=io.StringIO(data), stdout=out, stderr=err
    )
    return code, out.getvalue(), err.getvalue()


def test_handlers_run_in_order_until_one_replies() -> None:
    calls: list[str] = []

    def first(event: HookInput) -> HookReply | None:
        calls.append("first")
        return None

    def second(event: HookInput) -> HookReply | None:
        calls.append("second")
        return StopReply.block("Run the tests.")

    def third(event: HookInput) -> HookReply | None:
        calls.append("third")
        return None

    code, out, _ = run(first, second, third, data=json.dumps(payload("Stop")))
    assert code == 0 and json.loads(out) == {"decision": "block", "reason": "Run the tests."}
    assert calls == ["first", "second"]


def test_a_reply_for_another_event_is_a_failure() -> None:
    code, out, err = run(lambda e: StopReply.block("x"), data=bash("ls"), policy="block")
    assert code == 0 and '"permissionDecision":"deny"' in out
    assert "reply for Stop, but the hook runs for PreToolUse" in err.splitlines()[0]
    code, _, err = run(lambda e: RawReply(payload={"hookSpecificOutput": {"hookEventName": "Stop"}}), data=bash("ls"))
    assert code == 1 and "reply for Stop" in err


def test_a_reply_of_the_wrong_type_is_a_failure() -> None:
    code, out, err = run(lambda e: {"decision": "block"}, data=bash("ls"))
    assert (code, out) == (1, "")
    assert err.startswith("t hook failed: TypeError: The handler returned dict")


def test_a_generic_reply_fits_any_event() -> None:
    code, out, _ = run(lambda e: HookReply(system_message="hi"), data=json.dumps(payload("Notification")))
    assert code == 0 and json.loads(out) == {"systemMessage": "hi"}


@pytest.mark.parametrize(
    ("event", "policy", "expected"),
    [
        ("Stop", "block", None),
        ("SubagentStop", "ask", None),
        ("TaskCompleted", "block", None),
        ("PostToolUse", "block", None),
        ("UserPromptSubmit", "block", {"decision": "block"}),
        ("UserPromptSubmit", "ask", {"decision": "block"}),
        ("PermissionRequest", "block", {"hookSpecificOutput": {"decision": {"behavior": "deny"}}}),
        ("PreModelSwitch", "ask", {"hookSpecificOutput": {"permissionDecision": "ask"}}),
        ("ConfigChange", "block", {"decision": "block"}),
        ("Elicitation", "block", {"hookSpecificOutput": {"action": "decline"}}),
        ("PreCompact", "block", {"decision": "block"}),
        ("TaskCreated", "block", {"decision": "block"}),
        ("PostToolBatch", "block", {"decision": "block"}),
    ],
)
def test_failure_policy_per_event(event: str, policy: FailurePolicy, expected: dict[str, Any] | None) -> None:
    def crash(e: HookInput) -> HookReply | None:
        raise RuntimeError("bug")

    code, out, err = run(crash, data=json.dumps(payload(event)), policy=policy)
    assert err.startswith("t hook failed: RuntimeError: bug")
    if expected is None:
        assert (code, out) == (1, "")
        return
    assert code == 0
    written = json.loads(out)
    for key, value in expected.items():
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                actual = written[key][inner]
                assert actual == inner_value or (
                    isinstance(inner_value, dict) and inner_value.items() <= actual.items()
                )
        else:
            assert written[key] == value


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_terminal_on_stdin_gets_a_usage_note() -> None:
    err = io.StringIO()
    code = handle_hook(lambda e: None, name="t", stdin=_Tty(), stdout=io.StringIO(), stderr=err)
    assert code == 1 and "reads the hook's JSON input on stdin" in err.getvalue()


def test_handle_hook_needs_a_handler() -> None:
    with pytest.raises(TypeError):
        handle_hook(stdin=io.StringIO("{}"))


def test_typed_handler_with_expect() -> None:
    def guard(event: PreToolUseInput) -> PreToolUseReply | None:
        view = event.typed_tool_input()
        return PreToolUseReply.deny("no") if isinstance(view, ShellInput) and view.command == "rm" else None

    assert run(guard, data=bash("rm"), expect=PreToolUseInput)[1].startswith('{"hookSpecificOutput"')
    assert run(guard, data=bash("ls"), expect=PreToolUseInput) == (0, "", "")
