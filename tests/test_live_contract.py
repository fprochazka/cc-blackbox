"""The live contract tests: start real headless Claude Code sessions, message one through cc-blackbox, and deny a tool
call in another with a hook built on cc_blackbox.hooks.

They are excluded from the default run and from CI. Run them with ``uv run pytest -m live -s`` after a Claude Code
update to confirm that the registry record, the messaging protocol, the transcript records and the hook contract are
still aligned. They need the ``claude`` CLI on PATH, logged in, and they spend a little model usage: three short turns
on the cheapest model.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest

from cc_blackbox import (
    ActiveSession,
    ReadDiagnostics,
    assistant_texts,
    check_compatibility,
    current_profile,
    list_active_sessions,
    prompts,
    send_message,
    transcript_for,
)
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
        expected = session.profile.projects_dir / project_slug(session.cwd) / f"{session.session_id}.jsonl"
        transcript = transcript_for(session)
        assert transcript is not None, f"no transcript found for session {session.session_id}, expected {expected}"
        # Profiles often share one projects dir through a symlink; a listing reports the resolved path.
        assert transcript.path == expected.resolve()
        assert marker in json.dumps(results[1]), f"the session did not repeat the marker: {results[1]}"

        # The transcript side of the contract: the records still have the shapes the reader models, the peer message
        # is a prompt that says who sent it, and the reply is assistant text.
        diagnostics = ReadDiagnostics()
        entries = list(transcript.entries(diagnostics=diagnostics))
        assert diagnostics.aligned, f"unexpected record shapes: {dict(diagnostics.mistyped_fields)}"
        delivered = [p for p in prompts(entries) if marker in p.text]
        assert [p.origin for p in delivered] == ["peer"], f"the marker prompt is {delivered}"
        assert any(marker in t.text for t in assistant_texts(entries)), "no assistant text repeats the marker"
        assert transcript.cwd == session.cwd and transcript.last_activity_at is not None

        def total(key: str) -> int:
            return sum(int((r.get("usage") or {}).get(key) or 0) for r in results)

        costs = [float(r.get("total_cost_usd") or 0) for r in results]
        print(
            f"\nlive contract: Claude Code {session.version}, model {MODEL}, {time.monotonic() - started:.1f} s, "
            f"total_cost_usd per result {costs}, tokens: {total('input_tokens')} input, "
            f"{total('cache_creation_input_tokens')} cache write, {total('cache_read_input_tokens')} cache read, "
            f"{total('output_tokens')} output. Transcript: {diagnostics.lines} records, "
            f"unknown types {dict(diagnostics.unknown_types) or 'none'}, "
            f"unknown blocks {dict(diagnostics.unknown_block_types) or 'none'}"
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


DENY_HOOK = """
import json, sys
from pathlib import Path

from cc_blackbox.hooks import PreToolUseInput, PreToolUseReply, run_hook

dump, marker = Path(sys.argv[1]), sys.argv[2]


def deny(event: PreToolUseInput) -> PreToolUseReply:
    view = event.typed_tool_input()
    dump.write_text(json.dumps({
        "class": type(event).__name__,
        "session_id": event.session_id,
        "type_mismatches": list(event.type_mismatches),
        "command": getattr(view, "command", None),
        "raw_keys": sorted(event.raw),
    }))
    return PreToolUseReply.deny(f"{marker}: this hook denies every Bash call.")


run_hook(deny, expect=PreToolUseInput, name="cc-blackbox-live")
"""


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not on PATH")
def test_pre_tool_use_hook_denies_a_tool_call(workdir: Path) -> None:
    """A PreToolUse hook built on cc_blackbox.hooks denies a Bash call that an allow rule would let through."""
    assert CLAUDE is not None
    marker = f"cc-blackbox-hook-{uuid.uuid4().hex[:12]}"
    script, dump = workdir / "deny_hook.py", workdir / "hook-input.json"
    script.write_text(DENY_HOOK, encoding="utf-8")
    hook = {"type": "command", "command": sys.executable, "args": [str(script), str(dump), marker], "timeout": 30}
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}}
    env = {k: v for k, v in os.environ.items() if k not in _SESSION_ENV}
    command = [
        CLAUDE,
        "-p",
        "Run exactly this Bash command and nothing else: touch ran. If the call is denied, do not retry; reply with "
        "the denial reason verbatim.",
        "--output-format=stream-json",
        "--verbose",
        f"--model={MODEL}",
        "--tools=Bash",
        "--allowedTools=Bash(touch *)",
        # Keep the user's own hooks and plugins out: only this run's --settings apply.
        "--setting-sources=local",
        "--strict-mcp-config",
        "--max-budget-usd=0.5",
        f"--settings={json.dumps(settings)}",
    ]
    started = time.monotonic()
    result = subprocess.run(command, cwd=workdir, env=env, capture_output=True, text=True, timeout=REPLY_TIMEOUT)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    final = [e for e in events if e.get("type") == "result"]
    session_id = final[-1].get("session_id") if final else None
    try:
        assert final, f"no result event. Exit {result.returncode}. Stderr: {result.stderr[-2000:]}"
        assert not (workdir / "ran").exists(), "the denied command ran"
        assert dump.exists(), f"the hook never ran. Events: {events[-3:]}"
        seen = json.loads(dump.read_text(encoding="utf-8"))
        assert seen["class"] == "PreToolUseInput"
        assert seen["type_mismatches"] == [], seen
        assert "touch" in (seen["command"] or "")
        assert seen["session_id"] == session_id
        tool_results = [
            block
            for e in events
            if e.get("type") == "user"
            for block in (e.get("message") or {}).get("content") or []
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert any(marker in json.dumps(block) for block in tool_results), (
            f"no tool result carries the deny reason: {tool_results}"
        )
        cost, turns = final[-1].get("total_cost_usd"), final[-1].get("num_turns")
        print(
            f"\nlive hook contract: {time.monotonic() - started:.1f} s, total_cost_usd {cost}, num_turns {turns}, "
            f"payload keys {seen['raw_keys']}"
        )
    finally:
        profile = current_profile()
        if profile is not None:
            shutil.rmtree(profile.projects_dir / project_slug(workdir.resolve()), ignore_errors=True)
