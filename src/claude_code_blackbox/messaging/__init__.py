"""Claude Code's cross-session messaging: frames, transports and the sending API."""

from claude_code_blackbox.messaging.client import (
    MessagingTarget,
    RateLimitedSender,
    SentMessage,
    rename_session,
    send_message,
)
from claude_code_blackbox.messaging.protocol import Priority, build_envelope, percent_encode_address

__all__ = [
    "MessagingTarget",
    "Priority",
    "RateLimitedSender",
    "SentMessage",
    "build_envelope",
    "percent_encode_address",
    "rename_session",
    "send_message",
]
