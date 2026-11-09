"""The parts every hook needs: the common input fields, the tolerant field reader, the reply base and the parser.

Claude Code writes one JSON object on the hook's stdin. Each event has its own class, defined in one of three family
modules that :func:`parse_hook_input` imports only when an event of that family arrives, so a hook pays for the
classes of its own event and not for all 33.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias, TypedDict

from cc_blackbox.errors import HookInputError

if TYPE_CHECKING:
    from typing import Self

    from cc_blackbox.sessions.active import ActiveSession
    from cc_blackbox.sessions.stored import SessionTranscript

PermissionMode: TypeAlias = Literal["default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions"]
"""The documented ``permission_mode`` values. The field itself is a ``str``, so a new mode is kept, not dropped."""

PermissionDecision: TypeAlias = Literal["allow", "deny", "ask", "defer"]

EVENT_MODULES: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(
            [
                "PreToolUse",
                "PermissionRequest",
                "PermissionDenied",
                "PostToolUse",
                "PostToolUseFailure",
                "PostToolBatch",
            ],
            "cc_blackbox.hooks._tool_events",
        ),
        **dict.fromkeys(
            [
                "UserPromptSubmit",
                "UserPromptExpansion",
                "Stop",
                "SubagentStop",
                "StopFailure",
                "SubagentStart",
                "MessageDisplay",
                "Notification",
                "TaskCreated",
                "TaskCompleted",
                "TeammateIdle",
            ],
            "cc_blackbox.hooks._turn_events",
        ),
        **dict.fromkeys(
            [
                "SessionStart",
                "SessionEnd",
                "Setup",
                "InstructionsLoaded",
                "ConfigChange",
                "CwdChanged",
                "DirectoryAdded",
                "FileChanged",
                "WorktreeCreate",
                "WorktreeRemove",
                "PreCompact",
                "PostCompact",
                "PreModelSwitch",
                "PostModelSwitch",
                "Elicitation",
                "ElicitationResult",
            ],
            "cc_blackbox.hooks._session_events",
        ),
    }
)

KNOWN_HOOK_EVENTS: frozenset[str] = frozenset(EVENT_MODULES)
"""The hook events of Claude Code 2.1.x. Information only: an unknown event is parsed as :class:`UnknownHookInput`."""

_EMPTY: Mapping[str, Any] = MappingProxyType({})


class CommonFields(TypedDict):
    hook_event_name: str
    session_id: str | None
    transcript_path: str | None
    cwd: str | None
    scratchpad_dir: str | None
    prompt_id: str | None
    permission_mode: str | None
    effort_level: str | None
    agent_id: str | None
    agent_type: str | None


class Finish(TypedDict):
    raw: Mapping[str, Any]
    type_mismatches: tuple[str, ...]


class FieldReader:
    """Reads payload fields by type. A field of an unexpected type reads as ``None`` and is remembered."""

    __slots__ = ("data", "event", "mismatches")

    def __init__(self, data: Mapping[str, Any], event: str) -> None:
        self.data = data
        self.event = event
        self.mismatches: list[str] = []

    def _get(self, key: str, kinds: tuple[type, ...]) -> Any:
        value = self.data.get(key)
        if value is None:
            return None
        if isinstance(value, kinds) and (bool in kinds or not isinstance(value, bool)):
            return value
        self.mismatches.append(key)
        return None

    def text(self, key: str) -> str | None:
        value: str | None = self._get(key, (str,))
        return value

    def path(self, key: str) -> str | None:
        """A string field where ``""`` means absent, as Claude Code sends for paths it does not have."""
        return self.text(key) or None

    def integer(self, key: str) -> int | None:
        value: int | None = self._get(key, (int,))
        return value

    def number(self, key: str) -> float | None:
        value: int | float | None = self._get(key, (int, float))
        return None if value is None else float(value)

    def flag(self, key: str) -> bool | None:
        value: bool | None = self._get(key, (bool,))
        return value

    def obj(self, key: str) -> Mapping[str, Any] | None:
        value: dict[str, Any] | None = self._get(key, (dict,))
        return None if value is None else MappingProxyType(value)

    def array(self, key: str) -> list[Any] | None:
        value: list[Any] | None = self._get(key, (list,))
        return value

    def strings(self, key: str) -> tuple[str, ...] | None:
        items = self.array(key)
        if items is None:
            return None
        if not all(isinstance(item, str) for item in items):
            self.mismatches.append(key)
        return tuple(item for item in items if isinstance(item, str))

    def records(self, key: str) -> list[Mapping[str, Any]]:
        """The objects of a list field. Entries that are not objects are dropped and remembered."""
        items = self.array(key) or []
        records: list[Mapping[str, Any]] = [MappingProxyType(item) for item in items if isinstance(item, dict)]
        if len(records) != len(items):
            self.mismatches.append(key)
        return records

    def value(self, key: str) -> Any:
        return self.data.get(key)

    def common(self) -> CommonFields:
        effort = self.obj("effort")
        level = effort.get("level") if effort is not None else None
        if level is not None and not isinstance(level, str):
            self.mismatches.append("effort.level")
            level = None
        return CommonFields(
            hook_event_name=self.event,
            session_id=self.text("session_id"),
            transcript_path=self.path("transcript_path"),
            cwd=self.path("cwd"),
            scratchpad_dir=self.path("scratchpad_dir"),
            prompt_id=self.text("prompt_id"),
            permission_mode=self.text("permission_mode"),
            effort_level=level,
            agent_id=self.text("agent_id"),
            agent_type=self.text("agent_type"),
        )

    def finish(self) -> Finish:
        """Call last, after every other field is read."""
        return Finish(raw=MappingProxyType(dict(self.data)), type_mismatches=tuple(self.mismatches))


@dataclass(frozen=True, slots=True, kw_only=True)
class HookInput:
    """The fields every hook event carries, and the links to the rest of the library.

    Every field except ``hook_event_name`` can be ``None``: Claude Code leaves some out on some events, sends ``""`` for
    paths it does not have (read as ``None``), and a field with an unexpected type reads as ``None`` and is named in
    ``type_mismatches``. ``raw`` holds the whole payload.
    """

    EVENT: ClassVar[str | None] = None
    """The ``hook_event_name`` this class stands for; ``None`` on the bases and on :class:`UnknownHookInput`."""

    hook_event_name: str
    session_id: str | None
    """The session id. A hook that runs for a remote call gets ``served:<caller session id>``."""
    transcript_path: str | None
    """The main transcript. Claude Code writes it asynchronously, so it can lag the conversation by a few records."""
    cwd: str | None
    scratchpad_dir: str | None
    prompt_id: str | None
    permission_mode: str | None
    """One of :data:`PermissionMode`, or a newer mode kept as a string. Absent on some events, such as SessionStart."""
    effort_level: str | None
    """The ``effort.level`` field: ``low`` to ``max``, on models with effort."""
    agent_id: str | None
    """Set when the hook fires inside a subagent or an in-process teammate."""
    agent_type: str | None
    raw: Mapping[str, Any]
    type_mismatches: tuple[str, ...]
    """The modeled fields that had an unexpected type and read as ``None``. Empty when the payload matches the model."""

    def transcript(self) -> SessionTranscript | None:
        """Read the session's transcript, or return ``None`` when the payload names none or the file does not exist.

        Imports the transcript code on first call. The file can lag the conversation; on Stop and SubagentStop,
        ``last_assistant_message`` holds the final reply reliably.
        """
        from cc_blackbox.hooks._links import transcript_of

        return transcript_of(self.transcript_path)

    def active_session(self) -> ActiveSession | None:
        """Find the running session this hook fires in, or return ``None`` when no live registry record names it.

        A hook inherits the session's environment, so this is usually :func:`~cc_blackbox.current_session`. Imports the
        session code on first call.
        """
        from cc_blackbox.hooks._links import active_session_of

        return active_session_of(self.session_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownHookInput(HookInput):
    """An event this version of the library does not model. ``raw`` holds its fields."""


@dataclass(frozen=True, slots=True)
class Halt:
    """Stop Claude after the hook: ``"continue": false``, with ``reason`` as the ``stopReason`` shown to the user."""

    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HookResponse:
    """What a reply writes: stdout, stderr and the exit code."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


