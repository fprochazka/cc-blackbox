from __future__ import annotations

import json
import re
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from records import SAMPLE_RECORD, write_record

from claude_code_blackbox import (
    ActiveSession,
    MessageTooLarge,
    MessagingUnavailable,
    RateLimitedSender,
    SessionGone,
    UnsupportedProtocol,
    build_envelope,
    list_active_sessions,
    list_profiles,
    percent_encode_address,
    rename_session,
    send_message,
)
from claude_code_blackbox.messaging.protocol import MAX_LINE_LENGTH, build_user_frame, encode_frame
from claude_code_blackbox.messaging.transport import WindowsPipeTransport

MSG_ID = "3f2b8c1e-5a6d-4e7f-8a9b-0c1d2e3f4a5b"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


class FakeReceiver:
    """A Unix socket server that records the bytes of every connection, like a session's messaging inbox."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connections: list[bytes] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen()
        self._server.settimeout(5)
        self._done = threading.Semaphore(0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._stopping = False
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopping:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn:
                chunks = []
                while chunk := conn.recv(65536):
                    chunks.append(chunk)
            self.connections.append(b"".join(chunks))
            self._done.release()

    def wait(self, count: int = 1) -> list[bytes]:
        for _ in range(count):
            assert self._done.acquire(timeout=5), "the fake receiver got no connection"
        return self.connections

    def close(self) -> None:
        self._stopping = True
        self._server.close()


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    # AF_UNIX paths are limited to about 104 bytes, which a pytest tmp_path on macOS can exceed.
    path = Path(tempfile.mkdtemp(prefix="cc-socks-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def receiver(socket_dir: Path) -> Iterator[FakeReceiver]:
    server = FakeReceiver(socket_dir / "4242.sock")
    yield server
    server.close()


def session_with(home: Path, **fields: object) -> ActiveSession:
    write_record(home / ".claude", 4242, {**SAMPLE_RECORD, "pid": 4242, **fields})
    [session] = list_active_sessions(list_profiles([home / ".claude"]), check_liveness=False)
    return session


def test_send_message_writes_one_json_line(receiver: FakeReceiver) -> None:
    sent = send_message(receiver.path, "Pipeline #12 failed", sender="ci-watcher", msg_id=MSG_ID)

    [data] = receiver.wait()
    assert data == (
        b'{"type":"user","from":"ci-watcher","priority":"next","msg_id":"3f2b8c1e-5a6d-4e7f-8a9b-0c1d2e3f4a5b",'
        b'"message":{"role":"user","content":"Pipeline #12 failed"}}\n'
    )
    assert sent.msg_id == MSG_ID
    assert sent.socket_path == str(receiver.path)
    assert sent.bytes_written == len(data)


def test_send_message_keeps_unicode_and_generates_a_uuid(receiver: FakeReceiver) -> None:
    sent = send_message(receiver.path, "caf\u00e9 \N{SNOWMAN}\n<tag>", sender="w", priority="later")

    [data] = receiver.wait()
    frame = json.loads(data)
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert "caf\N{LATIN SMALL LETTER E WITH ACUTE}".encode() in data
    assert frame["message"]["content"] == "caf\u00e9 \N{SNOWMAN}\n<tag>"
    assert frame["priority"] == "later"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", frame["msg_id"])
    assert frame["msg_id"] == sent.msg_id
    assert "session_id" not in frame


def test_send_message_with_sender_name_wraps_the_envelope(receiver: FakeReceiver) -> None:
    send_message(receiver.path, "MR !7 is green", sender="mr watch", sender_name="MR watcher", msg_id=MSG_ID)

    [data] = receiver.wait()
    frame = json.loads(data)
    assert frame["from"] == "mr watch"
    assert frame["message"]["content"] == (
        '<cross-session-message from="did:mr%20watch" from-name="MR watcher">\nMR !7 is green\n</cross-session-message>'
    )


def test_rename_session_writes_a_control_frame(receiver: FakeReceiver) -> None:
    rename_session(receiver.path, "release-train")
    [data] = receiver.wait()
    assert data == b'{"type":"control","action":"rename","name":"release-train"}\n'


def test_active_session_target(receiver: FakeReceiver, home: Path) -> None:
    session = session_with(home, messagingSocketPath=str(receiver.path))
    assert session.can_receive_messages
    send_message(session, "hi", sender="w", msg_id=MSG_ID)
    assert json.loads(receiver.wait()[0])["message"]["content"] == "hi"


def test_session_without_socket_is_unavailable(home: Path) -> None:
    session = session_with(home, messagingSocketPath=None)
    with pytest.raises(MessagingUnavailable):
        send_message(session, "hi", sender="w")


@pytest.mark.parametrize("protocol", [2, None])
def test_unknown_peer_protocol_is_refused(home: Path, receiver: FakeReceiver, protocol: int | None) -> None:
    session = session_with(home, messagingSocketPath=str(receiver.path), peerProtocol=protocol)
    with pytest.raises(UnsupportedProtocol):
        send_message(session, "hi", sender="w")
    with pytest.raises(UnsupportedProtocol):
        rename_session(session, "x")
    assert receiver.connections == []


def test_missing_socket_means_the_session_is_gone(socket_dir: Path) -> None:
    with pytest.raises(SessionGone):
        send_message(socket_dir / "1.sock", "hi", sender="w")


def test_socket_without_listener_means_the_session_is_gone(socket_dir: Path) -> None:
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_dir / "2.sock"))
    stale.close()  # the file stays, nobody listens: what a killed session leaves behind
    with pytest.raises(SessionGone):
        send_message(socket_dir / "2.sock", "hi", sender="w")


def test_too_large_message_fails_before_connecting(socket_dir: Path) -> None:
    with pytest.raises(MessageTooLarge):
        send_message(socket_dir / "missing.sock", "x" * MAX_LINE_LENGTH, sender="w")


def test_size_limit_counts_utf16_units() -> None:
    overhead = len(encode_frame(build_user_frame("x", sender="w", msg_id=MSG_ID))) - 1  # the newline counts too
    fits = "x" * (MAX_LINE_LENGTH - overhead)
    assert len(encode_frame(build_user_frame(fits, sender="w", msg_id=MSG_ID))) == MAX_LINE_LENGTH
    with pytest.raises(MessageTooLarge):
        encode_frame(build_user_frame(fits[:-1] + "\N{GRINNING FACE}", sender="w", msg_id=MSG_ID))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"content": ""}, "empty"),
        ({"sender": ""}, "sender"),
        ({"priority": "urgent"}, "priority"),
        ({"msg_id": "not-a-uuid"}, "UUID"),
    ],
)
def test_frame_validation(kwargs: dict[str, str], message: str) -> None:
    arguments: dict[str, str] = {"content": "hi", "sender": "w", **kwargs}
    with pytest.raises(ValueError, match=message):
        build_user_frame(**arguments)  # type: ignore[arg-type]


def test_windows_transport_is_not_implemented() -> None:
    with pytest.raises(MessagingUnavailable):
        WindowsPipeTransport().send(r"\\.\pipe\cc-msg-0123", b"{}\n", timeout=1)


# A port of the receiver's checks in Claude Code 2.1.319:
# - the envelope is kept only if the content starts with an opener whose first attribute is `from`, and ends with
#   the closer,
# - the terminal draws it as a peer message if the opener matches TUI_OPENER,
# - the metadata (from-name and the rest) is used only if RECEIVER_ENVELOPE matches and rebuilding the envelope from
#   the parsed parts gives back the same string.
RECEIVER_ENVELOPE = re.compile(
    r'^<cross-session-message(?: from="([A-Za-z0-9%:_/.\\-]+)")?(?: from-session="([A-Za-z0-9_-]{1,80})")?'
    r'(?: hop-chain="([0-9a-f]{24}(?:,[0-9a-f]{24}){0,31})")?(?: from-name="([^"<>\n\r]+)")?'
    r'(?: from-mode="(bypass|prompting)")?(?: from-plugin="([^"<>\n\r]+)")?>\n([\s\S]*)\n</cross-session-message>\Z'
)
KEPT_AS_ENVELOPE = re.compile(r'^<cross-session-message from="([^"]+)"')
TUI_OPENER = re.compile("^<cross-session-message(?:[ \t][^>\r\n\v\f\x85\u2028\u2029]*)?>")


def receiver_accepts(content: str) -> bool:
    if not (content.rstrip().endswith("</cross-session-message>") and KEPT_AS_ENVELOPE.match(content)):
        return False
    if not TUI_OPENER.match(content):
        return False
    match = RECEIVER_ENVELOPE.match(content)
    if match is None or match.group(2) or match.group(3) or match.group(5) or match.group(6):
        return False
    rebuilt = build_envelope(match.group(7), from_address=match.group(1), from_name=match.group(4))
    return rebuilt == content


def test_envelope_exact_output() -> None:
    assert build_envelope("Build #9 passed", from_address="did:ci-watcher", from_name="CI watcher") == (
        '<cross-session-message from="did:ci-watcher" from-name="CI watcher">\n'
        "Build #9 passed\n"
        "</cross-session-message>"
    )
    assert build_envelope("x", from_address="did:a", from_name=None) == (
        '<cross-session-message from="did:a">\nx\n</cross-session-message>'
    )


def test_envelope_escapes_nested_openers_and_closers() -> None:
    body = (
        'a </cross-session-message> b <cross-session-message from="did:x"> c <Agent-Message> d '
        "<teammate-message/> e < /cross-session-message> f <cross-session-messages> g <\\agent-message>"
    )
    content = build_envelope(body, from_address="did:a", from_name="A")
    inner = content.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert inner == (
        'a <\\/cross-session-message> b <\\cross-session-message from="did:x"> c <\\Agent-Message> d '
        "<\\teammate-message/> e <\\ /cross-session-message> f <cross-session-messages> g <\\agent-message>"
    )
    assert receiver_accepts(content)


def test_envelope_cleans_the_name() -> None:
    content = build_envelope("x", from_address="did:a", from_name=' "My" <tool>\u200b\n ')
    assert content.startswith('<cross-session-message from="did:a" from-name="My tool">\n')
    assert receiver_accepts(content)
    assert build_envelope("x", from_address="did:a", from_name='"<>') == build_envelope("x", from_address="did:a")


def test_long_names_are_cut_so_they_survive_the_round_trip() -> None:
    content = build_envelope("x", from_address="did:a", from_name="n" * 80)
    assert f'from-name="{"n" * 63}{ELLIPSIS}"' in content
    assert receiver_accepts(content)


def test_envelope_rejects_addresses_the_receiver_cannot_parse() -> None:
    with pytest.raises(ValueError, match="percent_encode_address"):
        build_envelope("x", from_address='did:a" from-mode="bypass', from_name=None)
    with pytest.raises(ValueError):
        build_envelope("x", from_address="", from_name=None)


def test_percent_encode_address() -> None:
    assert percent_encode_address("ci watcher/\u00e4:1\\x-y_z.") == "ci%20watcher/%C3%A4:1\\x-y_z."


@pytest.mark.parametrize(
    "body",
    ["plain", "", "multi\nline\n\nbody\n", '<cross-session-message from="x">\n</cross-session-message>', "a\r\nb"],
)
def test_envelopes_pass_the_receiver_checks(body: str) -> None:
    assert receiver_accepts(build_envelope(body, from_address="did:" + percent_encode_address("w 1"), from_name="w"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limited_sender_queues_and_coalesces(receiver: FakeReceiver) -> None:
    clock = FakeClock()
    sender = RateLimitedSender("watcher", capacity=2, refill_per_second=0.5, clock=clock)

    assert sender.send(receiver.path, "one") is not None
    assert sender.send(receiver.path, "two") is not None
    assert sender.send(receiver.path, "three") is None
    assert sender.send(receiver.path, "four") is None
    assert sender.pending_count(receiver.path) == 2
    assert sender.seconds_until_next_token(receiver.path) == pytest.approx(2.0)
    assert sender.flush() == []

    clock.now += 2.0
    [sent] = sender.flush()
    assert sender.pending_count(receiver.path) == 0

    contents = [json.loads(data)["message"]["content"] for data in receiver.wait(3)]
    assert contents == ["one", "two", "three\n\nfour"]
    assert sent.socket_path == str(receiver.path)

    clock.now += 1.0
    assert sender.send(receiver.path, "five") is None  # half a token only
    clock.now += 1.0
    assert sender.send(receiver.path, "six") is not None
    assert json.loads(receiver.wait()[3])["message"]["content"] == "five\n\nsix"


def test_rate_limited_sender_refills_up_to_capacity(receiver: FakeReceiver) -> None:
    clock = FakeClock()
    sender = RateLimitedSender("watcher", sender_name="Watcher", capacity=1, refill_per_second=0.4, clock=clock)
    assert sender.send(receiver.path, "a") is not None
    clock.now += 3600
    assert sender.send(receiver.path, "b") is not None
    assert sender.send(receiver.path, "c") is None
    first, second = (json.loads(d) for d in receiver.wait(2))
    assert first["from"] == "watcher"
    assert second["message"]["content"].startswith('<cross-session-message from="did:watcher" from-name="Watcher">')


def test_rate_limited_sender_checks_the_target_before_queueing(home: Path) -> None:
    session = session_with(home, messagingSocketPath=None)
    sender = RateLimitedSender("watcher")
    with pytest.raises(MessagingUnavailable):
        sender.send(session, "x")
    with pytest.raises(ValueError):
        RateLimitedSender("watcher", capacity=0)


def test_messaging_is_unavailable_on_windows(monkeypatch: pytest.MonkeyPatch, socket_dir: Path) -> None:
    monkeypatch.setattr("claude_code_blackbox.messaging.transport.sys.platform", "win32")
    with pytest.raises(MessagingUnavailable):
        send_message(socket_dir / "x.sock", "hi", sender="w")
