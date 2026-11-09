"""Frames of Claude Code's cross-session messaging protocol (peer protocol 1).

A frame is one JSON object on one ``\\n``-terminated line. The receiver never answers on the same connection.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from typing import Any, Literal

from cc_blackbox.errors import MessageTooLarge

__all__ = [
    "ENVELOPE_TAG",
    "MAX_LINE_LENGTH",
    "Priority",
    "build_envelope",
    "build_rename_frame",
    "build_user_frame",
    "encode_frame",
    "percent_encode_address",
]

Priority = Literal["now", "next", "later"]
"""Where the receiver queues a message. ``now`` jumps ahead of queued work, ``later`` waits behind it."""

MAX_LINE_LENGTH = 1_048_576
"""The receiver drops the connection when one line exceeds this many UTF-16 code units."""

ENVELOPE_TAG = "cross-session-message"

_PRIORITIES = ("now", "next", "later")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_ADDRESS = re.compile(r"[A-Za-z0-9%:_/.\\-]+")
_ADDRESS_SAFE = re.compile(r"[A-Za-z0-9:_/.\\-]")
# The receiver escapes an opener or closer of these tags inside a peer message body, so a body can never end the
# envelope early or open a nested one. ASCII only: Claude Code also catches Unicode look-alikes of "<", "/" and of
# the tag names, and escapes those on its side.
_NESTED_TAG = re.compile(
    r"<(?!\\)(?=[^A-Za-z0-9_\-<>]*(?:cross-session-message|teammate-message|agent-message)(?:[^A-Za-z0-9_\-]|$))",
    re.IGNORECASE,
)
_NAME_REMOVED_CATEGORIES = ("Cf", "Cc", "Cs", "Zl", "Zp")
_NAME_MAX_CODE_POINTS = 64
_JS_TRIM = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"


def percent_encode_address(value: str) -> str:
    """Percent-encode ``value`` for the ``from`` attribute the way Claude Code encodes socket paths.

    Every character outside ``[A-Za-z0-9:_/.\\-]`` becomes its UTF-8 bytes as uppercase ``%XX``.
    """
    return "".join(
        ch if _ADDRESS_SAFE.fullmatch(ch) else "".join(f"%{b:02X}" for b in ch.encode("utf-8", "surrogatepass"))
        for ch in value
    )


def _envelope_name(name: str) -> str:
    # Claude Code's sanitizer: drop `"<>`, drop format, control, surrogate and line/paragraph separator characters,
    # trim, cap at 64 code points. Claude Code appends an ellipsis after 64 code points, which the receiver's own
    # round-trip check then rejects; cutting to 63 plus the ellipsis keeps the name within the limit so it survives.
    text = re.sub(r'["<>]', "", name)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in _NAME_REMOVED_CATEGORIES).strip(_JS_TRIM)
    if len(text) > _NAME_MAX_CODE_POINTS:
        text = text[: _NAME_MAX_CODE_POINTS - 1] + "\u2026"
    return text


def escape_envelope_body(body: str) -> str:
    """Escape every ``<cross-session-message``, ``<teammate-message`` and ``<agent-message`` opener or closer in
    ``body`` as ``<\\...``, the way Claude Code's own sender does. Already escaped text is left alone."""
    return _NESTED_TAG.sub("<\\\\", body)


def build_envelope(body: str, *, from_address: str, from_name: str | None = None) -> str:
    """Wrap ``body`` in a ``<cross-session-message>`` envelope, byte for byte as Claude Code's own sender does.

    The receiver's terminal then shows the message as a peer message under ``from_name``, and the model sees
    ``from_address`` as the address to reply to. ``from_address`` must be a ``scheme:value`` address made of
    ``[A-Za-z0-9%:_/.\\-]``; :func:`percent_encode_address` makes any value fit. Use ``did:<label>`` for a sender that
    cannot receive replies, and never the address of another session: the receiver does not verify it. ``from_name``
    is cleaned the way Claude Code cleans it and is left out when nothing remains.

    The ``from-session``, ``hop-chain`` and ``from-mode`` attributes are never written. ``from-mode`` in particular
    would be a claim about the sender's permission mode.

    Raises:
        ValueError: ``from_address`` contains a character the receiver does not accept.
    """
    if not _ADDRESS.fullmatch(from_address):
        raise ValueError(
            f"The envelope address {from_address!r} may contain only letters, digits and %:_/.\\- characters. "
            "Use percent_encode_address() on the variable part."
        )
    attributes = f' from="{from_address}"'
    name = _envelope_name(from_name) if from_name is not None else ""
    if name:
        attributes += f' from-name="{name}"'
    return f"<{ENVELOPE_TAG}{attributes}>\n{escape_envelope_body(body)}\n</{ENVELOPE_TAG}>"


def build_user_frame(
    content: str, *, sender: str, priority: Priority = "next", msg_id: str | None = None
) -> dict[str, Any]:
    """Build the frame that delivers ``content`` as a peer message.

    ``sender`` goes into the JSON ``from`` field. The receiver never shows it to the model, but uses it as the key of
    its rate limit and its 30-second duplicate check, and shows it to the user when it holds the message for approval.
    ``msg_id`` must be a UUID; the receiver ignores any other value. It defaults to a random UUID.

    The frame never carries ``session_id``: a value that differs from the receiver's makes it drop the message
    without telling anyone.

    Raises:
        ValueError: ``content`` or ``sender`` is empty, or ``priority`` or ``msg_id`` is not valid.
    """
    if not content:
        raise ValueError("The message content is empty. The receiver drops empty messages.")
    if not sender:
        raise ValueError("The sender label is empty. Pass a stable label that identifies the sending tool.")
    if priority not in _PRIORITIES:
        raise ValueError(f"The priority {priority!r} is not one of now, next or later.")
    if msg_id is None:
        msg_id = str(uuid.uuid4())
    elif not _UUID.fullmatch(msg_id):
        raise ValueError(f"The msg_id {msg_id!r} is not a UUID such as {uuid.uuid4()}. The receiver ignores it.")
    return {
        "type": "user",
        "from": sender,
        "priority": priority,
        "msg_id": msg_id,
        "message": {"role": "user", "content": content},
    }


def build_rename_frame(name: str) -> dict[str, Any]:
    """Build the control frame that renames the receiving session to ``name``.

    Raises:
        ValueError: ``name`` is empty.
    """
    if not name:
        raise ValueError("The new session name is empty.")
    return {"type": "control", "action": "rename", "name": name}


def encode_frame(frame: dict[str, Any]) -> bytes:
    """Serialise ``frame`` as one UTF-8 JSON line, newline included.

    Raises:
        MessageTooLarge: the line exceeds :data:`MAX_LINE_LENGTH`, so the receiver would drop it.
    """
    line = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    length = len(line.encode("utf-16-le", "surrogatepass")) // 2 + 1
    if length > MAX_LINE_LENGTH:
        raise MessageTooLarge(
            f"The frame is {length} characters long and the receiver accepts at most {MAX_LINE_LENGTH}. "
            "Shorten the message or split it."
        )
    return (line + "\n").encode("utf-8", "surrogatepass")
