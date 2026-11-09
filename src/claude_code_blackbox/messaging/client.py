"""Send messages and renames to running Claude Code sessions."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from claude_code_blackbox.errors import MessagingUnavailable, UnsupportedProtocol
from claude_code_blackbox.messaging.protocol import (
    Priority,
    build_envelope,
    build_rename_frame,
    build_user_frame,
    encode_frame,
    percent_encode_address,
)
from claude_code_blackbox.messaging.transport import default_transport
from claude_code_blackbox.sessions.active import SUPPORTED_PEER_PROTOCOL, ActiveSession

__all__ = [
    "MessagingTarget",
    "RateLimitedSender",
    "SentMessage",
    "rename_session",
    "send_message",
]

MessagingTarget = ActiveSession | os.PathLike[str] | str
"""A session from :func:`list_active_sessions`, or the path of a messaging socket."""


@dataclass(frozen=True, slots=True)
class SentMessage:
    """A frame that was written to a session's socket. Writing it does not mean the model saw it."""

    msg_id: str
    socket_path: str
    bytes_written: int


def _socket_path(target: MessagingTarget) -> str:
    if not isinstance(target, ActiveSession):
        return os.fspath(target)
    if target.messaging_socket is None:
        raise MessagingUnavailable(
            f"The session {target.address} has messaging turned off; its record names no socket."
        )
    if target.peer_protocol != SUPPORTED_PEER_PROTOCOL:
        raise UnsupportedProtocol(
            f"The session {target.address} speaks peer protocol {target.peer_protocol}, and this SDK speaks only "
            f"protocol {SUPPORTED_PEER_PROTOCOL}. Upgrade the SDK, or pass the socket path to send anyway."
        )
    return os.fspath(target.messaging_socket)


def send_message(
    target: MessagingTarget,
    content: str,
    *,
    sender: str,
    sender_name: str | None = None,
    priority: Priority = "next",
    msg_id: str | None = None,
    timeout: float = 5.0,
) -> SentMessage:
    """Deliver ``content`` to a running session as a message from another Claude session.

    The receiving model sees the text after a header saying another Claude session sent it, and before a footer
    saying it did not come from the user. Delivery is best effort. The receiver accepts the frame without answering,
    and may then:

    - drop it when ``sender`` exceeds 30 messages in a burst or 1 message per 2 seconds sustained,
    - drop it when the same content arrived within the last 30 seconds, so put a sequence number or time into it,
    - drop it when 50 messages already wait in its queue,
    - hold it until the user approves it, which a session in bypass-permissions mode does by default.

    ``sender`` goes into the frame's ``from`` field. The model never sees it; the receiver uses it as the key of its
    rate limit and duplicate check, so give every independent sending process its own label. With ``sender_name``,
    the content is wrapped in a ``<cross-session-message from="did:<sender>" from-name="<sender_name>">`` envelope
    (see :func:`build_envelope`), and the receiver's terminal shows the message under that name. Without it, the
    content arrives as it is and the model does not learn who sent it.

    A ``target`` that is an :class:`ActiveSession` must name a socket and speak peer protocol 1. A path is used as it
    is.

    Raises:
        MessagingUnavailable: the session has messaging turned off, or the platform is Windows.
        UnsupportedProtocol: the session speaks a peer protocol other than 1.
        MessageTooLarge: the frame exceeds 1 MiB; nothing was sent.
        SessionGone: nobody listens on the socket.
        MessagingError: any other failure to write.
        ValueError: an argument is empty or malformed.
    """
    socket_path = _socket_path(target)
    if sender_name is not None:
        content = build_envelope(content, from_address=f"did:{percent_encode_address(sender)}", from_name=sender_name)
    frame = build_user_frame(content, sender=sender, priority=priority, msg_id=msg_id)
    payload = encode_frame(frame)
    default_transport().send(socket_path, payload, timeout=timeout)
    return SentMessage(msg_id=frame["msg_id"], socket_path=socket_path, bytes_written=len(payload))


