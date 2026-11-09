"""Session-level events and their replies: start and end, setup, instructions, config, cwd and files, worktrees,
compaction, model switches and MCP elicitations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias, TypedDict

from cc_blackbox.hooks._base import FieldReader, HookInput, HookReply, HookResponse, plain, require

if TYPE_CHECKING:
    from typing import Self

SessionStartSource: TypeAlias = Literal["startup", "resume", "clear", "compact", "fork"]
SessionEndReason: TypeAlias = Literal["clear", "resume", "logout", "prompt_input_exit", "other"]
CompactTrigger: TypeAlias = Literal["manual", "auto"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionStartInput(HookInput):
    """When a session starts, resumes, is cleared, compacted or forked."""

    EVENT: ClassVar[str | None] = "SessionStart"

    source: str | None
    """One of :data:`SessionStartSource`, or a newer value."""
    model: str | None
    session_title: str | None
    seconds_since_last_response: float | None
    """This and the next three are set on ``resume`` and ``fork`` when the transcript holds a reply."""
    context_tokens: int | None
    prompt_cache_likely_expired: bool | None
    estimated_cache_write_usd: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionEndInput(HookInput):
    EVENT: ClassVar[str | None] = "SessionEnd"

    reason: str | None
    """One of :data:`SessionEndReason`, or a newer value."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SetupInput(HookInput):
    EVENT: ClassVar[str | None] = "Setup"

    trigger: str | None
    """``init`` or ``maintenance``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class InstructionsLoadedInput(HookInput):
    EVENT: ClassVar[str | None] = "InstructionsLoaded"

    file_path: str | None
    memory_type: str | None
    load_reason: str | None
    globs: tuple[str, ...] | None
    trigger_file_path: str | None
    parent_file_path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigChangeInput(HookInput):
    EVENT: ClassVar[str | None] = "ConfigChange"

    source: str | None
    """``user_settings``, ``project_settings``, ``local_settings``, ``policy_settings`` or ``skills``."""
    file_path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CwdChangedInput(HookInput):
    EVENT: ClassVar[str | None] = "CwdChanged"

    old_cwd: str | None
    new_cwd: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectoryAddedInput(HookInput):
    EVENT: ClassVar[str | None] = "DirectoryAdded"

    directory: str | None
    source: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FileChangedInput(HookInput):
    EVENT: ClassVar[str | None] = "FileChanged"

    file_path: str | None
    event: str | None
    """``change``, ``add`` or ``unlink``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeCreateInput(HookInput):
    EVENT: ClassVar[str | None] = "WorktreeCreate"

    name: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeRemoveInput(HookInput):
    EVENT: ClassVar[str | None] = "WorktreeRemove"

    worktree_path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PreCompactInput(HookInput):
    EVENT: ClassVar[str | None] = "PreCompact"

    trigger: str | None
    """One of :data:`CompactTrigger`."""
    custom_instructions: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PostCompactInput(HookInput):
    EVENT: ClassVar[str | None] = "PostCompact"

    trigger: str | None
    compact_summary: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelSwitchHookInput(HookInput):
    """The fields PreModelSwitch and PostModelSwitch share."""

    from_model: str | None
    to_model: str | None
    requested_model: str | None
    source: str | None
    context_tokens: int | None
    prompt_cache_warm: bool | None
    cache_ttl: str | None
    estimated_cache_write_usd: float | None
    pricing: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PreModelSwitchInput(ModelSwitchHookInput):
    EVENT: ClassVar[str | None] = "PreModelSwitch"


@dataclass(frozen=True, slots=True, kw_only=True)
class PostModelSwitchInput(ModelSwitchHookInput):
    EVENT: ClassVar[str | None] = "PostModelSwitch"


