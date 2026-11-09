"""The runner: read the payload, call the handlers, write the reply, and decide what a failure does."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import IO, Any, Literal, NoReturn, TypeAlias, TypeVar, overload

from cc_blackbox.hooks._base import HookInput, HookReply, RawReply, read_hook_input, write_response

FailurePolicy: TypeAlias = Literal["pass", "ask", "block"]
"""What a hook does when its handler raises or its input cannot be read.

- ``pass`` prints nothing and exits 1. Claude Code then shows a ``hook error`` notice and goes ahead as if the hook had
  no opinion, unless the hook entry sets ``"onFailure": "block"``.
- ``block`` writes the event's blocking reply, such as a PreToolUse deny, where the event has one, and otherwise does
  what ``pass`` does. Stop, SubagentStop, TaskCompleted and TeammateIdle are never blocked on a failure: Claude would
  be sent back to work on a problem it cannot fix.
- ``ask`` sends PreToolUse and PreModelSwitch to the permission prompt, and does what ``block`` does elsewhere.
"""

_E = TypeVar("_E", bound=HookInput)

_Handler: TypeAlias = Callable[[Any], "HookReply | None"]


@overload
def handle_hook(
    *handlers: Callable[[HookInput], HookReply | None],
    expect: None = None,
    on_error: FailurePolicy = "pass",
    name: str | None = None,
    stdin: IO[str] | IO[bytes] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int: ...


@overload
def handle_hook(
    *handlers: Callable[[_E], HookReply | None],
    expect: type[_E],
    on_error: FailurePolicy = "pass",
    name: str | None = None,
    stdin: IO[str] | IO[bytes] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int: ...


def handle_hook(
    *handlers: _Handler,
    expect: type[HookInput] | None = None,
    on_error: FailurePolicy = "pass",
    name: str | None = None,
    stdin: IO[str] | IO[bytes] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Run one hook invocation and return the exit code; :func:`run_hook` exits with it.

    Reads the payload from ``stdin``, calls ``handlers`` in order until one returns a reply, and writes that reply.
    A handler that has no opinion returns ``None``; when every handler does, the hook prints nothing and exits 0.

    With ``expect``, a payload without ``hook_event_name`` is read as that event, and a payload of another event is
    answered with nothing (exit 0), because a matcher routed it here by mistake. ``name`` labels the stderr notes; it
    defaults to the program name. A failure, meaning an unreadable payload, a handler that raises, or a reply for
    another event, is handled by ``on_error``; see :data:`FailurePolicy`.
    """
    if not handlers:
        raise TypeError("handle_hook() needs at least one handler.")
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    label = name or os.path.basename(sys.argv[0] or "") or "hook"
    assumed = expect.EVENT if expect is not None else None
    source: Any = stdin if stdin is not None else sys.stdin

    isatty = getattr(source, "isatty", None)
    if callable(isatty) and isatty():
        err.write(f"{label} is a Claude Code hook: it reads the hook's JSON input on stdin.\n")
        return 1
    try:
        event = read_hook_input(source, event=assumed)
    except Exception as error:
        return _fail(label, error, assumed, on_error, out, err)

    if expect is not None and not isinstance(event, expect):
        err.write(f"{label} handles {assumed} but received {event.hook_event_name}; it has nothing to say.\n")
        return 0

    try:
        reply: HookReply | None = None
        for handler in handlers:
            reply = handler(event)
            if reply is not None:
                break
        if reply is None:
            return 0
        _check_reply(reply, event.hook_event_name)
        response = reply.render()
    except Exception as error:
        return _fail(label, error, event.hook_event_name, on_error, out, err)
    return write_response(response, out, err)


@overload
def run_hook(
    *handlers: Callable[[HookInput], HookReply | None],
    expect: None = None,
    on_error: FailurePolicy = "pass",
    name: str | None = None,
) -> NoReturn: ...


@overload
def run_hook(
    *handlers: Callable[[_E], HookReply | None],
    expect: type[_E],
    on_error: FailurePolicy = "pass",
    name: str | None = None,
) -> NoReturn: ...


def run_hook(
    *handlers: _Handler,
    expect: type[HookInput] | None = None,
    on_error: FailurePolicy = "pass",
    name: str | None = None,
) -> NoReturn:
    """Run :func:`handle_hook` on the process's stdin and stdout and exit with its exit code."""
    raise SystemExit(handle_hook(*handlers, expect=expect, on_error=on_error, name=name))


def _check_reply(reply: object, event_name: str) -> None:
    if not isinstance(reply, HookReply):
        raise TypeError(f"The handler returned {type(reply).__name__}, not a HookReply or None.")
    target = reply.event_name if isinstance(reply, RawReply) else reply.EVENT
    if target is not None and target != event_name:
        raise ValueError(f"The handler returned a reply for {target}, but the hook runs for {event_name}.")


def _fail(
    label: str, error: Exception, event_name: str | None, policy: FailurePolicy, out: IO[str], err: IO[str]
) -> int:
    import traceback

    summary = f"{type(error).__name__}: {error}"
    # Claude Code's notice shows the first stderr line, so it carries the summary; the traceback is for the debug log.
    err.write(f"{label} hook failed: {summary}\n")
    err.write("".join(traceback.format_exception(error)))
    reply = _failure_reply(event_name, policy, f"The {label} hook failed ({summary}), so it blocks this.")
    if reply is None:
        return 1
    return write_response(reply.render(), out, err)


def _failure_reply(event_name: str | None, policy: FailurePolicy, message: str) -> HookReply | None:
    if policy == "pass" or event_name is None:
        return None
    if event_name in ("PreToolUse", "PreModelSwitch"):
        from cc_blackbox.hooks import _session_events, _tool_events

        if event_name == "PreToolUse":
            tool = _tool_events.PreToolUseReply
            return tool.ask(message) if policy == "ask" else tool.deny(message)
        model = _session_events.PreModelSwitchReply
        return model.ask(message) if policy == "ask" else model.deny(message)
    if event_name in ("PermissionRequest", "PostToolBatch"):
        from cc_blackbox.hooks import _tool_events

        if event_name == "PermissionRequest":
            return _tool_events.PermissionRequestReply.deny(message)
        return _tool_events.PostToolBatchReply.block(message)
    if event_name in ("UserPromptSubmit", "UserPromptExpansion", "TaskCreated"):
        from cc_blackbox.hooks import _turn_events

        if event_name == "UserPromptSubmit":
            return _turn_events.UserPromptSubmitReply.block(message)
        if event_name == "UserPromptExpansion":
            return _turn_events.UserPromptExpansionReply.block(message)
        return _turn_events.TaskCreatedReply.block(message)
    if event_name in ("PreCompact", "ConfigChange", "Elicitation", "ElicitationResult"):
        from cc_blackbox.hooks import _session_events

        if event_name == "PreCompact":
            return _session_events.PreCompactReply.block(message)
        if event_name == "ConfigChange":
            return _session_events.ConfigChangeReply.block()
        if event_name == "Elicitation":
            return _session_events.ElicitationReply.decline()
        return _session_events.ElicitationResultReply.decline()
    return None
