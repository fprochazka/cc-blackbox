"""What a session cost so far, as Claude Code records it in the transcript."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

__all__ = ["CostState", "ModelUsage"]


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """The tokens and the cost of one model, summed over the session. ``None`` when the record lacks the key."""

    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cache_read_input_tokens: int | None
    cache_creation_input_tokens: int | None
    web_search_requests: int | None
    cost_usd: float | None
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CostState:
    """The newest ``cost-state`` record of a transcript: the session's totals at the moment Claude Code wrote it.

    Claude Code writes the record when a session process exits, so for a running session it holds the totals as of its
    last exit, without the current run. In the one transcript checked, a resumed session kept its ``startTime`` and its
    totals grew across three runs, which suggests the totals cover every run. A field is ``None`` when the record lacks
    it or holds a value of an unexpected type. ``raw`` keeps the whole record.
    """

    total_cost_usd: float | None
    """The cost in US dollars, as Claude Code computes it from its own price list."""
    has_unknown_model_cost: bool | None
    """``True`` when a model without a known price was used, so :attr:`total_cost_usd` is too low."""
    started_at: datetime | None
    """When the session's first run started."""
    duration: timedelta | None
    """Wall-clock time the session's processes ran, summed over its runs."""
    api_duration: timedelta | None
    """Time spent waiting for API responses, retries included."""
    api_duration_without_retries: timedelta | None
    tool_duration: timedelta | None
    lines_added: int | None
    lines_removed: int | None
    model_usage: Mapping[str, ModelUsage]
    """Usage per model id, as Claude Code names it, for example ``claude-opus-5-5[1m]``."""
    raw: Mapping[str, Any]


def _int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _millis(data: Mapping[str, Any], key: str) -> timedelta | None:
    value = _float(data, key)
    return timedelta(milliseconds=value) if value is not None and value >= 0 else None


def _moment(data: Mapping[str, Any], key: str) -> datetime | None:
    value = _float(data, key)
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _model_usage(data: Mapping[str, Any]) -> ModelUsage:
    return ModelUsage(
        input_tokens=_int(data, "inputTokens"),
        output_tokens=_int(data, "outputTokens"),
        thinking_tokens=_int(data, "thinkingTokens"),
        cache_read_input_tokens=_int(data, "cacheReadInputTokens"),
        cache_creation_input_tokens=_int(data, "cacheCreationInputTokens"),
        web_search_requests=_int(data, "webSearchRequests"),
        cost_usd=_float(data, "costUSD"),
        raw=MappingProxyType(dict(data)),
    )


def parse_cost_state(record: Mapping[str, Any]) -> CostState | None:
    """Return the cost state of a ``cost-state`` record, or ``None`` for a record of another type."""
    if record.get("type") != "cost-state":
        return None
    usage = record.get("modelUsage")
    models = (
        {k: _model_usage(v) for k, v in usage.items() if isinstance(k, str) and isinstance(v, Mapping)}
        if isinstance(usage, Mapping)
        else {}
    )
    unknown = record.get("hasUnknownModelCost")
    return CostState(
        total_cost_usd=_float(record, "totalCostUSD"),
        has_unknown_model_cost=unknown if isinstance(unknown, bool) else None,
        started_at=_moment(record, "startTime"),
        duration=_millis(record, "totalDuration"),
        api_duration=_millis(record, "totalAPIDuration"),
        api_duration_without_retries=_millis(record, "totalAPIDurationWithoutRetries"),
        tool_duration=_millis(record, "totalToolDuration"),
        lines_added=_int(record, "totalLinesAdded"),
        lines_removed=_int(record, "totalLinesRemoved"),
        model_usage=MappingProxyType(models),
        raw=MappingProxyType(dict(record)),
    )
