"""The tool events that fire on every tool call, and their replies: PreToolUse, PostToolUse, PostToolUseFailure.

The other tool events live in :mod:`cc_blackbox.hooks._rare_tool_events`, so a PreToolUse hook builds fewer classes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

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


def mcp_server(reader: FieldReader) -> McpServer | None:
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
        mcp_server=mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        **r.finish(),
    )


def _post_tool_use(r: FieldReader) -> PostToolUseInput:
    return PostToolUseInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=mcp_server(r),
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
        mcp_server=mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        error=r.text("error"),
        is_interrupt=r.flag("is_interrupt"),
        duration_ms=r.integer("duration_ms"),
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


PARSERS: dict[str, Callable[[FieldReader], HookInput]] = {
    "PreToolUse": _pre_tool_use,
    "PostToolUse": _post_tool_use,
    "PostToolUseFailure": _post_tool_use_failure,
}
