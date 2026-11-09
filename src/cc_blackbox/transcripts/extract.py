"""Extract structured facts from transcript entries.

Every extractor takes the entries of one transcript, in file order, and yields typed results in file order. The
entries can be a list or the iterator :func:`~cc_blackbox.iter_entries` returns. To run several extractors over one
transcript, read it once into a list. Extractors that join a tool call with its result keep the entries of the
transcript in memory.

Entries come in file order, which includes branches the conversation later left, for example after a rewind.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from cc_blackbox.transcripts.entries import (
    AssistantEntry,
    Entry,
    SystemEntry,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserEntry,
)
from cc_blackbox.transcripts.markup import ShellCommandPart, parse_user_markup
from cc_blackbox.transcripts.prompts import Prompt, prompt_from

if TYPE_CHECKING:
    from cc_blackbox.sessions.stored import SessionTranscript, SubagentTranscript

__all__ = [
    "AssistantText",
    "Compaction",
    "Failure",
    "FileOperation",
    "FileTouch",
    "ShellCommand",
    "SubagentRun",
    "Thinking",
    "ToolCall",
    "UsageTotals",
    "assistant_texts",
    "compactions",
    "conversation",
    "failures",
    "files_touched",
    "shell_commands",
    "subagent_runs",
    "token_usage",
    "tool_calls",
]

SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
"""Tools that spawn a subagent. ``Task`` is the older name of ``Agent``."""

FileOperation = Literal["read", "write", "edit"]

_FILE_TOOLS: Mapping[str, tuple[str, FileOperation]] = {
    "Read": ("file_path", "read"),
    "Write": ("file_path", "write"),
    "Edit": ("file_path", "edit"),
    "MultiEdit": ("file_path", "edit"),
    "NotebookEdit": ("notebook_path", "edit"),
}
_INTERRUPTED = "[Request interrupted by user"


@dataclass(frozen=True, slots=True)
class AssistantText:
    text: str
    model: str | None
    timestamp: datetime | None
    uuid: str | None


@dataclass(frozen=True, slots=True)
class Thinking:
    text: str
    timestamp: datetime | None
    uuid: str | None


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call joined with its result."""

    id: str | None
    name: str | None
    input: Mapping[str, Any]
    result: ToolResultBlock | None
    """``None`` when the transcript holds no result, for example when the session ended during the call."""
    result_meta: Any
    """The ``toolUseResult`` of the record that carried the result: structured output the model never sees."""
    denial: str | None
    """``toolDenialKind`` when the call was denied, for example ``user-rejected``."""
    timestamp: datetime | None
    uuid: str | None
    """The uuid of the assistant record that made the call."""

    @property
    def is_error(self) -> bool:
        return self.result is not None and self.result.is_error


@dataclass(frozen=True, slots=True)
class SubagentRun:
    """A subagent spawned by an ``Agent`` tool call, with its final response."""

    tool_use_id: str | None
    description: str | None
    subagent_type: str | None
    prompt: str
    result: str | None
    """The subagent's final response. ``None`` when the main transcript holds none, for example for a subagent still
    running in the background."""
    agent_id: str | None
    transcript: SubagentTranscript | None
    """The subagent's own transcript, when a session was given and the file exists."""
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class FileTouch:
    path: str
    operation: FileOperation
    tool_name: str
    tool_use_id: str | None
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class ShellCommand:
    command: str
    output: str | None
    is_error: bool
    source: Literal["tool", "user"]
    """``tool`` for a Bash tool call, ``user`` for a command the user ran with ``!``."""
    tool_use_id: str | None
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class Failure:
    kind: Literal["tool-error", "denied", "interrupted"]
    message: str
    tool_name: str | None
    tool_use_id: str | None
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class UsageTotals:
    messages: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


@dataclass(frozen=True, slots=True)
class Compaction:
    """A point where Claude Code compacted the conversation, with the summary that replaced it."""

    trigger: str | None
    """``manual`` or ``auto``."""
    pre_tokens: int | None
    summary: str | None
    timestamp: datetime | None
    uuid: str | None


@dataclass(slots=True)
class _Result:
    block: ToolResultBlock
    entry: UserEntry


