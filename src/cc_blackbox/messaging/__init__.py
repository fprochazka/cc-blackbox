"""Claude Code's cross-session messaging: frames, transports and the sending API."""

from cc_blackbox.messaging.client import (
    MessagingTarget,
    RateLimitedSender,
    SentMessage,
    rename_session,
    send_message,
)
from cc_blackbox.messaging.event_stream import (
    EVENT_STREAM_MODES,
    EventEnvelope,
    EventSink,
    EventStreamChoice,
    EventStreamMode,
    MessageEventSink,
    StdoutEventSink,
    StreamEvent,
    choose_event_stream,
    current_session_or_none,
    messaging_problem,
)
from cc_blackbox.messaging.protocol import Priority, build_envelope, percent_encode_address

__all__ = [
    "EVENT_STREAM_MODES",
    "EventEnvelope",
    "EventSink",
    "EventStreamChoice",
    "EventStreamMode",
    "MessageEventSink",
    "MessagingTarget",
    "Priority",
    "RateLimitedSender",
    "SentMessage",
    "StdoutEventSink",
    "StreamEvent",
    "build_envelope",
    "choose_event_stream",
    "current_session_or_none",
    "messaging_problem",
    "percent_encode_address",
    "rename_session",
    "send_message",
]
