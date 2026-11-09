"""The hook output schema of Claude Code 2.1.319, transcribed from its zod definition (``OC`` in the bundle).

Claude Code validates a hook's JSON stdout against this schema. ``hookSpecificOutput`` is a union keyed by
``hookEventName``: an event without a variant here cannot carry one, and a reply that tries makes the whole hook run a
non-blocking error. Unknown keys are dropped by Claude Code; :func:`problems` reports them anyway, because a reply this
library builds should never contain one. Refresh this file from the bundle when the live contract test moves to a
newer Claude Code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

Check = Callable[[Any], bool]


def _str(value: Any) -> bool:
    return isinstance(value, str)


def _bool(value: Any) -> bool:
    return isinstance(value, bool)


def _object(value: Any) -> bool:
    return isinstance(value, dict)


def _any(value: Any) -> bool:
    return True


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _enum(*values: str) -> Check:
    return lambda value: value in values


def _permission_request_decision(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("behavior") == "allow":
        allowed = {"behavior", "updatedInput", "updatedPermissions"}
        return (
            set(value) <= allowed
            and _object(value.get("updatedInput", {}))
            and isinstance(value.get("updatedPermissions", []), list)
        )
    if value.get("behavior") == "deny":
        return set(value) <= {"behavior", "message", "interrupt"}
    return False


TOP_LEVEL: Mapping[str, Check] = {
    "continue": _bool,
    "suppressOutput": _bool,
    "stopReason": _str,
    "decision": _enum("approve", "block"),
    "reason": _str,
    "systemMessage": _str,
    "terminalSequence": _str,
}

_CONTEXT = {"additionalContext": _str}

VARIANTS: Mapping[str, Mapping[str, Check]] = {
    "PreToolUse": {
        "permissionDecision": _enum("allow", "deny", "ask", "defer"),
        "permissionDecisionReason": _str,
        "updatedInput": _object,
        **_CONTEXT,
    },
    "UserPromptSubmit": {**_CONTEXT, "sessionTitle": _str, "suppressOriginalPrompt": _bool},
    "UserPromptExpansion": {**_CONTEXT, "suppressOriginalPrompt": _bool},
    "SessionStart": {
        **_CONTEXT,
        "initialUserMessage": _str,
        "sessionTitle": _str,
        "watchPaths": _strings,
        "reloadSkills": _bool,
    },
    "Setup": _CONTEXT,
    "PreModelSwitch": {"permissionDecision": _enum("allow", "deny", "ask"), "permissionDecisionReason": _str},
    "PostModelSwitch": _CONTEXT,
    "SubagentStart": _CONTEXT,
    "PostToolUse": {
        **_CONTEXT,
        "classifierContext": _str,
        "updatedToolOutput": _any,
        "updatedMCPToolOutput": _any,
    },
    "PostToolUseFailure": _CONTEXT,
    "PostToolBatch": _CONTEXT,
    "Stop": _CONTEXT,
    "SubagentStop": _CONTEXT,
    "PermissionDenied": {"retry": _bool},
    "Notification": _CONTEXT,
    "PermissionRequest": {"decision": _permission_request_decision},
    "Elicitation": {"action": _enum("accept", "decline", "cancel"), "content": _object},
    "ElicitationResult": {"action": _enum("accept", "decline", "cancel"), "content": _object},
    "CwdChanged": {"watchPaths": _strings},
    "FileChanged": {"watchPaths": _strings},
    "WorktreeCreate": {"worktreePath": _str},
    "MessageDisplay": {"displayContent": _str},
}

REQUIRED: Mapping[str, frozenset[str]] = {"PermissionRequest": frozenset({"decision"})}


def problems(output: Mapping[str, Any], event: str) -> list[str]:
    """Return why ``output``, the JSON a hook for ``event`` printed, would not pass Claude Code's checks."""
    found = []
    for key, value in output.items():
        if key == "hookSpecificOutput":
            continue
        if key not in TOP_LEVEL:
            found.append(f"unknown top-level key {key}")
        elif not TOP_LEVEL[key](value):
            found.append(f"bad value for {key}: {value!r}")
    specific = output.get("hookSpecificOutput")
    if specific is None:
        return found
    if not isinstance(specific, dict):
        return [*found, "hookSpecificOutput is not an object"]
    name = specific.get("hookEventName")
    if name != event:
        found.append(f"hookEventName {name!r} differs from the event {event!r}")
    variant = VARIANTS.get(str(name))
    if variant is None:
        return [*found, f"no hookSpecificOutput variant for {name!r}"]
    for key, value in specific.items():
        if key == "hookEventName":
            continue
        if key not in variant:
            found.append(f"unknown hookSpecificOutput key {key}")
        elif not variant[key](value):
            found.append(f"bad value for hookSpecificOutput.{key}: {value!r}")
    missing = REQUIRED.get(str(name), frozenset()) - set(specific)
    found += [f"missing hookSpecificOutput.{key}" for key in sorted(missing)]
    return found
