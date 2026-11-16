"""The event stream: the stream choice, the stdout sink, and the message sink against a real Unix socket."""

from __future__ import annotations

import io
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from test_messaging import FakeReceiver, session_with

from cc_blackbox import (
    EventEnvelope,
    MessageEventSink,
    MessageTooLarge,
    MessagingUnavailable,
    RateLimitedSender,
    StdoutEventSink,
    UnsupportedProtocol,
    choose_event_stream,
    messaging_problem,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Claude Code messaging has no Windows transport")


@dataclass(frozen=True)
class Event:
    kind: str
    seq: int
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Mapping[str, Any]:
        return {"kind": self.kind, "seq": self.seq, "at": "2026-11-09T10:00:00Z", "data": self.data}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Status:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


class RefusingSender(RateLimitedSender):
    def __init__(self, error: Exception) -> None:
        super().__init__("refusing")
        self.error = error
        self.calls = 0

    def send(self, target: object, content: str) -> None:
        self.calls += 1
        raise self.error


@pytest.fixture
def receiver(socket_dir: Path) -> Any:
    server = FakeReceiver(socket_dir / "4242.sock")
    yield server
    server.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def short_payload(event: Event) -> Mapping[str, Any]:
    return {"kind": event.kind, "seq": event.seq, "data": event.data}


ENVELOPE: EventEnvelope[Event] = EventEnvelope(
    "test-watcher", {"resource": "thing 42"}, payload=short_payload, record="/tmp/x/events.jsonl"
)


def sink_for(
    home: Path, receiver: FakeReceiver, clock: Clock, status: Status | None = None, **kwargs: Any
) -> MessageEventSink[Event]:
    session = session_with(home, messagingSocketPath=str(receiver.path))
    options: dict[str, Any] = {"envelope": ENVELOPE, "sender": "test-watch:1", "sender_name": "test-watch thing 42"}
    options.update(kwargs)
    return MessageEventSink(session, status=status or Status(), clock=clock, **options)


def frames(receiver: FakeReceiver, count: int) -> list[dict[str, Any]]:
    return [json.loads(data) for data in receiver.wait(count)]


def test_a_burst_goes_out_as_one_message_after_the_burst_wait(home: Path, receiver: FakeReceiver, clock: Clock) -> None:
    sink = sink_for(home, receiver, clock)
    assert sink.describe() == (
        f"claude-message to session 'example-project-a1' via {receiver.path} "
        "(priority next, sender 'test-watch thing 42')"
    )
    sink.emit([Event("pushed", 3, {"to": "b" * 40})])
    sink.emit([Event("thread_created", 4, {"discussion_id": "abc"})])
    sink.tick()
    assert sink.seconds_until_flush() == 3.0
    assert receiver.connections == []
    clock.advance(3)
    sink.tick()
    assert sink.seconds_until_flush() is None
    [frame] = frames(receiver, 1)
    assert frame["type"] == "user" and frame["priority"] == "next" and frame["from"] == "test-watch:1"
    content = frame["message"]["content"]
    assert content.startswith('<cross-session-message from="did:test-watch:1" from-name="test-watch thing 42">')
    assert '<test-watcher resource="thing 42" seq="3-4">\n{"kind":"pushed"' in content
    assert '\n{"kind":"thread_created","seq":4,"data":{"discussion_id":"abc"}}\n</test-watcher>' in content


def test_only_the_first_batch_with_the_announcing_kind_carries_the_announcement(
    home: Path, receiver: FakeReceiver, clock: Clock
) -> None:
    envelope = replace(
        ENVELOPE,
        announce_on="watch_started",
        announce_attributes={"dir": "/tmp/x", "changelog": "/tmp/x/events.jsonl"},
        announced=lambda e: replace(e, data={k: v for k, v in e.data.items() if k != "dir"}),
    )
    sink = sink_for(home, receiver, clock, envelope=envelope)
    # The first batch has no watch_started, and the fourth repeats it after a restart: neither announces anything.
    batches = [
        [Event("pushed", 1)],
        [Event("watch_started", 2, {"dir": "/tmp/x", "iid": 42}), Event("pushed", 3)],
        [Event("pushed", 4)],
        [Event("watch_started", 5, {"dir": "/tmp/x"})],
    ]
    for batch in batches:
        sink.emit(batch)
        clock.advance(3)
        sink.tick()
    contents = [f["message"]["content"] for f in frames(receiver, 4)]
    heads = [c.split("<test-watcher ", 1)[1].split(">", 1)[0] for c in contents]
    assert heads == [
        'resource="thing 42" seq="1"',
        'resource="thing 42" seq="2-3" dir="/tmp/x" changelog="/tmp/x/events.jsonl"',
        'resource="thing 42" seq="4"',
        'resource="thing 42" seq="5"',
    ]
    assert '{"kind":"watch_started","seq":2,"data":{"iid":42}}' in contents[1]
    assert '{"kind":"watch_started","seq":5,"data":{"dir":"/tmp/x"}}' in contents[3]


def test_without_an_announcing_kind_the_first_message_announces(
    home: Path, receiver: FakeReceiver, clock: Clock
) -> None:
    envelope = replace(ENVELOPE, announce_attributes={"log": "/tmp/a&b"})
    sink = sink_for(home, receiver, clock, envelope=envelope)
    for seq in (1, 2):
        sink.emit([Event("pushed", seq)])
        clock.advance(3)
        sink.tick()
    contents = [f["message"]["content"] for f in frames(receiver, 2)]
    assert '<test-watcher resource="thing 42" seq="1" log="/tmp/a&amp;b">' in contents[0]
    assert '<test-watcher resource="thing 42" seq="2">' in contents[1]


def test_a_flush_kind_is_sent_at_once_with_the_priority(home: Path, receiver: FakeReceiver, clock: Clock) -> None:
    sink = sink_for(home, receiver, clock, priority="later", flush_on={"watch_ended"})
    sink.emit([Event("state_changed", 9), Event("watch_ended", 10)])
    [frame] = frames(receiver, 1)
    assert frame["priority"] == "later" and 'seq="9-10"' in frame["message"]["content"]
    sink.close()


def test_a_gone_session_ends_the_stream(home: Path, receiver: FakeReceiver, clock: Clock) -> None:
    sink = sink_for(home, receiver, clock)
    receiver.close()
    receiver.path.unlink()  # a closed server socket still accepts while its thread waits in accept()
    sink.emit([Event("pushed", 1)])
    clock.advance(3)
    sink.tick()
    assert not sink.alive()


def test_liveness_is_rechecked_every_interval(home: Path, receiver: FakeReceiver, clock: Clock) -> None:
    alive = [True]
    sink = sink_for(home, receiver, clock, session_alive=lambda: alive[0])
    alive[0] = False
    assert sink.alive()
    clock.advance(30)
    assert not sink.alive()


def test_oversized_batches_become_a_count_per_kind() -> None:
    events = [Event("thread_updated", i, {"body": "x" * 2000}) for i in range(1, 60)]
    content = ENVELOPE.format(events)
    assert len(content.encode()) < ENVELOPE.max_bytes
    assert content == (
        '<test-watcher resource="thing 42" seq="1-59">\n'
        '{"truncated":true,"events":59,"kinds":{"thread_updated":59},"read":"/tmp/x/events.jsonl"}\n'
        "</test-watcher>"
    )
    assert '"read"' not in replace(ENVELOPE, record=None).format(events)


def test_the_default_payload_is_the_events_json() -> None:
    assert EventEnvelope[Event]("w").format([Event("pushed", 7)]) == (
        '<w seq="7">\n{"kind":"pushed","seq":7,"at":"2026-11-09T10:00:00Z","data":{}}\n</w>'
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tag": "bad tag"}, "must be letters"),
        ({"tag": ""}, "must be letters"),
        ({"tag": "w", "attributes": {"seq": "1"}}, "seq attribute"),
        ({"tag": "w", "announce_attributes": {"seq": "1"}}, "seq attribute"),
    ],
)
def test_envelope_validation(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        EventEnvelope(**kwargs)


def test_a_protocol_change_mid_run_moves_events_to_the_fallback(
    home: Path, receiver: FakeReceiver, clock: Clock, capsys: pytest.CaptureFixture[str]
) -> None:
    status = Status()
    refusing = RefusingSender(UnsupportedProtocol("The session speaks peer protocol 2."))
    sink = sink_for(home, receiver, clock, status, rate_limiter=refusing, flush_on={"watch_ended"})
    sink.emit([Event("pushed", 1)])
    clock.advance(3)
    sink.tick()
    sink.emit([Event("watch_ended", 2)])
    sink.close()
    out = capsys.readouterr().out
    assert [json.loads(line)["seq"] for line in out.splitlines()] == [1, 2]
    assert status.lines == [
        "stream fallback: cannot message the session (The session speaks peer protocol 2.); events now go to stdout"
    ]
    assert refusing.calls == 1 and sink.alive()


def test_a_failed_send_names_the_record(home: Path, receiver: FakeReceiver, clock: Clock) -> None:
    status = Status()
    sink = sink_for(home, receiver, clock, status, rate_limiter=RefusingSender(MessageTooLarge("too large")))
    sink.emit([Event("pushed", 1)])
    clock.advance(3)
    sink.tick()
    assert status.lines == ["could not message the session: too large; the events are in /tmp/x/events.jsonl"]
    assert sink.alive()


def test_a_session_without_a_socket_cannot_get_a_message_sink(home: Path) -> None:
    session = session_with(home, messagingSocketPath=None)
    with pytest.raises(MessagingUnavailable, match="messaging turned off"):
        MessageEventSink(session, ENVELOPE, sender="s", sender_name="S", status=Status())


def test_stdout_sink_writes_one_json_line_per_event() -> None:
    out = io.StringIO()
    sink = StdoutEventSink(out)
    sink.emit([Event("pushed", 1, {"to": "café"}), Event("merged", 2)])
    assert out.getvalue() == (
        '{"kind":"pushed","seq":1,"at":"2026-11-09T10:00:00Z","data":{"to":"café"}}\n'
        '{"kind":"merged","seq":2,"at":"2026-11-09T10:00:00Z","data":{}}\n'
    )
    assert sink.alive() and sink.seconds_until_flush() is None and sink.describe().startswith("stdout")


def test_stream_choice_outside_a_session() -> None:
    assert choose_event_stream("auto", None).mode == "stdout"
    assert choose_event_stream("auto", None).fallback is None
    with pytest.raises(MessagingUnavailable, match="no Claude Code session found"):
        choose_event_stream("claude-message", None)
    with pytest.raises(ValueError, match="must be one of auto, stdout, claude-message"):
        choose_event_stream("pigeon", None)  # type: ignore[arg-type]


def test_a_socket_without_a_live_record_falls_back_with_a_reason(
    socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", str(socket_dir / "123.sock"))
    choice = choose_event_stream("auto", None)
    assert choice.mode == "stdout" and choice.fallback is not None and "no live session record" in choice.fallback
    assert choose_event_stream("stdout", None).fallback is None


def test_stream_choice_under_a_session(home: Path, receiver: FakeReceiver) -> None:
    session = session_with(home, messagingSocketPath=str(receiver.path))
    assert messaging_problem(session) is None
    chosen = choose_event_stream("auto", session)
    assert chosen.mode == "claude-message" and chosen.session is session and chosen.fallback is None
    assert choose_event_stream("claude-message", session).mode == "claude-message"
    assert choose_event_stream("stdout", session) == choose_event_stream("stdout", None)


def test_an_incompatible_session_falls_back_to_stdout_with_a_reason(home: Path, receiver: FakeReceiver) -> None:
    session = session_with(home, messagingSocketPath=str(receiver.path), peerProtocol=2)
    chosen = choose_event_stream("auto", session)
    assert chosen.mode == "stdout" and chosen.session is None
    assert chosen.fallback is not None and chosen.fallback.startswith("the session's messaging is not compatible")
    assert "peerProtocol 2" in chosen.fallback
    with pytest.raises(MessagingUnavailable, match="not compatible"):
        choose_event_stream("claude-message", session)


def test_a_missing_socket_file_is_named(home: Path, socket_dir: Path) -> None:
    session = session_with(home, messagingSocketPath=str(socket_dir / "gone.sock"))
    assert messaging_problem(session) == f"the session's messaging socket {socket_dir / 'gone.sock'} does not exist"