@dataclass(frozen=True, slots=True, kw_only=True)
class ElicitationInput(HookInput):
    """When an MCP server asks the user for input during a tool call."""

    EVENT: ClassVar[str | None] = "Elicitation"

    mcp_server_name: str | None
    message: str | None
    mode: str | None
    """``form`` or ``url``."""
    url: str | None
    elicitation_id: str | None
    requested_schema: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ElicitationResultInput(HookInput):
    """After the user answered an MCP elicitation, before the answer goes to the server."""

    EVENT: ClassVar[str | None] = "ElicitationResult"

    mcp_server_name: str | None
    action: str | None
    """``accept``, ``decline`` or ``cancel``."""
    mode: str | None
    elicitation_id: str | None
    content: Mapping[str, Any] | None


# Replies


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionStartReply(HookReply):
    """Context and settings for a new session. SessionStart cannot block."""

    EVENT: ClassVar[str | None] = "SessionStart"

    additional_context: str | None = None
    """Added to Claude's context before the first prompt."""
    initial_user_message: str | None = None
    """The first user message of a ``-p`` session."""
    session_title: str | None = None
    watch_paths: tuple[str, ...] | None = None
    """Absolute paths for FileChanged hooks to watch."""
    reload_skills: bool = False

    def specific_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.additional_context is not None:
            out["additionalContext"] = self.additional_context
        if self.initial_user_message is not None:
            out["initialUserMessage"] = self.initial_user_message
        if self.session_title is not None:
            out["sessionTitle"] = self.session_title
        if self.watch_paths is not None:
            out["watchPaths"] = list(self.watch_paths)
        if self.reload_skills:
            out["reloadSkills"] = True
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class PostModelSwitchReply(HookReply):
    EVENT: ClassVar[str | None] = "PostModelSwitch"

    additional_context: str | None = None

    @classmethod
    def context(cls, additional_context: str) -> PostModelSwitchReply:
        return cls(additional_context=additional_context)

    def specific_json(self) -> dict[str, Any]:
        return {} if self.additional_context is None else {"additionalContext": self.additional_context}


@dataclass(frozen=True, slots=True, kw_only=True)
class PreModelSwitchReply(HookReply):
    """Allow, deny or ask about a model switch. Outside an interactive ``/model``, Claude Code treats ask as deny."""

    EVENT: ClassVar[str | None] = "PreModelSwitch"

    decision: Literal["allow", "deny", "ask"] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        require(self.decision != "deny" or bool(self.reason), "A deny needs a reason: the user reads it.")

    @classmethod
    def allow(cls) -> PreModelSwitchReply:
        return cls(decision="allow")

    @classmethod
    def deny(cls, reason: str) -> PreModelSwitchReply:
        return cls(decision="deny", reason=reason)

    @classmethod
    def ask(cls, reason: str) -> PreModelSwitchReply:
        return cls(decision="ask", reason=reason)

    def specific_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.decision is not None:
            out["permissionDecision"] = self.decision
        if self.reason is not None:
            out["permissionDecisionReason"] = self.reason
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfigChangeReply(HookReply):
    """Keep a changed settings file from taking effect. Claude Code shows no reason, so none is taken."""

    EVENT: ClassVar[str | None] = "ConfigChange"

    blocked: bool = False

    @classmethod
    def block(cls) -> ConfigChangeReply:
        return cls(blocked=True)

    def top_level_json(self) -> dict[str, Any]:
        return {"decision": "block"} if self.blocked else {}


@dataclass(frozen=True, slots=True, kw_only=True)
class CwdChangedReply(HookReply):
    EVENT: ClassVar[str | None] = "CwdChanged"

    watch_paths: tuple[str, ...] | None = None
    """Replaces the dynamic FileChanged watch list; an empty tuple clears it."""

    @classmethod
    def watch(cls, paths: Iterable[str]) -> Self:
        return cls(watch_paths=tuple(paths))

    def specific_json(self) -> dict[str, Any]:
        return {} if self.watch_paths is None else {"watchPaths": list(self.watch_paths)}


@dataclass(frozen=True, slots=True, kw_only=True)
class FileChangedReply(CwdChangedReply):
    EVENT: ClassVar[str | None] = "FileChanged"


