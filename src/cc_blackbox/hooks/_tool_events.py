"""Tool-call events: PreToolUse, PermissionRequest, PermissionDenied, PostToolUse, PostToolUseFailure, PostToolBatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

from cc_blackbox.hooks._base import FieldReader, HookInput

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


PARSERS: dict[str, Callable[[FieldReader], HookInput]] = {
    "PreToolUse": _pre_tool_use,
    "PermissionRequest": _permission_request,
    "PermissionDenied": _permission_denied,
    "PostToolUse": _post_tool_use,
    "PostToolUseFailure": _post_tool_use_failure,
    "PostToolBatch": _post_tool_batch,
}
