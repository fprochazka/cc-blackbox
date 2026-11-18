"""How full a session's context window is, as its transcript records it."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from cc_blackbox.sessions._tail import records_backward
from cc_blackbox.transcripts.entries import parse_timestamp

__all__ = ["ContextWindow", "ContextWindowSizeSource", "context_window_size"]

ContextWindowSizeSource = Literal["model_tag", "model_table", "used_exceeds_table", "default", "env_disable_1m"]
"""Where :attr:`ContextWindow.size` came from.

``model_tag`` is the ``[1m]`` suffix of the model id. ``model_table`` is this package's table of models. ``default`` is
the 200k that Claude Code assumes for a model it does not know. ``used_exceeds_table`` means the used tokens were above
the table's size, so the window must be the 1M one. ``env_disable_1m`` is the 200k cap that
``CLAUDE_CODE_DISABLE_1M_CONTEXT`` puts on every model, which only :attr:`ActiveSession.context_window
<cc_blackbox.ActiveSession.context_window>` can see.
"""

_200K = 200_000
_1M = 1_000_000

# The table follows the Claude Code docs (https://code.claude.com/docs/en/model-config.md, the sentence "Fable models,
# Sonnet 5 and later, Haiku 5.5, and Opus 4.7 and later run with the 1M window by default") and the API docs
# (https://platform.claude.com/docs/en/build-with-claude/context-windows). When either changes, or a Claude Code release
# note says "Added Claude ... 1M context", update the table and the test that lists one model id per rule.
_FAMILY_VERSION = re.compile(r"claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?!\d)")
_OPUS_1M_SINCE_MINOR = 7  # Opus 4.7


def context_window_size(model: str | None) -> tuple[int, ContextWindowSizeSource]:
    """Return the context window size of a model id in tokens, with where it came from.

    A model id matches when it contains the API id, as Claude Code matches it, so provider ids such as
    ``us.anthropic.claude-opus-4-8`` or ``claude-sonnet-4-5@20250929`` resolve too. A family and version are parsed from
    the id, so a new model of a known family needs no table change.
    """
    if not model:
        return _200K, "default"
    lowered = model.lower()
    if lowered.endswith("[1m]"):
        return _1M, "model_tag"
    if "claude-fable" in lowered or "claude-mythos" in lowered:
        return _1M, "model_table"
    found = _FAMILY_VERSION.search(lowered)
    if found is not None:
        family, major, minor = found.group(1), int(found.group(2)), int(found.group(3) or 0)
        if major >= 5 or (family == "opus" and major == 4 and minor >= _OPUS_1M_SINCE_MINOR):
            return _1M, "model_table"
        return _200K, "model_table"
    if "claude-" in lowered:
        return _200K, "model_table"
    return _200K, "default"


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """How full the context window was at a session's newest model response, as Claude Code's status line shows it.

    The used tokens are the input, cache creation and cache read tokens of the newest response of the main
    conversation. Output tokens do not count, and neither do subagents, which have their own context. After a
    compaction, the session has no measured fill until it answers again: :attr:`used` is 0 and :attr:`compacted` is
    ``True``, like the status line's 0%.

    The transcript does not record the window size, so :attr:`size` comes from the model id; :attr:`size_source` says
    how. A provider that refuses the 1M window, or a fallback to a smaller model, does not show in the transcript.
    """

    size: int
    """The window size in tokens."""
    size_source: ContextWindowSizeSource
    used: int
    """Tokens in the context at the newest response; 0 when :attr:`compacted`."""
    compacted: bool
    """``True`` when the session compacted after its newest response and has not answered since."""
    percent: int
    """:attr:`used` as a percentage of :attr:`size`, rounded as Claude Code rounds it."""
    model: str | None
    """:attr:`requested_model`, else :attr:`response_model`. After a compaction, the model of the response before it."""
    requested_model: str | None
    """The model id Claude Code asked for, the only one that can carry the ``[1m]`` suffix."""
    response_model: str | None
    """The model id the API answered with."""
    measured_at: datetime | None
    """The timestamp of the record the value comes from: the newest response, or the compaction after it."""
    raw: Mapping[str, Any]
    """The ``usage`` of the newest response; empty when :attr:`compacted`."""

    def with_size(self, size: int, source: ContextWindowSizeSource) -> ContextWindow:
        """Return a copy with another window size, and :attr:`percent` computed again."""
        return dataclasses.replace(self, size=size, size_source=source, percent=_percent(self.used, size))


def _percent(used: int, size: int) -> int:
    # Claude Code rounds with Math.round, which rounds halves up, unlike Python's round().
    return int(used * 100 / size + 0.5) if size > 0 else 0


def _tokens(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _used_tokens(usage: Mapping[str, Any]) -> int:
    counted: Mapping[str, Any] = usage
    iterations = usage.get("iterations")
    if isinstance(iterations, list):
        # The last request of the response, leaving out the advisor's and the compaction's own requests.
        for item in reversed(iterations):
            if isinstance(item, Mapping) and item.get("type") not in ("advisor_message", "compaction"):
                counted = item
                break
    return sum(_tokens(counted, k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _is_response(record: Mapping[str, Any]) -> bool:
    # A progress record embeds whole assistant messages, so the top-level type must be checked.
    if record.get("type") != "assistant" or record.get("isSidechain") is True or record.get("isUnmetered") is True:
        return False
    message = record.get("message")
    if not isinstance(message, Mapping) or message.get("model") == "<synthetic>":
        return False
    if not isinstance(message.get("usage"), Mapping):
        return False
    content = message.get("content")
    # Claude Code's canned reply to a message that needs none is not a model response.
    canned = (
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], Mapping)
        and content[0].get("text") == "No response requested."
    )
    return not canned


def _is_compact_boundary(record: Mapping[str, Any]) -> bool:
    return record.get("type") == "system" and record.get("subtype") == "compact_boundary"


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _from_records(records: Iterator[dict[str, Any]]) -> ContextWindow | None:
    boundary: Mapping[str, Any] | None = None
    for record in records:
        if _is_compact_boundary(record):
            # Only the newest boundary matters; keep reading for the model of the response before it.
            boundary = boundary or record
            continue
        if not _is_response(record):
            continue
        message = record["message"]
        requested, responded = _str(record.get("requestedModel")), _str(message.get("model"))
        model = requested or responded
        size, source = context_window_size(model)
        usage = message["usage"]
        # A response above the table's size proves the larger window, also for the context after a compaction.
        if _used_tokens(usage) > size:
            size, source = _1M, "used_exceeds_table"
        compacted = boundary is not None
        used = 0 if compacted else _used_tokens(usage)
        raw: Mapping[str, Any] = MappingProxyType({} if compacted else dict(usage))
        measured_at = parse_timestamp((boundary or record).get("timestamp"))
        return ContextWindow(
            size=size,
            size_source=source,
            used=used,
            compacted=compacted,
            percent=_percent(used, size),
            model=model,
            requested_model=requested,
            response_model=responded,
            measured_at=measured_at,
            raw=raw,
        )
    if boundary is None:
        return None
    size, source = context_window_size(None)
    return ContextWindow(
        size=size,
        size_source=source,
        used=0,
        compacted=True,
        percent=0,
        model=None,
        requested_model=None,
        response_model=None,
        measured_at=parse_timestamp(boundary.get("timestamp")),
        raw=MappingProxyType({}),
    )


# Claude Code writes compact JSON and escapes quotes inside strings, so these match only a record's own keys. The loose
# b'"assistant"' would also match every "role":"assistant" of the user records' tool results and of progress records.
_NEEDLES = (b'"type":"assistant"', b'"subtype":"compact_boundary"')


def read_context_window(path: Path) -> ContextWindow | None:
    """Return the context fill of the transcript at ``path``, or ``None`` without a response or a compaction.

    Reads backwards from the end to the newest response. After a compaction it reads on to the response before the
    compaction, for the model. An unreadable file counts as one without either.
    """
    try:
        return _from_records(records_backward(path, _NEEDLES))
    except OSError:
        return None
