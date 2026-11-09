"""The tool events that do not fire on every tool call, and their replies: PermissionRequest, PermissionDenied,
PostToolBatch."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Literal

from cc_blackbox.hooks._base import FieldReader, HookInput, HookReply, plain, require
from cc_blackbox.hooks._tool_events import ToolHookInput, mcp_server

_EMPTY: Mapping[str, Any] = MappingProxyType({})


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


def _permission_request(r: FieldReader) -> PermissionRequestInput:
    return PermissionRequestInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=mcp_server(r),
        permission_suggestions=tuple(r.records("permission_suggestions")),
        **r.finish(),
    )


def _permission_denied(r: FieldReader) -> PermissionDeniedInput:
    return PermissionDeniedInput(
        **r.common(),
        tool_name=r.text("tool_name"),
        tool_input=r.obj("tool_input") or _EMPTY,
        mcp_server=mcp_server(r),
        tool_use_id=r.text("tool_use_id"),
        reason=r.text("reason"),
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
    "PermissionRequest": _permission_request,
    "PermissionDenied": _permission_denied,
    "PostToolBatch": _post_tool_batch,
}
