"""Typed transcript records.

One JSONL line is one record. Claude Code changes the record set every few releases, so the parser keeps every record:
the types it models get their own class, every other type is a plain :class:`Entry`, and ``raw`` always holds the
whole record. A field with an unexpected type becomes ``None`` and is counted in the diagnostics; nothing here raises
on content.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, TypeAlias

__all__ = [
    "AssistantEntry",
    "AttachmentEntry",
    "ContentBlock",
    "Entry",
    "ImageBlock",
    "KNOWN_ENTRY_TYPES",
    "ReadDiagnostics",
    "SystemEntry",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "UnknownBlock",
    "Usage",
    "UserEntry",
    "parse_entry",
]

KNOWN_ENTRY_TYPES: frozenset[str] = frozenset(
    {
        "user",
        "assistant",
        "system",
        "attachment",
        "summary",
        "ai-title",
        "custom-title",
        "last-prompt",
        "queue-operation",
        "file-history-snapshot",
        "file-history-delta",
        "permission-mode",
        "mode",
        "bridge-session",
        "atis-latch",
        "agent-name",
        "api-request",
        "api-request-blob",
        "api-request-shape",
        "pr-link",
        "session-owner",
        "history-suppression",
        "cost-state",
        "tools-used",
        "relocated",
        "frame-link",
        "continued-in",
        "progress",
    }
)
"""The record types seen in Claude Code 2.1.x transcripts. Information only: an unknown type is still read."""

_EMPTY: Mapping[str, Any] = MappingProxyType({})


@dataclass(slots=True)
class ReadDiagnostics:
    """What a read skipped or did not understand. Pass one to the reader and inspect it afterwards."""

    lines: int = 0
    malformed_lines: int = 0
    """Lines that are not a JSON object with a string ``type``."""
    unknown_types: Counter[str] = field(default_factory=Counter)
    """Record types outside :data:`KNOWN_ENTRY_TYPES`. New types are additions and do not affect :attr:`aligned`."""
    unknown_block_types: Counter[str] = field(default_factory=Counter)
    mistyped_fields: Counter[str] = field(default_factory=Counter)
    """Fields this package models that held a value of an unexpected type, such as ``assistant.message.content``."""

    @property
    def aligned(self) -> bool:
        """``True`` when every field this package models had the shape it expects.

        This is the transcript counterpart of :func:`cc_blackbox.check_compatibility`: it checks what the records
        contain, never the Claude Code version that wrote them.
        """
        return not self.mistyped_fields


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    thinking: str
    """Often empty: Claude Code 2.1 keeps only the signature of most thinking blocks."""
    has_signature: bool


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str | None
    name: str | None
    input: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str | None
    text: str
    """The result as text: a string result as it is, or the text parts of a list result joined."""
    is_error: bool
    content: tuple[ContentBlock, ...]
    """The parts of a list result. Empty when the result is a string."""


@dataclass(frozen=True, slots=True)
class ImageBlock:
    media_type: str | None


@dataclass(frozen=True, slots=True)
class UnknownBlock:
    type: str | None
    raw: Mapping[str, Any]


ContentBlock: TypeAlias = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock | ImageBlock | UnknownBlock


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts of one assistant message. Claude Code repeats them on every line of a multi-block message."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Entry:
    """One transcript record. Records of a type this package does not model are plain instances of this class."""

    type: str
    uuid: str | None
    parent_uuid: str | None
    session_id: str | None
    timestamp: datetime | None
    cwd: str | None
    git_branch: str | None
    version: str | None
    is_sidechain: bool
    """``True`` on the records of a subagent transcript."""
    agent_id: str | None
    line_number: int
    """1-based line number in the file."""
    offset: int
    """Byte offset of the line in the file."""
    end_offset: int
    """Byte offset just after the line: where a reader that follows the file continues."""
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class UserEntry(Entry):
    """A ``user`` record: a prompt, an injected message, or the results of tool calls."""

    content: tuple[ContentBlock, ...]
    """The blocks of a list ``message.content``. Empty when the content is a string."""
    text: str | None
    """``message.content`` when it is a string."""
    is_meta: bool
    """Claude Code marks injected records, such as skill expansions and peer messages, as meta."""
    origin: str | None
    """``origin.kind``: ``human``, ``peer``, ``task-notification``, ``coordinator``. Older transcripts lack it."""
    prompt_source: str | None
    is_compact_summary: bool
    tool_use_result: Any
    """The record-level ``toolUseResult``: structured tool output the model never sees."""
    tool_denial: str | None
    """``toolDenialKind``, for example ``user-rejected``, when a tool call was denied."""

    @property
    def plain_text(self) -> str:
        """The string content, or the text blocks joined with newlines."""
        if self.text is not None:
            return self.text
        return "\n".join(block.text for block in self.content if isinstance(block, TextBlock))


@dataclass(frozen=True, slots=True)
class AssistantEntry(Entry):
    """An ``assistant`` record. Claude Code writes one record per content block of a message."""

    message_id: str | None
    model: str | None
    content: tuple[ContentBlock, ...]
    usage: Usage | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class SystemEntry(Entry):
    """A ``system`` record, such as ``turn_duration`` or ``compact_boundary``."""

    subtype: str | None
    content: str | None


@dataclass(frozen=True, slots=True)
class AttachmentEntry(Entry):
    """An ``attachment`` record: hook output, reminders, queued prompts and other context the model was shown."""

    attachment_type: str | None
    attachment: Mapping[str, Any]


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 transcript timestamp such as ``2026-11-01T09:00:00.000Z`` into an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _blocks(content: Any, where: str, diagnostics: ReadDiagnostics) -> tuple[ContentBlock, ...]:
    blocks: list[ContentBlock] = []
    for item in content:
        if isinstance(item, dict):
            blocks.append(_block(item, where, diagnostics))
        else:
            diagnostics.mistyped_fields[f"{where}[]"] += 1
    return tuple(blocks)


def _block(data: dict[str, Any], where: str, diagnostics: ReadDiagnostics) -> ContentBlock:
    block_type = data.get("type")
    if block_type == "text" and isinstance(data.get("text"), str):
        return TextBlock(data["text"])
    if block_type == "thinking":
        thinking = data.get("thinking")
        return ThinkingBlock(thinking if isinstance(thinking, str) else "", bool(data.get("signature")))
    if block_type == "tool_use":
        tool_input = data.get("input")
        return ToolUseBlock(
            _str(data, "id"),
            _str(data, "name"),
            MappingProxyType(tool_input) if isinstance(tool_input, dict) else _EMPTY,
        )
    if block_type == "tool_result":
        result = data.get("content")
        parts: tuple[ContentBlock, ...] = ()
        if isinstance(result, str):
            result_text = result
        elif isinstance(result, list):
            parts = _blocks(result, f"{where}.tool_result.content", diagnostics)
            result_text = "\n".join(part.text for part in parts if isinstance(part, TextBlock))
        else:
            result_text = ""
        return ToolResultBlock(_str(data, "tool_use_id"), result_text, data.get("is_error") is True, parts)
    if block_type == "image":
        source = data.get("source")
        return ImageBlock(_str(source, "media_type") if isinstance(source, dict) else None)
    diagnostics.unknown_block_types[str(block_type)] += 1
    return UnknownBlock(block_type if isinstance(block_type, str) else None, MappingProxyType(data))


def _message(data: Mapping[str, Any], record_type: str, diagnostics: ReadDiagnostics) -> Mapping[str, Any]:
    message = data.get("message")
    if isinstance(message, dict):
        return message
    diagnostics.mistyped_fields[f"{record_type}.message"] += 1
    return _EMPTY


def parse_entry(
    data: dict[str, Any],
    *,
    line_number: int = 0,
    offset: int = 0,
    end_offset: int = 0,
    diagnostics: ReadDiagnostics | None = None,
) -> Entry | None:
    """Turn one decoded JSON record into an :class:`Entry`, or return ``None`` when it has no string ``type``."""
    diag = diagnostics if diagnostics is not None else ReadDiagnostics()
    record_type = data.get("type")
    if not isinstance(record_type, str) or not record_type:
        diag.malformed_lines += 1
        return None
    if record_type not in KNOWN_ENTRY_TYPES:
        diag.unknown_types[record_type] += 1
    if "timestamp" in data and parse_timestamp(data["timestamp"]) is None and data["timestamp"] is not None:
        diag.mistyped_fields["timestamp"] += 1

    common: dict[str, Any] = {
        "type": record_type,
        "uuid": _str(data, "uuid"),
        "parent_uuid": _str(data, "parentUuid"),
        "session_id": _str(data, "sessionId"),
        "timestamp": parse_timestamp(data.get("timestamp")),
        "cwd": _str(data, "cwd"),
        "git_branch": _str(data, "gitBranch"),
        "version": _str(data, "version"),
        "is_sidechain": data.get("isSidechain") is True,
        "agent_id": _str(data, "agentId"),
        "line_number": line_number,
        "offset": offset,
        "end_offset": end_offset,
        "raw": MappingProxyType(data),
    }

    if record_type == "user":
        message = _message(data, "user", diag)
        content = message.get("content")
        blocks: tuple[ContentBlock, ...] = ()
        text: str | None = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            blocks = _blocks(content, "user.message.content", diag)
        elif message:
            diag.mistyped_fields["user.message.content"] += 1
        origin = data.get("origin")
        return UserEntry(
            **common,
            content=blocks,
            text=text,
            is_meta=data.get("isMeta") is True,
            origin=_str(origin, "kind") if isinstance(origin, dict) else None,
            prompt_source=_str(data, "promptSource"),
            is_compact_summary=data.get("isCompactSummary") is True,
            tool_use_result=data.get("toolUseResult"),
            tool_denial=_str(data, "toolDenialKind"),
        )

    if record_type == "assistant":
        message = _message(data, "assistant", diag)
        content = message.get("content")
        if isinstance(content, list):
            blocks = _blocks(content, "assistant.message.content", diag)
        else:
            blocks = ()
            if message:
                diag.mistyped_fields["assistant.message.content"] += 1
        raw_usage = message.get("usage")
        usage = (
            Usage(
                _int(raw_usage.get("input_tokens")),
                _int(raw_usage.get("output_tokens")),
                _int(raw_usage.get("cache_creation_input_tokens")),
                _int(raw_usage.get("cache_read_input_tokens")),
                MappingProxyType(raw_usage),
            )
            if isinstance(raw_usage, dict)
            else None
        )
        return AssistantEntry(
            **common,
            message_id=_str(message, "id"),
            model=_str(message, "model"),
            content=blocks,
            usage=usage,
            stop_reason=_str(message, "stop_reason"),
        )

    if record_type == "system":
        system_content = data.get("content")
        return SystemEntry(
            **common,
            subtype=_str(data, "subtype"),
            content=system_content if isinstance(system_content, str) else None,
        )

    if record_type == "attachment":
        payload = data.get("attachment")
        if not isinstance(payload, dict):
            diag.mistyped_fields["attachment.attachment"] += 1
            payload = {}
        return AttachmentEntry(
            **common,
            attachment_type=_str(payload, "type"),
            attachment=MappingProxyType(payload),
        )

    return Entry(**common)