def rename_session(target: MessagingTarget, name: str, *, timeout: float = 5.0) -> None:
    """Ask a running session to rename itself to ``name``.

    The session may still change the name: when another live session in its profile already holds the name, Claude
    Code gives the newcomer a different one. Read the session's record again to see the result.

    Raises:
        The same errors as :func:`send_message`.
    """
    socket_path = _socket_path(target)
    default_transport().send(socket_path, encode_frame(build_rename_frame(name)), timeout=timeout)


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float
    pending: list[str] = field(default_factory=list)


class RateLimitedSender:
    """Send messages under a client-side rate limit that stays below the receiver's.

    The receiver drops messages above 30 in a burst or 1 per 2 seconds for each ``from`` label, and it does not tell
    the sender. This class keeps a token bucket for each target socket, 25 messages refilled at 0.4 per second by
    default. When the bucket is empty, :meth:`send` queues the message. The next call that finds a token, from
    :meth:`send` or :meth:`flush`, sends every queued message for that target as one message, joined by ``separator``.
    Coalescing also keeps a burst of identical texts from being dropped by the receiver's duplicate check.

    A queued message is lost when sending it fails. Instances are thread-safe.
    """

    def __init__(
        self,
        sender: str,
        *,
        sender_name: str | None = None,
        capacity: int = 25,
        refill_per_second: float = 0.4,
        priority: Priority = "next",
        timeout: float = 5.0,
        separator: str = "\n\n",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("The capacity must be at least 1 and the refill rate must be positive.")
        self.sender = sender
        self.sender_name = sender_name
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.priority: Priority = priority
        self.timeout = timeout
        self.separator = separator
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, socket_path: str) -> _Bucket:
        now = self._clock()
        bucket = self._buckets.get(socket_path)
        if bucket is None:
            bucket = self._buckets[socket_path] = _Bucket(tokens=float(self.capacity), updated=now)
        else:
            bucket.tokens = min(float(self.capacity), bucket.tokens + (now - bucket.updated) * self.refill_per_second)
            bucket.updated = now
        return bucket

    def _drain(self, socket_path: str, bucket: _Bucket) -> SentMessage | None:
        if not bucket.pending or bucket.tokens < 1:
            return None
        content = self.separator.join(bucket.pending)
        bucket.pending.clear()
        bucket.tokens -= 1
        return send_message(
            socket_path,
            content,
            sender=self.sender,
            sender_name=self.sender_name,
            priority=self.priority,
            timeout=self.timeout,
        )

    def send(self, target: MessagingTarget, content: str) -> SentMessage | None:
        """Send ``content`` now, together with anything queued for the target, or queue it when no token is left.

        Returns the sent message, or ``None`` when ``content`` was queued.

        Raises:
            The errors of :func:`send_message`. The target checks run before anything is queued.
        """
        if not content:
            raise ValueError("The message content is empty. The receiver drops empty messages.")
        socket_path = _socket_path(target)
        with self._lock:
            bucket = self._bucket(socket_path)
            bucket.pending.append(content)
            return self._drain(socket_path, bucket)

    def flush(self) -> list[SentMessage]:
        """Send the queued messages of every target that has a token, and return what was sent."""
        sent: list[SentMessage] = []
        with self._lock:
            for socket_path in list(self._buckets):
                message = self._drain(socket_path, self._bucket(socket_path))
                if message is not None:
                    sent.append(message)
        return sent

    def pending_count(self, target: MessagingTarget) -> int:
        """Return how many messages wait for a token for ``target``."""
        with self._lock:
            bucket = self._buckets.get(_socket_path(target))
            return len(bucket.pending) if bucket is not None else 0

    def seconds_until_next_token(self, target: MessagingTarget) -> float:
        """Return how long the next message to ``target`` would wait for a token; 0 when one is available."""
        with self._lock:
            bucket = self._bucket(_socket_path(target))
            return max(0.0, (1 - bucket.tokens) / self.refill_per_second)
