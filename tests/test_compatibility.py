from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from records import SAMPLE_RECORD, write_record

from cc_blackbox import (
    TESTED_CLAUDE_CODE_VERSIONS,
    ActiveSession,
    SessionGone,
    UnsupportedProtocol,
    check_compatibility,
    list_active_sessions,
    list_profiles,
    send_message,
)

on_linux = sys.platform.startswith("linux")


def session_from(home: Path, record: dict[str, Any]) -> ActiveSession:
    write_record(home / ".claude", 4242, record)
    [session] = list_active_sessions(list_profiles([home / ".claude"]), check_liveness=False)
    return session


def without(*keys: str, **changes: Any) -> dict[str, Any]:
    record = {k: v for k, v in SAMPLE_RECORD.items() if k not in keys}
    record.update(changes)
    return record


def test_sample_record_is_compatible(home: Path) -> None:
    session = session_from(home, dict(SAMPLE_RECORD))
    result = session.compatibility
    assert result.compatible
    assert dict(result.features) == {"messaging": True, "scratchpad": True, "liveness": True}
    assert result.reasons == ()
    assert result == check_compatibility(session)


def test_newer_claude_code_with_additions_is_compatible(home: Path) -> None:
    record = without(
        version="9.0.0",
        peerFeatures=["notify_idle", "some_future_feature"],
        futureField={"anything": [1, 2]},
        status="a-new-status",
    )
    assert session_from(home, record).compatibility.compatible
    assert TESTED_CLAUDE_CODE_VERSIONS == ("2.1.319",)


def test_record_without_version_is_compatible(home: Path) -> None:
    assert session_from(home, without("version")).compatibility.compatible


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (without(peerProtocol=2), "peerProtocol 2"),
        (without(peerProtocol="1"), "peerProtocol '1'"),
        (without(peerProtocol=True), "peerProtocol True"),
        (without("peerProtocol"), "no peerProtocol"),
        (without("messagingSocketPath"), "messaging turned off"),
        (without(messagingSocketPath=""), "messaging turned off"),
        (without(messagingSocketPath=7), "messaging turned off"),
    ],
)
def test_messaging_misalignment(home: Path, record: dict[str, Any], reason: str) -> None:
    result = session_from(home, record).compatibility
    assert not result.compatible
    assert dict(result.features) == {"messaging": False, "scratchpad": True, "liveness": True}
    [message] = result.reasons
    assert message.startswith("messaging: ")
    assert reason in message


@pytest.mark.parametrize("record", [without("sessionId"), without("cwd"), without(cwd="?"), without(sessionId=5)])
def test_scratchpad_misalignment(home: Path, record: dict[str, Any]) -> None:
    result = session_from(home, record).compatibility
    assert dict(result.features) == {"messaging": True, "scratchpad": False, "liveness": True}
    assert all(r.startswith("scratchpad: ") for r in result.reasons)


def test_liveness_needs_pid(home: Path) -> None:
    result = session_from(home, without("pid")).compatibility
    assert result.features["liveness"] is False
    assert result.reasons == ("liveness: the record has no integer pid.",)


def test_liveness_needs_proc_start_on_linux(home: Path) -> None:
    result = session_from(home, without("procStart")).compatibility
    assert result.features["liveness"] is not on_linux
    assert result.compatible is not on_linux


def test_reasons_cover_every_problem(home: Path) -> None:
    result = session_from(home, {}).compatibility
    assert not any(result.features.values())
    assert [r.split(":")[0] for r in result.reasons] == ["messaging", "messaging", "scratchpad", "scratchpad"] + [
        "liveness"
    ] * (2 if on_linux else 1)


def test_send_message_reports_the_reasons(home: Path, socket_dir: Path) -> None:
    session = session_from(home, without(peerProtocol=3, messagingSocketPath=str(socket_dir / "x.sock")))
    with pytest.raises(UnsupportedProtocol, match=r"peerProtocol 3, and this cc-blackbox version speaks 1"):
        send_message(session, "hi", sender="w")


def test_send_message_ignores_other_features(home: Path, socket_dir: Path) -> None:
    # Only messaging matters for sending: a record without cwd still gets the message.
    session = session_from(home, without("cwd", messagingSocketPath=str(socket_dir / "missing.sock")))
    assert not session.compatibility.compatible
    with pytest.raises(SessionGone):
        send_message(session, "hi", sender="w")
