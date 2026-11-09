"""Turn events: prompts, Stop and SubagentStop, subagent start, notifications, message display, tasks and teammates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

from cc_blackbox.hooks._base import FieldReader, HookInput

if TYPE_CHECKING:
    from cc_blackbox.sessions.stored import SubagentTranscript


def _text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPromptSubmitInput(HookInput):
    """Before a prompt reaches Claude: typed by the user, or from a scheduled task, a background report or a peer."""

    EVENT: ClassVar[str | None] = "UserPromptSubmit"

    prompt: str | None
    """The prompt as submitted. A typed slash command arrives as typed, not expanded."""
    session_title: str | None
    """The session's custom title, when one is set."""
    source: str | None
    """Seen in the Claude Code 2.1.319 bundle as ``poll_event`` behind a feature flag; undocumented."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPromptExpansionInput(HookInput):
    """When a typed slash command or MCP prompt expands into a prompt."""

    EVENT: ClassVar[str | None] = "UserPromptExpansion"

    expansion_type: str | None
    """``slash_command`` or ``mcp_prompt``."""
    command_name: str | None
    command_args: str | None
    command_source: str | None
    prompt: str | None


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    """A task in flight when the turn stopped. The fields beyond ``type`` depend on it; ``raw`` holds the rest."""

    id: str | None
    type: str | None
    """``shell``, ``subagent``, ``monitor``, ``workflow``, ``teammate``, ``cloud session``, ``MCP task`` or newer."""
    status: str | None
    description: str | None
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SessionCron:
    """A session-scoped scheduled wakeup."""

    id: str | None
    schedule: str | None
    recurring: bool | None
    prompt: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class StopHookInput(HookInput):
    """The fields Stop and SubagentStop share."""

    stop_hook_active: bool | None
    """``True`` when the turn already continues because a stop hook blocked. Check it to avoid an endless loop."""
    last_assistant_message: str | None
    """The text of the final reply. Prefer it to the transcript, which can lag."""
    background_tasks: tuple[BackgroundTask, ...]
    session_crons: tuple[SessionCron, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StopInput(StopHookInput):
    """When the main agent finished its turn."""

    EVENT: ClassVar[str | None] = "Stop"


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentStopInput(StopHookInput):
    """When a subagent finished. ``agent_type`` is ``""`` for Claude Code's internal agents."""

    EVENT: ClassVar[str | None] = "SubagentStop"

    agent_transcript_path: str | None

    def agent_transcript(self) -> SubagentTranscript | None:
        """Return the subagent's transcript, or ``None`` when it cannot be found. Imports the transcript code."""
        from cc_blackbox.hooks._links import agent_transcript_of

        return agent_transcript_of(self.transcript_path, self.agent_transcript_path)


