"""Tool-call events and their replies: PreToolUse, PermissionRequest, PermissionDenied, PostToolUse,
PostToolUseFailure, PostToolBatch."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from cc_blackbox.hooks._base import FieldReader, HookInput, HookReply, PermissionDecision, plain, require

if TYPE_CHECKING:
    from cc_blackbox.hooks._tool_inputs import ToolInputView

_EMPTY: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class McpServer:
    """The MCP server behind an MCP tool. Base trust decisions on ``source``, not on ``name``."""

    name: str | None
    source: str | None
    """Where the server's definition came from: ``plugin``, ``sdk``, or a scope such as ``user`` or ``project``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolHookInput(HookInput):
    """The fields of the events about one tool call."""

    tool_name: str | None
    tool_input: Mapping[str, Any]
    """The tool's arguments, read-only. Empty when the payload has none."""
    mcp_server: McpServer | None
    """Set for an MCP tool."""

    def typed_tool_input(self) -> ToolInputView | None:
        """Return a typed view of ``tool_input`` for a documented built-in tool.

        ``None`` for other tools, and when the input lacks the field the view is built around, such as a Bash input
        without a string ``command``.
        """
        from cc_blackbox.hooks._tool_inputs import view_for

        return view_for(self.tool_name, self.tool_input)

    def tool_input_with(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``tool_input`` with ``changes`` applied. ``updatedInput`` replaces the whole input, so start here."""
        return {**self.tool_input, **changes}


@dataclass(frozen=True, slots=True, kw_only=True)
class PreToolUseInput(ToolHookInput):
    """Before a tool call runs. The hook can allow, deny, ask, defer or rewrite it."""

    EVENT: ClassVar[str | None] = "PreToolUse"

    tool_use_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionRequestInput(ToolHookInput):
    """When Claude Code is about to show a permission prompt. Carries no ``tool_use_id``."""

    EVENT: ClassVar[str | None] = "PermissionRequest"

    permission_suggestions: tuple[Mapping[str, Any], ...]
    """The permission updates the prompt would offer, as Claude Code sends them; a hook can echo one back."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionDeniedInput(ToolHookInput):
    """After auto mode denied a tool call."""

    EVENT: ClassVar[str | None] = "PermissionDenied"

    tool_use_id: str | None
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolUseInput(ToolHookInput):
    """After a tool call succeeded."""

    EVENT: ClassVar[str | None] = "PostToolUse"

    tool_use_id: str | None
    tool_response: Any
    """The tool's structured output, whose shape depends on the tool."""
    duration_ms: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolUseFailureInput(ToolHookInput):
    """After a tool call failed while it ran."""

    EVENT: ClassVar[str | None] = "PostToolUseFailure"

    tool_use_id: str | None
    error: str | None
    """For shells, the first line is ``Exit code N``; treat the rest as display text."""
    is_interrupt: bool | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class BatchedToolCall:
    tool_name: str | None
    tool_input: Mapping[str, Any]
    tool_use_id: str | None
    tool_response: Any
    """The serialized ``tool_result`` content the model sees, not the structured output PostToolUse gets."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolBatchInput(HookInput):
    """After every tool call of a parallel batch resolved, before the next model request."""

    EVENT: ClassVar[str | None] = "PostToolBatch"

    tool_calls: tuple[BatchedToolCall, ...]


def _mcp_server(reader: FieldReader) -> McpServer | None:
    data = reader.obj("mcp_server")
    if data is None:
        return None
    name, source = data.get("name"), data.get("source")
    return McpServer(name if isinstance(name, str) else None, source if isinstance(source, str) else None)


def _pre_tool_use(r: FieldReader) -> PreToolUseInput:
    return PreToolUseInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=_mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        **r.finish(),
    )


def _permission_request(r: FieldReader) -> PermissionRequestInput:
    return PermissionRequestInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=_mcp_server(r),
        permission_suggestions=tuple(r.records("permission_suggestions")),
        **r.finish(),
    )


def _permission_denied(r: FieldReader) -> PermissionDeniedInput:
    return PermissionDeniedInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=_mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        reason=r.text("reason"),
        **r.finish(),
    )


def _post_tool_use(r: FieldReader) -> PostToolUseInput:
    return PostToolUseInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=_mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        tool_response=r.value("tool_response"),
        duration_ms=r.integer("duration_ms"),
        **r.finish(),
    )


def _post_tool_use_failure(r: FieldReader) -> PostToolUseFailureInput:
    return PostToolUseFailureInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=_mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        error=r.text("error"),
        is_interrupt=r.flag("is_interrupt"),
        duration_ms=r.integer("duration_ms"),
        **r.finish(),
    )


def _batched_call(data: Mapping[str, Any]) -> BatchedToolCall:
    name, tool_input, tool_use_id = data.get("tool_name"), data.get("tool_input"), data.get("tool_use_id")
    return BatchedToolCall(
        tool_name=name if isinstance(name, str) else None,
        tool_input=MappingProxyType(tool_input) if isinstance(tool_input, dict) else _EMPTY,
        tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
        tool_response=data.get("tool_response"),
    )


def _post_tool_batch(r: FieldReader) -> PostToolBatchInput:
    return PostToolBatchInput(
        **r.common(),
        tool_calls=tuple(_batched_call(call) for call in r.records("tool_calls")),
        **r.finish(),
    )


# Replies


@dataclass(frozen=True, slots=True, kw_only=True)
class PreToolUseReply(HookReply):
    """Allow, deny, ask about, defer or rewrite a tool call. Build one with the class methods.

    Printing nothing leaves the call to the normal permission flow; an explicit ``allow`` skips the user's prompt and
    overrides other hooks' asks, so return ``None`` from a handler that has no opinion. In auto mode Claude Code still
    sends an allowed call to its classifier.
    """

    EVENT: ClassVar[str | None] = "PreToolUse"

    decision: PermissionDecision | None = None
    reason: str | None = None
    """For ``deny``, what Claude reads; for ``ask``, what the user reads; for ``allow``, only the debug log."""
    updated_input: Mapping[str, Any] | None = None
    """Replaces the whole tool input. Start from :meth:`ToolHookInput.tool_input_with`."""
    additional_context: str | None = None

    def __post_init__(self) -> None:
        require(self.decision != "deny" or bool(self.reason), "A deny needs a reason: Claude reads it.")
        require(
            self.decision not in ("deny", "defer") or self.updated_input is None,
            f"A {self.decision} cannot rewrite the tool input.",
        )
        require(self.decision != "defer" or self.additional_context is None, "A defer ignores additional context.")

    @classmethod
    def allow(
        cls,
        *,
        reason: str | None = None,
        updated_input: Mapping[str, Any] | None = None,
        additional_context: str | None = None,
    ) -> PreToolUseReply:
        return cls(decision="allow", reason=reason, updated_input=updated_input, additional_context=additional_context)

    @classmethod
    def deny(cls, reason: str, *, additional_context: str | None = None) -> PreToolUseReply:
        return cls(decision="deny", reason=reason, additional_context=additional_context)

    @classmethod
    def ask(
        cls,
        reason: str | None = None,
        *,
        updated_input: Mapping[str, Any] | None = None,
        additional_context: str | None = None,
    ) -> PreToolUseReply:
        """Show the permission prompt. In a ``-p`` run nobody can answer it, so Claude Code denies the call."""
        return cls(decision="ask", reason=reason, updated_input=updated_input, additional_context=additional_context)

    @classmethod
    def defer(cls) -> PreToolUseReply:
        """End the turn so the caller of ``claude -p`` can resume the call later. Ignored in interactive sessions."""
        return cls(decision="defer")

    @classmethod
    def context(
        cls, additional_context: str | None = None, *, updated_input: Mapping[str, Any] | None = None
    ) -> PreToolUseReply:
        """Add context or rewrite the input without a decision. In auto mode, a rewrite without a decision can be
        denied."""
        return cls(additional_context=additional_context, updated_input=updated_input)

    def specific_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.decision is not None:
            out["permissionDecision"] = self.decision
        if self.reason is not None:
            out["permissionDecisionReason"] = self.reason
        if self.updated_input is not None:
            out["updatedInput"] = plain(self.updated_input)
        if self.additional_context is not None:
            out["additionalContext"] = self.additional_context
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionRequestReply(HookReply):
    """Answer a permission prompt for the user."""

    EVENT: ClassVar[str | None] = "PermissionRequest"

    behavior: Literal["allow", "deny"]
    updated_input: Mapping[str, Any] | None = None
    updated_permissions: tuple[Mapping[str, Any], ...] = ()
    """Permission update entries to apply, such as one of the input's ``permission_suggestions``."""
    message: str | None = None
    """For a deny: tells Claude why."""
    interrupt: bool = False
    """For a deny: also stop Claude."""

    def __post_init__(self) -> None:
        if self.behavior == "allow":
            require(self.message is None and not self.interrupt, "An allow takes no message and no interrupt.")
        else:
            require(self.updated_input is None and not self.updated_permissions, "A deny cannot change input or rules.")

    @classmethod
    def allow(
        cls, *, updated_input: Mapping[str, Any] | None = None, updated_permissions: Iterable[Mapping[str, Any]] = ()
    ) -> PermissionRequestReply:
        return cls(behavior="allow", updated_input=updated_input, updated_permissions=tuple(updated_permissions))

    @classmethod
    def deny(cls, message: str | None = None, *, interrupt: bool = False) -> PermissionRequestReply:
        return cls(behavior="deny", message=message, interrupt=interrupt)

    def specific_json(self) -> dict[str, Any]:
        decision: dict[str, Any] = {"behavior": self.behavior}
        if self.updated_input is not None:
            decision["updatedInput"] = plain(self.updated_input)
        if self.updated_permissions:
            decision["updatedPermissions"] = plain(self.updated_permissions)
        if self.message is not None:
            decision["message"] = self.message
        if self.interrupt:
            decision["interrupt"] = True
        return {"decision": decision}


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionDeniedReply(HookReply):
    """Tell the model it may retry a call auto mode denied. The denial itself stands."""

    EVENT: ClassVar[str | None] = "PermissionDenied"

    retry: bool = False

    @classmethod
    def allow_retry(cls) -> PermissionDeniedReply:
        return cls(retry=True)

    def specific_json(self) -> dict[str, Any]:
        return {"retry": True} if self.retry else {}


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolUseReply(HookReply):
    """Feedback after a tool ran. The tool already took effect; a block only adds the reason next to its result."""

    EVENT: ClassVar[str | None] = "PostToolUse"

    block_reason: str | None = None
    additional_context: str | None = None
    classifier_context: str | None = None
    """A short note about this call's result for the auto-mode classifier, not for Claude."""
    updated_tool_output: Any = None
    """Replaces what Claude sees. It must match the tool's output shape, or Claude Code ignores it."""

    @classmethod
    def block(cls, reason: str, *, additional_context: str | None = None) -> PostToolUseReply:
        require(bool(reason), "A block needs a reason.")
        return cls(block_reason=reason, additional_context=additional_context)

    @classmethod
    def context(cls, additional_context: str, *, classifier_context: str | None = None) -> PostToolUseReply:
        return cls(additional_context=additional_context, classifier_context=classifier_context)

    @classmethod
    def replace_output(
        cls, output: Any, *, classifier_context: str | None = None, additional_context: str | None = None
    ) -> PostToolUseReply:
        require(output is not None, "The replacement output cannot be None.")
        return cls(
            updated_tool_output=output, classifier_context=classifier_context, additional_context=additional_context
        )

    def top_level_json(self) -> dict[str, Any]:
        return {} if self.block_reason is None else {"decision": "block", "reason": self.block_reason}

    def specific_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.additional_context is not None:
            out["additionalContext"] = self.additional_context
        if self.classifier_context is not None:
            out["classifierContext"] = self.classifier_context
        if self.updated_tool_output is not None:
            out["updatedToolOutput"] = plain(self.updated_tool_output)
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolUseFailureReply(HookReply):
    EVENT: ClassVar[str | None] = "PostToolUseFailure"

    additional_context: str | None = None

    @classmethod
    def context(cls, additional_context: str) -> PostToolUseFailureReply:
        return cls(additional_context=additional_context)

    def specific_json(self) -> dict[str, Any]:
        return {} if self.additional_context is None else {"additionalContext": self.additional_context}


@dataclass(frozen=True, slots=True, kw_only=True)
class PostToolBatchReply(HookReply):
    EVENT: ClassVar[str | None] = "PostToolBatch"

    block_reason: str | None = None
    """Stops the agentic loop before the next model request."""
    additional_context: str | None = None

    @classmethod
    def block(cls, reason: str) -> PostToolBatchReply:
        require(bool(reason), "A block needs a reason.")
        return cls(block_reason=reason)

    @classmethod
    def context(cls, additional_context: str) -> PostToolBatchReply:
        return cls(additional_context=additional_context)

    def top_level_json(self) -> dict[str, Any]:
        return {} if self.block_reason is None else {"decision": "block", "reason": self.block_reason}

    def specific_json(self) -> dict[str, Any]:
        return {} if self.additional_context is None else {"additionalContext": self.additional_context}


PARSERS: dict[str, Callable[[FieldReader], HookInput]] = {
    "PreToolUse": _pre_tool_use,
    "PermissionRequest": _permission_request,
    "PermissionDenied": _permission_denied,
    "PostToolUse": _post_tool_use,
    "PostToolUseFailure": _post_tool_use_failure,
    "PostToolBatch": _post_tool_batch,
}
