"""A stream of events from a background tool to the Claude Code session that started it, or to stdout.

A watcher started as a background Bash task from a session can report what it sees in two ways: one JSON line per
event on stdout, which the session reads from the task output, or a user message into the session through its
messaging socket. :func:`choose_event_stream` picks one of them for the ``auto``, ``stdout`` and ``claude-message``
modes, and says why ``auto`` did not message a session the tool runs under.

:class:`MessageEventSink` batches the events that arrive close together into one message: the first event of a burst
waits ``burst_wait`` seconds for the rest. Each message is an XML-like block named by :class:`EventEnvelope`, because
the receiver shows the content to the model but never the sender label. The ``seq`` attribute keeps every body unique,
so the receiver's duplicate check never drops a real event. A batch above ``max_bytes`` becomes a count per kind.

Delivery is best effort: the receiver may rate-limit a message, or hold it until its user approves it. Keep a full
record of the events elsewhere, for example a JSON-lines file, and name it in ``EventEnvelope.record``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any, Literal, Protocol, TextIO, TypeVar

from cc_blackbox.errors import (
    ClaudeBlackboxError,
    MessageTooLarge,
    MessagingUnavailable,
    SessionGone,
    UnsupportedProtocol,
)
from cc_blackbox.messaging.client import RateLimitedSender
from cc_blackbox.messaging.protocol import Priority
from cc_blackbox.sessions.active import ActiveSession, current_session

__all__ = [
    "EVENT_STREAM_MODES",
    "EventEnvelope",
    "EventSink",
    "EventStreamChoice",
    "EventStreamMode",
    "MessageEventSink",
    "StdoutEventSink",
    "StreamEvent",
    "choose_event_stream",
    "current_session_or_none",
    "messaging_problem",
]

EventStreamMode = Literal["auto", "stdout", "claude-message"]
EVENT_STREAM_MODES: tuple[EventStreamMode, ...] = ("auto", "stdout", "claude-message")

BURST_WAIT = 3.0
MAX_CONTENT_BYTES = 64 * 1024
LIVENESS_INTERVAL = 30.0
FLUSH_TIMEOUT = 10.0


class StreamEvent(Protocol):
    """What the sinks need from an event: its kind, its sequence number, and its JSON form for stdout."""

    @property
    def kind(self) -> str: ...

    @property
    def seq(self) -> int: ...

    def to_json(self) -> Mapping[str, Any]: ...


E_contra = TypeVar("E_contra", bound=StreamEvent, contravariant=True)


class EventSink(Protocol[E_contra]):
    """Where a tool delivers its events. The tool calls :meth:`tick` when :meth:`seconds_until_flush` says so."""

    name: str

    def describe(self) -> str:
        """One line for a startup header: the stream mode and its target."""
        ...

    def emit(self, events: Sequence[E_contra]) -> None: ...

    def seconds_until_flush(self) -> float | None:
        """When a buffered batch is due, so the tool wakes up for it; ``None`` when nothing is buffered."""
        ...

    def tick(self) -> None:
        """Deliver a buffered batch whose time has come."""
        ...

    def alive(self) -> bool:
        """``False`` once the target is gone, which should end the tool."""
        ...

    def close(self) -> None: ...


class StdoutEventSink:
    """One compact JSON object per line, from ``event.to_json()``, flushed after every batch."""

    name = "stdout"

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def describe(self) -> str:
        return "stdout (one JSON event per line)"

    def emit(self, events: Sequence[StreamEvent]) -> None:
        stream = self._stream or sys.stdout
        for event in events:
            stream.write(json.dumps(event.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()

    def seconds_until_flush(self) -> float | None:
        return None

    def tick(self) -> None:
        pass

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class EventEnvelope[E: StreamEvent]:
    """How a batch of events becomes the text of one message.

    The message is ``<tag attributes seq="first-last">``, one JSON line per event, and ``</tag>``. ``seq`` is the
    single number when the batch holds one sequence number.

    - ``payload`` turns an event into the object of its line; ``event.to_json()`` by default.
    - The first batch that holds an event of kind ``announce_on`` (any first batch, when it is ``None``) also carries
      ``announce_attributes`` in its head, and its events pass through ``announced``. Use it for long values, such as
      paths, that the receiver needs once.
    - Above ``max_bytes`` the body becomes ``{"truncated":true,"events":n,"kinds":{kind: count},"read":record}``,
      where ``read`` is present only with a ``record``. ``record`` also names, in a failed send's status line, where
      the events can still be read.
    """

    tag: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    payload: Callable[[E], Mapping[str, Any]] | None = None
    announce_on: str | None = None
    announce_attributes: Mapping[str, str] = field(default_factory=dict)
    announced: Callable[[E], E] | None = None
    record: str | None = None
    max_bytes: int = MAX_CONTENT_BYTES

    def __post_init__(self) -> None:
        if not self.tag or not all(c.isalnum() or c in "-_." for c in self.tag):
            raise ValueError(f"The envelope tag {self.tag!r} must be letters, digits, '-', '_' or '.'.")
        if "seq" in self.attributes or "seq" in self.announce_attributes:
            raise ValueError("The envelope sets the seq attribute itself.")

    def format(self, events: Sequence[E], *, announce: bool = False) -> str:
        """The text of one message for a non-empty batch."""
        seqs = [e.seq for e in events]
        seq = str(seqs[0]) if min(seqs) == max(seqs) else f"{min(seqs)}-{max(seqs)}"
        attributes = {**self.attributes, "seq": seq}
        if announce:
            attributes.update(self.announce_attributes)
            if self.announced is not None:
                events = [self.announced(e) for e in events]
        rendered = "".join(f' {name}="{escape(value)}"' for name, value in attributes.items())
        head = f"<{self.tag}{rendered}>"
        tail = f"</{self.tag}>"
        body = "\n".join(self._line(e) for e in events)
        content = f"{head}\n{body}\n{tail}"
        if len(content.encode("utf-8")) <= self.max_bytes:
            return content
        counts: dict[str, int] = {}
        for event in events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        summary: dict[str, Any] = {"truncated": True, "events": len(events), "kinds": counts}
        if self.record is not None:
            summary["read"] = self.record
        return f"{head}\n{json.dumps(summary, separators=(',', ':'))}\n{tail}"

    def _line(self, event: E) -> str:
        payload = self.payload(event) if self.payload is not None else event.to_json()
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class MessageEventSink[E: StreamEvent]:
    """The ``claude-message`` stream: batches of events as user messages to one session.

    - ``sender`` is the receiver's rate-limit key, for example ``"my-watch:<pid>"``; ``sender_name`` is the name the
      receiver's terminal shows. Messages go through a :class:`RateLimitedSender`, which merges what it cannot send
      yet into the next message.
    - An event whose kind is in ``flush_on``, such as the tool's last event, sends the batch at once.
    - ``status`` receives one line, without a trailing newline, for every problem; print it to stderr.
    - :meth:`alive` turns ``False`` when the socket refuses a message, or when ``session_alive`` (by default: the
      current session can still receive messages) says no, checked every ``liveness_interval`` seconds.
    - When a send raises :class:`UnsupportedProtocol` or :class:`MessagingUnavailable`, for example after a Claude
      Code update changed the protocol, this batch and every later one go to ``fallback``, by default stdout.
    """

    name = "claude-message"

    def __init__(
        self,
        session: ActiveSession,
        envelope: EventEnvelope[E],
        *,
        sender: str,
        sender_name: str,
        status: Callable[[str], None],
        priority: Priority = "next",
        flush_on: Collection[str] = (),
        burst_wait: float = BURST_WAIT,
        liveness_interval: float = LIVENESS_INTERVAL,
        flush_timeout: float = FLUSH_TIMEOUT,
        fallback: EventSink[E] | None = None,
        rate_limiter: RateLimitedSender | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_alive: Callable[[], bool] | None = None,
    ) -> None:
        if session.messaging_socket is None:
            raise MessagingUnavailable("the session has messaging turned off")
        self.session = session
        self.envelope = envelope
        self.sender_name = sender_name
        self.priority: Priority = priority
        self.status = status
        self.flush_on = frozenset(flush_on)
        self.burst_wait = burst_wait
        self.liveness_interval = liveness_interval
        self.flush_timeout = flush_timeout
        self.rate_limiter = rate_limiter or RateLimitedSender(sender, sender_name=sender_name, priority=priority)
        self._fallback_sink = fallback
        self._socket = session.messaging_socket
        self._clock = clock
        self._session_alive = session_alive or _session_still_current
        self._buffer: list[E] = []
        self._deadline: float | None = None
        self._gone = False
        self._fallback: EventSink[E] | None = None
        self._last_liveness = clock()
        self._announced = False

    def describe(self) -> str:
        who = self.session.name or self.session.address
        return (
            f"claude-message to session {who!r} via {self._socket} "
            f"(priority {self.priority}, sender {self.sender_name!r})"
        )

    def emit(self, events: Sequence[E]) -> None:
        if not events:
            return
        if self._fallback is not None:
            self._fallback.emit(events)
            return
        self._buffer.extend(events)
        if self._deadline is None:
            self._deadline = self._clock() + self.burst_wait
        if any(e.kind in self.flush_on for e in events):
            self._send_buffer()

    def seconds_until_flush(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - self._clock())

    def tick(self) -> None:
        if self._deadline is not None and self._clock() >= self._deadline:
            self._send_buffer()

    def _send_buffer(self) -> None:
        events, self._buffer, self._deadline = self._buffer, [], None
        if not events or self._gone:
            return
        if self._fallback is not None:
            self._fallback.emit(events)
            return
        trigger = self.envelope.announce_on
        announce = not self._announced and (trigger is None or any(e.kind == trigger for e in events))
        self._announced = self._announced or announce
        content = self.envelope.format(events, announce=announce)
        try:
            self.rate_limiter.send(self._socket, content)
        except SessionGone:
            self._gone = True
        except (UnsupportedProtocol, MessagingUnavailable) as exc:
            # The session stopped taking messages from us. Keep the events flowing rather than dropping them.
            fallback: EventSink[E] = self._fallback_sink or StdoutEventSink()
            self.status(f"stream fallback: cannot message the session ({exc}); events now go to {fallback.name}")
            self._fallback = fallback
            fallback.emit(events)
        except (MessageTooLarge, ClaudeBlackboxError, OSError) as exc:
            where = f"; the events are in {self.envelope.record}" if self.envelope.record is not None else ""
            self.status(f"could not message the session: {exc}{where}")

    def alive(self) -> bool:
        if self._gone:
            return False
        now = self._clock()
        if now - self._last_liveness >= self.liveness_interval:
            self._last_liveness = now
            if not self._session_alive():
                self._gone = True
        return not self._gone

    def close(self) -> None:
        """Send the buffered batch, and wait up to ``flush_timeout`` seconds for messages the rate limit held back."""
        self._send_buffer()
        deadline = self._clock() + self.flush_timeout
        while not self._gone and self.rate_limiter.pending_count(self._socket) and self._clock() < deadline:
            time.sleep(min(1.0, self.rate_limiter.seconds_until_next_token(self._socket) + 0.05))
            try:
                self.rate_limiter.flush()
            except SessionGone:
                self._gone = True
            except (ClaudeBlackboxError, OSError) as exc:
                self.status(f"could not message the session: {exc}")
                return


def _session_still_current() -> bool:
    session = current_session_or_none()
    return session is not None and session.can_receive_messages


def current_session_or_none() -> ActiveSession | None:
    """:func:`current_session`, with ``None`` also when the registry cannot be read."""
    try:
        return current_session()
    except (ClaudeBlackboxError, OSError):
        return None


def messaging_problem(session: ActiveSession | None) -> str | None:
    """Why ``session`` cannot take messages from this process, or ``None`` when it can."""
    if sys.platform == "win32":
        return "the claude-message stream is not supported on Windows"
    if session is None:
        if os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"):
            return (
                "CLAUDE_CODE_MESSAGING_SOCKET is set, but cc-blackbox found no live session record for it"
                " (the record is gone, or cc-blackbox judged its process not running)"
            )
        return "no Claude Code session found (CLAUDE_CODE_MESSAGING_SOCKET is unset, or the session record is gone)"
    if session.can_receive_messages:
        return None
    reasons = [r for r in session.compatibility.reasons if r.startswith("messaging")]
    if reasons:
        return "the session's messaging is not compatible with this cc-blackbox version: " + " ".join(reasons)
    if session.messaging_socket is None:
        return "the session has messaging turned off"
    return f"the session's messaging socket {session.messaging_socket} does not exist"


@dataclass(frozen=True, slots=True)
class EventStreamChoice:
    """The stream to use. ``session`` is set for ``claude-message``.

    ``fallback`` says why ``auto`` chose stdout although the process runs under a Claude Code session; it is ``None``
    outside a session, where stdout is simply the default, and for an explicit ``stdout``.
    """

    mode: Literal["stdout", "claude-message"]
    session: ActiveSession | None = None
    fallback: str | None = None


def choose_event_stream(mode: EventStreamMode, session: ActiveSession | None) -> EventStreamChoice:
    """Resolve ``mode`` against ``session``, usually :func:`current_session_or_none`.

    ``auto`` picks ``claude-message`` when the session can receive messages, else ``stdout``.

    Raises:
        MessagingUnavailable: ``mode`` is ``claude-message`` and the session cannot receive messages. The message is
            the reason from :func:`messaging_problem`.
        ValueError: ``mode`` is not one of :data:`EVENT_STREAM_MODES`.
    """
    if mode not in EVENT_STREAM_MODES:
        raise ValueError(f"The event stream mode must be one of {', '.join(EVENT_STREAM_MODES)}, not {mode!r}.")
    problem = messaging_problem(session)
    if mode == "claude-message" and problem is not None:
        raise MessagingUnavailable(problem)
    if mode != "stdout" and problem is None:
        return EventStreamChoice("claude-message", session)
    # A messaging socket in the environment means the process runs under a session even when no record was found.
    under_session = session is not None or bool(os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"))
    return EventStreamChoice("stdout", None, problem if mode == "auto" and under_session else None)