def _results(entries: list[Entry]) -> dict[str, _Result]:
    found: dict[str, _Result] = {}
    for entry in entries:
        if isinstance(entry, UserEntry):
            for block in entry.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id is not None:
                    found.setdefault(block.tool_use_id, _Result(block, entry))
    return found


def _tool_uses(entries: Iterable[Entry]) -> Iterator[tuple[AssistantEntry, ToolUseBlock]]:
    for entry in entries:
        if isinstance(entry, AssistantEntry):
            for block in entry.content:
                if isinstance(block, ToolUseBlock):
                    yield entry, block


def _call(entry: AssistantEntry, block: ToolUseBlock, results: Mapping[str, _Result]) -> ToolCall:
    found = results.get(block.id) if block.id is not None else None
    return ToolCall(
        id=block.id,
        name=block.name,
        input=block.input,
        result=found.block if found else None,
        result_meta=found.entry.tool_use_result if found else None,
        denial=found.entry.tool_denial if found else None,
        timestamp=entry.timestamp,
        uuid=entry.uuid,
    )


def _str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def assistant_texts(entries: Iterable[Entry]) -> Iterator[AssistantText]:
    """Yield the text blocks of assistant messages. Needs only ``assistant`` records."""
    for entry in entries:
        if isinstance(entry, AssistantEntry):
            for block in entry.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    yield AssistantText(block.text, entry.model, entry.timestamp, entry.uuid)


def tool_calls(entries: Iterable[Entry]) -> Iterator[ToolCall]:
    """Yield every tool call joined with its result. Needs ``assistant`` and ``user`` records."""
    materialized = list(entries)
    results = _results(materialized)
    for entry, block in _tool_uses(materialized):
        yield _call(entry, block, results)


def _subagent_run(call: ToolCall, session: SessionTranscript | None) -> SubagentRun:
    meta = call.result_meta if isinstance(call.result_meta, Mapping) else {}
    agent_id = _str(meta, "agentId")
    # Only a result that names the agent is its final response; anything else is a launch notice or an error.
    result = call.result.text if call.result is not None and agent_id is not None and call.result.text else None
    prompt = call.input.get("prompt")
    return SubagentRun(
        tool_use_id=call.id,
        description=_str(call.input, "description"),
        subagent_type=_str(call.input, "subagent_type"),
        prompt=prompt if isinstance(prompt, str) else "",
        result=result,
        agent_id=agent_id,
        transcript=session.subagent(agent_id) if session is not None and agent_id is not None else None,
        timestamp=call.timestamp,
    )


def subagent_runs(entries: Iterable[Entry], session: SessionTranscript | None = None) -> Iterator[SubagentRun]:
    """Yield every subagent the transcript spawned, with its prompt and final response.

    With ``session``, each run is linked to the subagent's own transcript.
    """
    for call in tool_calls(entries):
        if call.name in SUBAGENT_TOOLS:
            yield _subagent_run(call, session)


def files_touched(entries: Iterable[Entry]) -> Iterator[FileTouch]:
    """Yield every file a Read, Write, Edit, MultiEdit or NotebookEdit call named. Needs only ``assistant`` records.

    A file touched through a shell command is not included; see :func:`shell_commands`.
    """
    for entry, block in _tool_uses(entries):
        spec = _FILE_TOOLS.get(block.name or "")
        path = _str(block.input, spec[0]) if spec else None
        if spec and path and block.name:
            yield FileTouch(path, spec[1], block.name, block.id, entry.timestamp)


def shell_commands(entries: Iterable[Entry]) -> Iterator[ShellCommand]:
    """Yield every Bash tool call and every ``!`` command the user ran, with its output."""
    materialized = list(entries)
    results = _results(materialized)
    for entry in materialized:
        if isinstance(entry, AssistantEntry):
            for block in entry.content:
                if isinstance(block, ToolUseBlock) and block.name == "Bash":
                    call = _call(entry, block, results)
                    command = _str(call.input, "command") or ""
                    output = call.result.text if call.result is not None else None
                    yield ShellCommand(command, output, call.is_error, "tool", call.id, call.timestamp)
        elif isinstance(entry, UserEntry) and entry.text is not None and "<bash-input" in entry.text:
            for part in parse_user_markup(entry.text):
                if isinstance(part, ShellCommandPart) and part.command:
                    output = "\n".join(p for p in (part.stdout, part.stderr) if p) or None
                    yield ShellCommand(part.command, output, part.stderr is not None, "user", None, entry.timestamp)