@dataclass(frozen=True, slots=True, kw_only=True)
class StopFailureInput(HookInput):
    """When a turn ended on an API error. Claude Code ignores the reply except ``terminal_sequence``."""

    EVENT: ClassVar[str | None] = "StopFailure"

    error: str | None
    """The error type, such as ``rate_limit`` or ``server_error``."""
    error_details: str | None
    last_assistant_message: str | None
    """The rendered API error text, not a reply from Claude."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentStartInput(HookInput):
    """When a subagent starts or resumes. ``agent_id`` and ``agent_type`` name it."""

    EVENT: ClassVar[str | None] = "SubagentStart"


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageDisplayInput(HookInput):
    """While assistant text is shown; display-only."""

    EVENT: ClassVar[str | None] = "MessageDisplay"

    turn_id: str | None
    message_id: str | None
    index: int | None
    final: bool | None
    delta: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationInput(HookInput):
    EVENT: ClassVar[str | None] = "Notification"

    message: str | None
    title: str | None
    notification_type: str | None
    """For example ``permission_prompt``, ``idle_prompt`` or ``auth_success``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskHookInput(HookInput):
    """The fields TaskCreated and TaskCompleted share."""

    task_id: str | None
    task_subject: str | None
    task_description: str | None
    teammate_name: str | None
    team_name: str | None
    """Deprecated by Claude Code."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCreatedInput(TaskHookInput):
    EVENT: ClassVar[str | None] = "TaskCreated"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCompletedInput(TaskHookInput):
    EVENT: ClassVar[str | None] = "TaskCompleted"


@dataclass(frozen=True, slots=True, kw_only=True)
class TeammateIdleInput(HookInput):
    EVENT: ClassVar[str | None] = "TeammateIdle"

    teammate_name: str | None
    team_name: str | None


def _user_prompt_submit(r: FieldReader) -> UserPromptSubmitInput:
    return UserPromptSubmitInput(
        **r.common(),
        prompt=r.text("prompt"),
        session_title=r.text("session_title"),
        source=r.text("source"),
        **r.finish(),
    )


def _user_prompt_expansion(r: FieldReader) -> UserPromptExpansionInput:
    return UserPromptExpansionInput(
        **r.common(),
        expansion_type=r.text("expansion_type"),
        command_name=r.text("command_name"),
        command_args=r.text("command_args"),
        command_source=r.text("command_source"),
        prompt=r.text("prompt"),
        **r.finish(),
    )


def _background_tasks(r: FieldReader) -> tuple[BackgroundTask, ...]:
    return tuple(
        BackgroundTask(_text(t, "id"), _text(t, "type"), _text(t, "status"), _text(t, "description"), t)
        for t in r.records("background_tasks")
    )


def _session_crons(r: FieldReader) -> tuple[SessionCron, ...]:
    crons = []
    for c in r.records("session_crons"):
        recurring = c.get("recurring")
        crons.append(
            SessionCron(
                _text(c, "id"),
                _text(c, "schedule"),
                recurring if isinstance(recurring, bool) else None,
                _text(c, "prompt"),
            )
        )
    return tuple(crons)


def _stop(r: FieldReader) -> StopInput:
    return StopInput(
        **r.common(),
        stop_hook_active=r.flag("stop_hook_active"),
        last_assistant_message=r.text("last_assistant_message"),
        background_tasks=_background_tasks(r),
        session_crons=_session_crons(r),
        **r.finish(),
    )


def _subagent_stop(r: FieldReader) -> SubagentStopInput:
    return SubagentStopInput(
        **r.common(),
        stop_hook_active=r.flag("stop_hook_active"),
        last_assistant_message=r.text("last_assistant_message"),
        background_tasks=_background_tasks(r),
        session_crons=_session_crons(r),
        agent_transcript_path=r.path("agent_transcript_path"),
        **r.finish(),
    )


def _stop_failure(r: FieldReader) -> StopFailureInput:
    return StopFailureInput(
        **r.common(),
        error=r.text("error"),
        error_details=r.text("error_details"),
        last_assistant_message=r.text("last_assistant_message"),
        **r.finish(),
    )


def _subagent_start(r: FieldReader) -> SubagentStartInput:
    return SubagentStartInput(**r.common(), **r.finish())


def _message_display(r: FieldReader) -> MessageDisplayInput:
    return MessageDisplayInput(
        **r.common(),
        turn_id=r.text("turn_id"),
        message_id=r.text("message_id"),
        index=r.integer("index"),
        final=r.flag("final"),
        delta=r.text("delta"),
        **r.finish(),
    )


def _notification(r: FieldReader) -> NotificationInput:
    return NotificationInput(
        **r.common(),
        message=r.text("message"),
        title=r.text("title"),
        notification_type=r.text("notification_type"),
        **r.finish(),
    )


class _TaskFields(TypedDict):
    task_id: str | None
    task_subject: str | None
    task_description: str | None
    teammate_name: str | None
    team_name: str | None


def _task_fields(r: FieldReader) -> _TaskFields:
    return _TaskFields(
        task_id=r.text("task_id"),
        task_subject=r.text("task_subject"),
        task_description=r.text("task_description"),
        teammate_name=r.text("teammate_name"),
        team_name=r.text("team_name"),
    )


def _task_created(r: FieldReader) -> TaskCreatedInput:
    return TaskCreatedInput(**r.common(), **_task_fields(r), **r.finish())


def _task_completed(r: FieldReader) -> TaskCompletedInput:
    return TaskCompletedInput(**r.common(), **_task_fields(r), **r.finish())


def _teammate_idle(r: FieldReader) -> TeammateIdleInput:
    return TeammateIdleInput(
        **r.common(), teammate_name=r.text("teammate_name"), team_name=r.text("team_name"), **r.finish()
    )


PARSERS: dict[str, Callable[[FieldReader], HookInput]] = {
    "UserPromptSubmit": _user_prompt_submit,
    "UserPromptExpansion": _user_prompt_expansion,
    "Stop": _stop,
    "SubagentStop": _subagent_stop,
    "StopFailure": _stop_failure,
    "SubagentStart": _subagent_start,
    "MessageDisplay": _message_display,
    "Notification": _notification,
    "TaskCreated": _task_created,
    "TaskCompleted": _task_completed,
    "TeammateIdle": _teammate_idle,
}
