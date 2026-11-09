"""The live contract test: start a real headless Claude Code session and message it through cc-blackbox.

It is excluded from the default run and from CI. Run it with ``uv run pytest -m live -s`` after a Claude Code update
to confirm that the registry record and the messaging protocol are still aligned. It needs the ``claude`` CLI on PATH,
logged in, and it spends a little model usage: two short turns on the cheapest model.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest

from cc_blackbox import ActiveSession, check_compatibility, current_profile, list_active_sessions, send_message
from cc_blackbox.paths import env_config_dir, project_slug

pytestmark = pytest.mark.live

CLAUDE = shutil.which("claude")
MODEL = os.environ.get("CC_BLACKBOX_LIVE_MODEL", "haiku")
STARTUP_TIMEOUT = 60.0
REPLY_TIMEOUT = 120.0

# Variables that tie a process to the Claude Code session it runs in. A child that inherits them counts as part of
# that session: it skips writing its own registry record and points at the parent's socket.
_SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_NAME",
)


class StreamReader:
    """Collects the stream-json events a headless session prints, one JSON object per line."""

    def __init__(self, stream: IO[str]) -> None:
        self.events: list[dict[str, Any]] = []
        self.other_lines: list[str] = []
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: IO[str]) -> None:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            with self._condition:
                if isinstance(event, dict):
                    self.events.append(event)
                else:
                    self.other_lines.append(line.rstrip())
                self._condition.notify_all()

    def wait_for_results(self, count: int, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                results = [e for e in self.events if e.get("type") == "result"]
                if len(results) >= count:
                    return results
                remaining = deadline - time.monotonic()
                assert remaining > 0, (
                    f"expected {count} result events, got {len(results)}. "
                    f"Last events: {self.events[-3:]}. Other output: {self.other_lines[-20:]}"
                )
                self._condition.wait(remaining)


@pytest.fixture
def workdir() -> Iterator[Path]:
    # The socket path must fit in about 104 bytes and its dir must be 0700, which mkdtemp gives.
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    path = Path(tempfile.mkdtemp(prefix="cc-blackbox-live-", dir=base))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def wait_for_session(socket_path: Path, timeout: float, reader: StreamReader) -> ActiveSession:
    profile = current_profile()
    assert profile is not None, f"the config dir {env_config_dir()} is not a known profile"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for session in list_active_sessions([profile]):
            if session.messaging_socket == socket_path:
                return session
        time.sleep(0.2)
    raise AssertionError(
        f"no registry record named the socket {socket_path} within {timeout} s. "
        f"Socket exists: {socket_path.exists()}. Events: {reader.events[-3:]}. Other output: {reader.other_lines[-20:]}"
    )


def user_turn(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not on PATH")
def test_headless_session_receives_a_message(workdir: Path) -> None:
    assert CLAUDE is not None
    socket_path = workdir / "t.sock"
    marker = f"cc-blackbox-live-{uuid.uuid4().hex[:12]}"
    env = {k: v for k, v in os.environ.items() if k not in _SESSION_ENV}
    env["CLAUDE_CODE_HARBOR_KITE"] = "1"
    command = [
        CLAUDE,
        "-p",
        "--input-format=stream-json",
        "--output-format=stream-json",
        "--verbose",
        f"--model={MODEL}",
        "--tools=",
        "--strict-mcp-config",
        "--max-budget-usd=0.5",
        f"--messaging-socket-path={socket_path}",
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=workdir,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    reader = StreamReader(process.stdout)
    session: ActiveSession | None = None
    try:
        # Stream-json input keeps the session alive between turns, so it can take the peer message as a new turn.
        process.stdin.write(user_turn("Reply with the single word READY."))
        process.stdin.flush()

        session = wait_for_session(socket_path, STARTUP_TIMEOUT, reader)
        compatibility = check_compatibility(session)
        assert compatibility.compatible, compatibility.reasons
        reader.wait_for_results(1, REPLY_TIMEOUT)

        send_message(session, f"Reply with exactly this text and nothing else: {marker}", sender="cc-blackbox-live")
        results = reader.wait_for_results(2, REPLY_TIMEOUT)

        assert session.session_id is not None and session.cwd is not None
        transcript = session.profile.projects_dir / project_slug(session.cwd) / f"{session.session_id}.jsonl"
        assert marker in transcript.read_text(encoding="utf-8"), f"the transcript {transcript} lacks the marker"
        assert marker in json.dumps(results[1]), f"the session did not repeat the marker: {results[1]}"

        def total(key: str) -> int:
            return sum(int((r.get("usage") or {}).get(key) or 0) for r in results)

        costs = [float(r.get("total_cost_usd") or 0) for r in results]
        print(
            f"\nlive contract: Claude Code {session.version}, model {MODEL}, {time.monotonic() - started:.1f} s, "
            f"total_cost_usd per result {costs}, tokens: {total('input_tokens')} input, "
            f"{total('cache_creation_input_tokens')} cache write, {total('cache_read_input_tokens')} cache read, "
            f"{total('output_tokens')} output"
        )
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if session is not None and session.session_id is not None and session.cwd is not None:
            # The headless run wrote a transcript for the temporary cwd; nothing else lives in that project dir.
            shutil.rmtree(session.profile.projects_dir / project_slug(session.cwd), ignore_errors=True)
