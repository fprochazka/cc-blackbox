"""Which transcript records are prompts, and who sent them."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from cc_blackbox.transcripts.entries import AttachmentEntry, Entry, ToolResultBlock, UserEntry, parse_timestamp
from cc_blackbox.transcripts.markup import UserPart, parse_user_markup

__all__ = ["Prompt", "PromptOrigin", "prompt_from", "prompts"]

PromptOrigin = Literal["human", "peer", "task-notification", "coordinator"]
"""The origins Claude Code 2.1 writes. :attr:`Prompt.origin` keeps any other string as it is."""

# Before Claude Code wrote `origin`, only the opening tag of the text told an injected message from a typed one.
_LEGACY_ORIGIN_PREFIXES = (
    ("<task-notification", "task-notification"),
    ("<cross-session-message", "peer"),
)

_QUEUED_MODES = ("prompt", "task-notification")


@dataclass(frozen=True, slots=True)
class Prompt:
    """A message that started or steered a turn: typed by the user, sent by another session, or a task notification."""

    text: str
    parts: tuple[UserPart, ...]
    """:attr:`text` parsed with :func:`~cc_blackbox.parse_user_markup`."""
    origin: str
    """See :data:`PromptOrigin`."""
    queued: bool
    """``True`` for a prompt that arrived while Claude was working. Claude Code records those as ``queued_command``
    attachments, not as user records."""
    timestamp: datetime | None
    uuid: str | None
    entry: Entry


def _legacy_origin(text: str) -> str:
    head = text.lstrip()
    for prefix, origin in _LEGACY_ORIGIN_PREFIXES:
        if head.startswith(prefix):
            return origin
    return "human"


def prompt_from(entry: Entry) -> Prompt | None:
    """Return the prompt ``entry`` carries, or ``None`` when it is not one.

    A ``user`` record is a prompt unless it carries tool results or a compaction summary. Its origin is ``origin.kind``
    when the record has one; Claude Code marks peer messages as meta, so a meta record with an origin still counts.
    Without ``origin``, a meta record is an injection (a skill expansion, a command echo) and not a prompt, and the
    opening tag of the text tells task notifications and peer messages from typed prompts. A ``queued_command``
    attachment in ``prompt`` or ``task-notification`` mode is a prompt with ``queued`` set.
    """
    if isinstance(entry, UserEntry):
        if entry.is_compact_summary or any(isinstance(block, ToolResultBlock) for block in entry.content):
            return None
        text = entry.plain_text
        if not text.strip():
            return None
        if entry.origin is not None:
            origin = entry.origin
        elif entry.is_meta:
            return None
        else:
            origin = _legacy_origin(text)
        return Prompt(text, tuple(parse_user_markup(text)), origin, False, entry.timestamp, entry.uuid, entry)

    if isinstance(entry, AttachmentEntry) and entry.attachment_type == "queued_command":
        payload = entry.attachment
        text_value = payload.get("prompt")
        mode = payload.get("commandMode")
        if mode not in _QUEUED_MODES or not isinstance(text_value, str) or not text_value.strip():
            return None
        raw_origin = payload.get("origin")
        kind = raw_origin.get("kind") if isinstance(raw_origin, dict) else None
        if isinstance(kind, str) and kind:
            origin = kind
        else:
            origin = "task-notification" if mode == "task-notification" else _legacy_origin(text_value)
        timestamp = parse_timestamp(payload.get("timestamp")) or entry.timestamp
        return Prompt(text_value, tuple(parse_user_markup(text_value)), origin, True, timestamp, entry.uuid, entry)

    return None


def prompts(entries: Iterable[Entry]) -> Iterator[Prompt]:
    """Yield every prompt in ``entries``, in file order. Reading needs only ``user`` and ``attachment`` records."""
    for entry in entries:
        prompt = prompt_from(entry)
        if prompt is not None:
            yield prompt