@dataclass(frozen=True, slots=True, kw_only=True)
class _PlainTextReply(HookReply):
    def __post_init__(self) -> None:
        require(
            self.system_message is None and self.halt is None and self.terminal_sequence is None,
            "A plain-text reply cannot carry the JSON fields; Claude Code reads its stdout as text.",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeCreateReply(_PlainTextReply):
    """The path of the worktree the hook created, printed as the last line of stdout."""

    EVENT: ClassVar[str | None] = "WorktreeCreate"

    path: str

    def render(self) -> HookResponse:
        return HookResponse(stdout=self.path + "\n")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeRemoveReply(HookReply):
    """Report that the hook could not remove the worktree. Claude Code reads only the exit code."""

    EVENT: ClassVar[str | None] = "WorktreeRemove"

    failure: str | None = None

    @classmethod
    def failed(cls, message: str) -> WorktreeRemoveReply:
        return cls(failure=message)

    def render(self) -> HookResponse:
        if self.failure is None:
            return HookResponse()
        return HookResponse(stderr=self.failure.rstrip("\n") + "\n", exit_code=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class PreCompactReply(_PlainTextReply):
    """Block a compaction, or give instructions for its summary. Claude Code discards the common JSON fields here."""

    EVENT: ClassVar[str | None] = "PreCompact"

    block_reason: str | None = None
    instructions_text: str | None = None

    def __post_init__(self) -> None:
        _PlainTextReply.__post_init__(self)
        require(self.block_reason is None or self.instructions_text is None, "Block or instruct, not both.")

    @classmethod
    def block(cls, reason: str) -> PreCompactReply:
        require(bool(reason), "A block needs a reason.")
        return cls(block_reason=reason)

    @classmethod
    def instructions(cls, text: str) -> PreCompactReply:
        """Instructions for the compaction summary, as plain stdout. Claude Code cuts them at 10,000 characters."""
        return cls(instructions_text=text)

    def top_level_json(self) -> dict[str, Any]:
        return {} if self.block_reason is None else {"decision": "block", "reason": self.block_reason}

    def render(self) -> HookResponse:
        if self.instructions_text is not None:
            return HookResponse(stdout=self.instructions_text)
        return HookReply.render(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class ElicitationReply(HookReply):
    """Answer an MCP elicitation for the user, decline or cancel it. ``None`` from a handler leaves it to the dialog."""

    EVENT: ClassVar[str | None] = "Elicitation"

    action: Literal["accept", "decline", "cancel"]
    content: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require(self.action == "accept" or self.content is None, "Only accept carries content.")

    @classmethod
    def accept(cls, content: Mapping[str, Any]) -> Self:
        return cls(action="accept", content=content)

    @classmethod
    def decline(cls) -> Self:
        return cls(action="decline")

    @classmethod
    def cancel(cls) -> Self:
        return cls(action="cancel")

    def specific_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action}
        if self.content is not None:
            out["content"] = plain(self.content)
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class ElicitationResultReply(ElicitationReply):
    """Change, decline or cancel the user's answer before it reaches the server. ``content`` replaces the whole answer.

    The hook also runs when the user declined; check the input's ``action`` before returning ``accept``."""

    EVENT: ClassVar[str | None] = "ElicitationResult"


def _session_start(r: FieldReader) -> SessionStartInput:
    return SessionStartInput(
        **r.common(),
        source=r.text("source"),
        model=r.text("model"),
        session_title=r.text("session_title"),
        seconds_since_last_response=r.number("seconds_since_last_response"),
        context_tokens=r.integer("context_tokens"),
        prompt_cache_likely_expired=r.flag("prompt_cache_likely_expired"),
        estimated_cache_write_usd=r.number("estimated_cache_write_usd"),
        **r.finish(),
    )


def _instructions_loaded(r: FieldReader) -> InstructionsLoadedInput:
    return InstructionsLoadedInput(
        **r.common(),
        file_path=r.text("file_path"),
        memory_type=r.text("memory_type"),
        load_reason=r.text("load_reason"),
        globs=r.strings("globs"),
        trigger_file_path=r.text("trigger_file_path"),
        parent_file_path=r.text("parent_file_path"),
        **r.finish(),
    )


class _ModelSwitchFields(TypedDict):
    from_model: str | None
    to_model: str | None
    requested_model: str | None
    source: str | None
    context_tokens: int | None
    prompt_cache_warm: bool | None
    cache_ttl: str | None
    estimated_cache_write_usd: float | None
    pricing: str | None


def _model_switch(r: FieldReader) -> _ModelSwitchFields:
    return _ModelSwitchFields(
        from_model=r.text("from_model"),
        to_model=r.text("to_model"),
        requested_model=r.text("requested_model"),
        source=r.text("source"),
        context_tokens=r.integer("context_tokens"),
        prompt_cache_warm=r.flag("prompt_cache_warm"),
        cache_ttl=r.text("cache_ttl"),
        estimated_cache_write_usd=r.number("estimated_cache_write_usd"),
        pricing=r.text("pricing"),
    )


def _elicitation(r: FieldReader) -> ElicitationInput:
    return ElicitationInput(
        **r.common(),
        mcp_server_name=r.text("mcp_server_name"),
        message=r.text("message"),
        mode=r.text("mode"),
        url=r.text("url"),
        elicitation_id=r.text("elicitation_id"),
        requested_schema=r.obj("requested_schema"),
        **r.finish(),
    )


def _elicitation_result(r: FieldReader) -> ElicitationResultInput:
    return ElicitationResultInput(
        **r.common(),
        mcp_server_name=r.text("mcp_server_name"),
        action=r.text("action"),
        mode=r.text("mode"),
        elicitation_id=r.text("elicitation_id"),
        content=r.obj("content"),
        **r.finish(),
    )


PARSERS: dict[str, Callable[[FieldReader], HookInput]] = {
    "SessionStart": _session_start,
    "SessionEnd": lambda r: SessionEndInput(**r.common(), reason=r.text("reason"), **r.finish()),
    "Setup": lambda r: SetupInput(**r.common(), trigger=r.text("trigger"), **r.finish()),
    "InstructionsLoaded": _instructions_loaded,
    "ConfigChange": lambda r: ConfigChangeInput(
        **r.common(), source=r.text("source"), file_path=r.text("file_path"), **r.finish()
    ),
    "CwdChanged": lambda r: CwdChangedInput(
        **r.common(), old_cwd=r.text("old_cwd"), new_cwd=r.text("new_cwd"), **r.finish()
    ),
    "DirectoryAdded": lambda r: DirectoryAddedInput(
        **r.common(), directory=r.text("directory"), source=r.text("source"), **r.finish()
    ),
    "FileChanged": lambda r: FileChangedInput(
        **r.common(), file_path=r.text("file_path"), event=r.text("event"), **r.finish()
    ),
    "WorktreeCreate": lambda r: WorktreeCreateInput(**r.common(), name=r.text("name"), **r.finish()),
    "WorktreeRemove": lambda r: WorktreeRemoveInput(**r.common(), worktree_path=r.text("worktree_path"), **r.finish()),
    "PreCompact": lambda r: PreCompactInput(
        **r.common(), trigger=r.text("trigger"), custom_instructions=r.text("custom_instructions"), **r.finish()
    ),
    "PostCompact": lambda r: PostCompactInput(
        **r.common(), trigger=r.text("trigger"), compact_summary=r.text("compact_summary"), **r.finish()
    ),
    "PreModelSwitch": lambda r: PreModelSwitchInput(**r.common(), **_model_switch(r), **r.finish()),
    "PostModelSwitch": lambda r: PostModelSwitchInput(**r.common(), **_model_switch(r), **r.finish()),
    "Elicitation": _elicitation,
    "ElicitationResult": _elicitation_result,
}
