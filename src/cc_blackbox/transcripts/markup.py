"""The markup Claude Code embeds in the text of user messages.

A slash command or an inline shell command (``! cmd``) is not recorded as a structured record. It is a user message
whose text holds tags such as::

    <command-message>review</command-message><command-name>/review</command-name><command-args>42</command-args>
    <bash-input>git status</bash-input><bash-stdout>...</bash-stdout><bash-stderr></bash-stderr>
    <local-command-caveat>Caveat: ...</local-command-caveat>

:func:`parse_user_markup` turns such text into typed parts. Tags may be malformed, nested, empty or mixed with prose;
anything unrecognized stays plain text, and prose that merely contains angle brackets, like ``List<String>``, is kept
as it is. The parser never raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias

__all__ = ["ShellCommandPart", "SlashCommandPart", "TextPart", "UserPart", "parse_user_markup"]


@dataclass(frozen=True, slots=True)
class TextPart:
    """A run of plain user prose."""

    text: str


@dataclass(frozen=True, slots=True)
class ShellCommandPart:
    """An inline shell command the user ran (``<bash-input>`` + optional output)."""

    command: str
    stdout: str | None = None
    stderr: str | None = None


@dataclass(frozen=True, slots=True)
class SlashCommandPart:
    """A slash-command invocation (``<command-name>`` + optional ``<command-args>``)."""

    name: str
    args: str | None = None


UserPart: TypeAlias = TextPart | ShellCommandPart | SlashCommandPart

# Tags whose entire block (open->content->close) is machine noise that must never
# be rendered. Empty <bash-stderr></bash-stderr> is handled separately: an empty
# stderr on a command is dropped, a non-empty one is kept.
_NOISE_TAGS = (
    "system-reminder",
    "local-command-caveat",
    "local-command-stdout",
    "local-command-stderr",
    "command-message",
)

# Stray (unclosed / unpaired) markup fragments we recognize. Only these are
# stripped from prose, so legitimate prose containing angle brackets (generics,
# `<T>`, an HTML snippet in a bug report) is preserved verbatim.
_STRAY_TAG_RE = re.compile(
    r"</?(?:bash-input|bash-stdout|bash-stderr|command-name|command-args|"
    r"command-message|system-reminder|local-command-[\w-]*|persisted-output)\b[^>]*>",
    re.IGNORECASE,
)


def _block_re(tag: str) -> re.Pattern[str]:
    # Tag name is always a literal from our own list -- no untrusted interpolation.
    return re.compile(rf"<{tag}\b[^>]*>([\s\S]*?)</{tag}>", re.IGNORECASE)


def _first_block(text: str, tag: str) -> re.Match[str] | None:
    return _block_re(tag).search(text)


def _all_blocks(text: str, tag: str) -> list[re.Match[str]]:
    return list(_block_re(tag).finditer(text))


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]*>", " ", s)


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _remove_blocks(text: str, tags: tuple[str, ...]) -> str:
    out = text
    for tag in tags:
        out = re.sub(rf"<{tag}\b[^>]*>[\s\S]*?</{tag}>", " ", out, flags=re.IGNORECASE)
    return out


def _normalize_slash_name(raw: str) -> str:
    n = raw.strip().lstrip("/")
    return f"/{n}" if n else ""


def _append_prose(parts: list[UserPart], s: str) -> None:
    """Append a text part for ``s`` if it has meaningful (non-whitespace) content."""
    text = _STRAY_TAG_RE.sub(" ", s)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if text:
        parts.append(TextPart(text=text))


def parse_user_markup(raw: str | None) -> list[UserPart]:
    """Parse a raw user-message string into ordered, typed parts.

    Recognizes (in priority order, since these forms are mutually exclusive in
    practice per entry):

    * slash-command: ``<command-name>/NAME</command-name>`` (+ optional
      ``<command-args>``); ``<command-message>`` is ignored.
    * shell-command: ``<bash-input>CMD</bash-input>`` with inline
      ``<bash-stdout>``/``<bash-stderr>`` when present in the same entry.
    * output-only entry (``<bash-stdout>``/``<bash-stderr>`` without an input):
      a shell-command part with an empty command, so a cross-turn pairing pass
      can attach it to the preceding input. A standalone empty stderr-only entry
      yields no parts (pure noise).
    * noise tags -> dropped.
    * everything else -> plain text parts (prose preserved verbatim).

    Returns ``[]`` when the entry is only noise / empty.
    """
    if not raw or not raw.strip():
        return []

    cmd_name = _first_block(raw, "command-name")
    if cmd_name:
        name = _normalize_slash_name(_strip_tags(cmd_name.group(1)))
        if name:
            cmd_args = _first_block(raw, "command-args")
            args = _collapse(_strip_tags(cmd_args.group(1))) if cmd_args else ""
            return [SlashCommandPart(name=name, args=args or None)]
        # Empty name -> fall through to generic handling below.

    inputs = _all_blocks(raw, "bash-input")
    if inputs:
        stdout_m = _first_block(raw, "bash-stdout")
        stderr_m = _first_block(raw, "bash-stderr")
        out = stdout_m.group(1) if stdout_m else None
        err = stderr_m.group(1) if (stderr_m and stderr_m.group(1).strip()) else None

        parts: list[UserPart] = []
        rest = raw
        for i, blk in enumerate(inputs):
            rest = rest.replace(blk.group(0), " ")
            is_last = i == len(inputs) - 1
            parts.append(
                ShellCommandPart(
                    command=blk.group(1).strip(),
                    stdout=out if (is_last and out is not None) else None,
                    stderr=err if (is_last and err is not None) else None,
                )
            )
        rest = _remove_blocks(rest, ("bash-stdout", "bash-stderr", *_NOISE_TAGS))
        _append_prose(parts, rest)
        return parts

    out_only = _first_block(raw, "bash-stdout")
    err_only = _first_block(raw, "bash-stderr")
    if out_only or err_only:
        out = out_only.group(1) if out_only else None
        err = err_only.group(1) if (err_only and err_only.group(1).strip()) else None
        if out is None and err is None:
            return []  # empty stderr only -> noise
        return [ShellCommandPart(command="", stdout=out, stderr=err)]

    cleaned = _remove_blocks(raw, (*_NOISE_TAGS, "command-args"))
    parts = []
    _append_prose(parts, cleaned)
    return parts