def plain(value: Any) -> Any:
    """A JSON-ready copy: read-only mappings become dicts and tuples become lists."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(plain(payload), ensure_ascii=True, separators=(",", ":")) + "\n"


@dataclass(frozen=True, slots=True, kw_only=True)
class HookReply:
    """A reply to any event: the fields every event accepts. Subclasses add what one event can decide.

    ``HookReply()`` says nothing and writes nothing. Several events discard ``system_message`` and ``halt``; the
    Claude Code hooks reference lists which.
    """

    EVENT: ClassVar[str | None] = None
    """The ``hookEventName`` this reply belongs to; ``None`` when it fits every event."""

    system_message: str | None = None
    """A warning shown to the user."""
    halt: Halt | None = None
    suppress_output: bool = False
    """``suppressOutput``. Documented as having no effect; Claude Code 2.1.x still reads it to hide a success line."""
    terminal_sequence: str | None = None
    """An allowlisted terminal escape sequence (OSC 0, 1, 2, 9, 99, 777 or BEL) for Claude Code to write."""

    def common_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.halt is not None:
            out["continue"] = False
            if self.halt.reason is not None:
                out["stopReason"] = self.halt.reason
        if self.suppress_output:
            out["suppressOutput"] = True
        if self.system_message is not None:
            out["systemMessage"] = self.system_message
        if self.terminal_sequence is not None:
            out["terminalSequence"] = self.terminal_sequence
        return out

    def top_level_json(self) -> dict[str, Any]:
        """Event fields that sit at the top level, such as ``decision`` and ``reason``."""
        return {}

    def specific_json(self) -> dict[str, Any]:
        """The ``hookSpecificOutput`` fields, without ``hookEventName``."""
        return {}

    def to_json(self) -> dict[str, Any]:
        """Return the JSON object Claude Code reads, or ``{}`` when the reply says nothing."""
        out = {**self.top_level_json(), **self.common_json()}
        specific = self.specific_json()
        if specific:
            out["hookSpecificOutput"] = {"hookEventName": self.EVENT, **specific}
        return out

    def render(self) -> HookResponse:
        payload = self.to_json()
        return HookResponse(stdout=dumps(payload) if payload else "")

    def emit(self, stdout: IO[str] | None = None, stderr: IO[str] | None = None) -> int:
        """Write the reply to stdout and stderr and return the exit code the hook should exit with."""
        return write_response(self.render(), stdout, stderr)

    def with_system_message(self, text: str) -> Self:
        return replace(self, system_message=text)

    def with_halt(self, reason: str | None = None) -> Self:
        return replace(self, halt=Halt(reason))

    def with_terminal_sequence(self, sequence: str) -> Self:
        return replace(self, terminal_sequence=sequence)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawReply(HookReply):
    """A JSON object written as given, for output fields this library does not model yet. The common fields merge in."""

    payload: Mapping[str, Any]

    @property
    def event_name(self) -> str | None:
        specific = self.payload.get("hookSpecificOutput")
        name = specific.get("hookEventName") if isinstance(specific, Mapping) else None
        return name if isinstance(name, str) else None

    def to_json(self) -> dict[str, Any]:
        return {**plain(self.payload), **self.common_json()}


def write_response(response: HookResponse, stdout: IO[str] | None, stderr: IO[str] | None) -> int:
    for text, stream in ((response.stdout, stdout or sys.stdout), (response.stderr, stderr or sys.stderr)):
        if not text:
            continue
        try:
            stream.write(text)
            stream.flush()
        except BrokenPipeError:
            pass  # Claude Code stopped reading, for example after a timeout; there is nobody left to tell
    return response.exit_code


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_hook_input(data: Mapping[str, Any] | str | bytes, *, event: str | None = None) -> HookInput:
    """Parse a hook payload into the class of its event.

    ``event`` is the event to assume when the payload has no ``hook_event_name``, as in a test that sends only
    ``tool_input``. A payload that names its event wins. An unknown event gives :class:`UnknownHookInput`.

    Raises:
        HookInputError: the payload is not a JSON object.
    """
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError as error:
            raise HookInputError(f"The hook payload is not valid JSON: {error}.") from None
    if not isinstance(data, Mapping):
        raise HookInputError(f"The hook payload is a JSON {type(data).__name__}, not an object.")
    name = data.get("hook_event_name")
    if not isinstance(name, str) or not name:
        name = event or ""
    module_name = EVENT_MODULES.get(name)
    reader = FieldReader(data, name)
    if module_name is None:
        return UnknownHookInput(**reader.common(), **reader.finish())
    module = sys.modules.get(module_name)
    if module is None:
        import importlib

        module = importlib.import_module(module_name)
    parsed: HookInput = module.PARSERS[name](reader)
    return parsed


def read_hook_input(stream: IO[str] | IO[bytes] | None = None, *, event: str | None = None) -> HookInput:
    """Read the whole payload from ``stream``, ``sys.stdin`` by default, and parse it.

    A text stream is read through its binary buffer when it has one and decoded as UTF-8, which is what Claude Code
    writes whatever the platform's default encoding is.
    """
    source: Any = sys.stdin if stream is None else stream
    buffer = getattr(source, "buffer", None)
    data = (buffer if buffer is not None else source).read()
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return parse_hook_input(data, event=event)
