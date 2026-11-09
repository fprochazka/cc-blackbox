"""Write frames to a session's messaging endpoint."""

from __future__ import annotations

import errno
import socket
import sys
from typing import Protocol

from claude_code_blackbox.errors import MessagingError, MessagingUnavailable, SessionGone

__all__ = [
    "Transport",
    "UnixSocketTransport",
    "WindowsPipeTransport",
    "default_transport",
]

_GONE_ERRNOS = {errno.ENOENT, errno.ECONNREFUSED, errno.ENOTSOCK}


class Transport(Protocol):
    """Writes one payload to a messaging endpoint on a fresh connection, then closes the connection."""

    def send(self, address: str, payload: bytes, *, timeout: float) -> None: ...


class UnixSocketTransport:
    """The Linux and macOS transport: a Unix stream socket per session, with no authentication.

    Only filesystem permissions protect the socket (a ``0700`` dir and a ``0600`` socket), so any process of the same
    user can write to it. The receiver closes a connection that sends no complete line within 30 seconds.
    """

    def send(self, address: str, payload: bytes, *, timeout: float) -> None:
        """Connect to the socket at ``address``, write ``payload``, and close the connection.

        Raises:
            SessionGone: the socket does not exist or nobody listens on it.
            MessagingError: any other failure, such as a timeout.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(address)
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
        except TimeoutError as e:
            raise MessagingError(f"Writing to the messaging socket {address} timed out after {timeout} s.") from e
        except OSError as e:
            if e.errno in _GONE_ERRNOS:
                raise SessionGone(
                    f"No session listens on the messaging socket {address}; it has probably ended."
                ) from e
            raise MessagingError(f"Cannot write to the messaging socket {address}: {e}") from e
        finally:
            sock.close()


class WindowsPipeTransport:
    """The Windows transport, which this version of the SDK does not implement.

    Claude Code on Windows uses a named pipe and requires a token read from the session's key file.
    """

    def send(self, address: str, payload: bytes, *, timeout: float) -> None:
        """Always raise :class:`MessagingUnavailable`."""
        raise MessagingUnavailable(
            "Messaging on Windows needs the named-pipe transport, which this SDK version does not implement."
        )


def default_transport() -> Transport:
    """Return the transport for the current platform."""
    return WindowsPipeTransport() if sys.platform == "win32" else UnixSocketTransport()
