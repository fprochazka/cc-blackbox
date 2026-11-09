"""Fixture builders for Claude Code config dirs and session registry records.

The record shape is copied from records written by Claude Code 2.1.319 on Linux, with every value anonymised.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from cc_blackbox.sessions.liveness import own_pid_domain

SAMPLE_RECORD: dict[str, Any] = {
    "pid": 0,
    "sessionId": "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11",
    "cwd": "/home/user/projects/example",
    "startedAt": 1794100000000,
    "procStart": "123456",
    "version": "2.1.319",
    "peerProtocol": 1,
    "peerFeatures": ["notify_idle", "reply_across_default_dirs", "artifact_yield"],
    "kind": "interactive",
    "entrypoint": "cli",
    "pidDomain": "linux:00000000000000000000000000000000:pid:[4026531836]",
    "asksForConvertedSkills": False,
    "tmux": "example:@1.%1",
    "messagingSocketPath": "/run/user/1000/cc-socks/0.sock",
    "name": "example-project-a1",
    "nameSource": "derived",
    "nameSince": 1794100000500,
    "status": "idle",
    "updatedAt": 1794100360000,
    "statusUpdatedAt": 1794100360000,
    "bridgeSessionId": "session_01ExampleBridgeId000000000",
}


def proc_start(pid: int) -> str | None:
    """Return the procStart value Claude Code would record for ``pid``, or ``None`` off Linux."""
    if not sys.platform.startswith("linux"):
        return None
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat[stat.rfind(")") + 2 :].split(" ")[19]


def live_record(pid: int, /, **overrides: Any) -> dict[str, Any]:
    """Return a sample record that passes the liveness check for the running process ``pid``."""
    record = {**SAMPLE_RECORD, "pid": pid, "messagingSocketPath": f"/run/user/1000/cc-socks/{pid}.sock"}
    record["procStart"] = proc_start(pid)
    record["pidDomain"] = own_pid_domain()
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


def write_record(config_dir: Path, pid: int, record: dict[str, Any] | str) -> Path:
    sessions = config_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{pid}.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
    return path


def dead_pid() -> int:
    """Return the pid of a process that has already exited."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def make_profile_dir(path: Path) -> Path:
    (path / "sessions").mkdir(parents=True, exist_ok=True)
    return path


def own_pid() -> int:
    return os.getpid()