def failures(entries: Iterable[Entry]) -> Iterator[Failure]:
    """Yield failed tool calls, denied tool calls and turns the user interrupted."""
    materialized = list(entries)
    results = _results(materialized)
    for entry in materialized:
        if isinstance(entry, AssistantEntry):
            for block in entry.content:
                if isinstance(block, ToolUseBlock):
                    call = _call(entry, block, results)
                    message = call.result.text if call.result is not None else ""
                    if call.denial is not None:
                        yield Failure("denied", message or call.denial, call.name, call.id, call.timestamp)
                    elif call.is_error:
                        yield Failure("tool-error", message, call.name, call.id, call.timestamp)
        elif isinstance(entry, UserEntry) and entry.plain_text.lstrip().startswith(_INTERRUPTED):
            yield Failure("interrupted", entry.plain_text.strip(), None, None, entry.timestamp)


def token_usage(entries: Iterable[Entry]) -> dict[str, UsageTotals]:
    """Sum token usage per model. Needs only ``assistant`` records.

    Claude Code writes one record per content block of a message and repeats the message's whole usage on each, so a
    message is counted once per ``message.id``. Synthetic messages that Claude Code made up without calling a model
    are left out.
    """
    seen: set[str] = set()
    totals: dict[str, list[int]] = {}
    for entry in entries:
        if not isinstance(entry, AssistantEntry) or entry.usage is None or not entry.model:
            continue
        if entry.model == "<synthetic>":
            continue
        if entry.message_id is not None:
            if entry.message_id in seen:
                continue
            seen.add(entry.message_id)
        usage = entry.usage
        counts = totals.setdefault(entry.model, [0, 0, 0, 0, 0])
        counts[0] += 1
        counts[1] += usage.input_tokens
        counts[2] += usage.output_tokens
        counts[3] += usage.cache_creation_input_tokens
        counts[4] += usage.cache_read_input_tokens
    return {model: UsageTotals(*counts) for model, counts in totals.items()}


def compactions(entries: Iterable[Entry]) -> Iterator[Compaction]:
    """Yield every compaction with the summary that follows its boundary. Needs ``system`` and ``user`` records."""
    pending: SystemEntry | None = None

    def build(boundary: SystemEntry, summary: str | None) -> Compaction:
        meta = boundary.raw.get("compactMetadata")
        meta = meta if isinstance(meta, Mapping) else {}
        tokens = meta.get("preTokens")
        return Compaction(
            trigger=_str(meta, "trigger"),
            pre_tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
            summary=summary,
            timestamp=boundary.timestamp,
            uuid=boundary.uuid,
        )

    for entry in entries:
        if isinstance(entry, SystemEntry) and entry.subtype == "compact_boundary":
            if pending is not None:
                yield build(pending, None)
            pending = entry
        elif isinstance(entry, UserEntry) and entry.is_compact_summary and pending is not None:
            yield build(pending, entry.plain_text)
            pending = None
    if pending is not None:
        yield build(pending, None)


def conversation(
    entries: Iterable[Entry],
    *,
    include_thinking: bool = False,
    session: SessionTranscript | None = None,
) -> Iterator[Prompt | AssistantText | Thinking | SubagentRun]:
    """Yield the conversation without tool noise: prompts, assistant text and subagent runs, in file order.

    A subagent run comes where it was spawned, already joined with its final response. Thinking blocks come only
    with ``include_thinking``, and only when they hold text: Claude Code keeps only the signature of most of them.
    """
    materialized = list(entries)
    results = _results(materialized)
    for entry in materialized:
        prompt = prompt_from(entry)
        if prompt is not None:
            yield prompt
            continue
        if not isinstance(entry, AssistantEntry):
            continue
        for block in entry.content:
            if isinstance(block, TextBlock) and block.text.strip():
                yield AssistantText(block.text, entry.model, entry.timestamp, entry.uuid)
            elif isinstance(block, ThinkingBlock) and include_thinking and block.thinking.strip():
                yield Thinking(block.thinking, entry.timestamp, entry.uuid)
            elif isinstance(block, ToolUseBlock) and block.name in SUBAGENT_TOOLS:
                yield _subagent_run(_call(entry, block, results), session)
