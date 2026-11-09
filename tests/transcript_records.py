"""Fixture builders for Claude Code transcripts.

The record shapes are copied from transcripts written by Claude Code 2.1.x on Linux. Every id, path and text is
invented: no real transcript content belongs in these tests.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cc_blackbox.paths import project_slug

SESSION_ID = "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11"
OTHER_SESSION_ID = "0b6e2d1f-3c4a-4e5b-8f9a-1d2c3b4a5e6f"
CWD = "/home/user/projects/example"
VERSION = "2.1.319"
BASE_TIME = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

_uuids = (str(uuid.UUID(int=n)) for n in itertools.count(1))


def ts(minutes: float) -> str:
    """Return the transcript timestamp ``minutes`` after :data:`BASE_TIME`, in Claude Code's format."""
    moment = BASE_TIME + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def envelope(
    record_type: str,
    *,
    minutes: float = 0,
    session_id: str = SESSION_ID,
    cwd: str = CWD,
    parent: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Return the keys Claude Code puts on every conversation record."""
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": record_type,
        "uuid": next(_uuids),
        "timestamp": ts(minutes),
        "userType": "external",
        "entrypoint": "cli",
        "cwd": cwd,
        "sessionId": session_id,
        "version": VERSION,
        "gitBranch": "main",
        **extra,
    }


def user(
    content: str | list[dict[str, Any]], *, minutes: float = 0, origin: str | None = "human", **extra: Any
) -> dict[str, Any]:
    record = envelope("user", minutes=minutes, message={"role": "user", "content": content}, **extra)
    if origin is not None:
        record["origin"] = {"kind": origin}
        record.setdefault("promptSource", "typed" if origin == "human" else "system")
    return record


def assistant(
    blocks: Sequence[dict[str, Any]],
    *,
    minutes: float = 0,
    message_id: str = "msg_01Example",
    model: str = "claude-opus-5",
    usage: dict[str, Any] | None = None,
    stop_reason: str = "end_turn",
    **extra: Any,
) -> dict[str, Any]:
    message = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": list(blocks),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage if usage is not None else {"input_tokens": 3, "output_tokens": 10},
    }
    return envelope("assistant", minutes=minutes, message=message, requestId="req_01Example", **extra)


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def thinking(value: str = "") -> dict[str, Any]:
    return {"type": "thinking", "thinking": value, "signature": "c2lnbmF0dXJl"}


def tool_use(tool_id: str, name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def tool_result(tool_id: str, content: str | list[dict[str, Any]], *, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def result_record(
    tool_id: str,
    content: str | list[dict[str, Any]],
    *,
    minutes: float = 0,
    is_error: bool = False,
    tool_use_result: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    record = user([tool_result(tool_id, content, is_error=is_error)], minutes=minutes, origin=None, **extra)
    record["toolUseResult"] = tool_use_result if tool_use_result is not None else {"stdout": "", "stderr": ""}
    record["sourceToolAssistantUUID"] = next(_uuids)
    return record


def system(subtype: str, *, minutes: float = 0, **extra: Any) -> dict[str, Any]:
    return envelope("system", minutes=minutes, subtype=subtype, level="info", **extra)


def attachment(payload: dict[str, Any], *, minutes: float = 0) -> dict[str, Any]:
    return envelope("attachment", minutes=minutes, attachment=payload)


def bookkeeping(record_type: str, session_id: str = SESSION_ID, **fields: Any) -> dict[str, Any]:
    """Return a record without the conversation envelope, like ``ai-title`` or ``last-prompt``."""
    return {"type": record_type, **fields, "sessionId": session_id}


def jsonl(records: Iterable[dict[str, Any] | str]) -> str:
    # Claude Code writes compact JSON with raw UTF-8, one record per line.
    return "".join(
        (r if isinstance(r, str) else json.dumps(r, separators=(",", ":"), ensure_ascii=False)) + "\n" for r in records
    )


def write_transcript(
    projects_dir: Path,
    records: Iterable[dict[str, Any] | str],
    *,
    session_id: str = SESSION_ID,
    cwd: str = CWD,
    mtime: datetime | None = None,
) -> Path:
    """Write a main transcript under the slug of ``cwd`` and return its path."""
    path = projects_dir / project_slug(cwd) / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(jsonl(records), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def write_subagent(
    main: Path,
    agent_id: str,
    records: Iterable[dict[str, Any] | str],
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write a subagent transcript, and its ``.meta.json`` when ``meta`` is given, next to the main transcript."""
    folder = main.with_suffix("") / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"agent-{agent_id}.jsonl"
    path.write_text(jsonl(records), encoding="utf-8")
    if meta is not None:
        (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def write_tool_result_file(main: Path, name: str, content: str) -> Path:
    folder = main.with_suffix("") / "tool-results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(content, encoding="utf-8")
    return path


def conversation(
    *, title: str | None = "Example title", prompt: str = "Explain the example module."
) -> list[dict[str, Any]]:
    """Return a short, complete session: one typed prompt, one reply, and the bookkeeping Claude Code appends."""
    records: list[dict[str, Any]] = [
        bookkeeping("permission-mode", permissionMode="default"),
        user(prompt, minutes=0),
        assistant([text("The example module reads a file.")], minutes=1),
        bookkeeping("last-prompt", lastPrompt=prompt, leafUuid=str(uuid.UUID(int=999))),
    ]
    if title is not None:
        records.append(bookkeeping("ai-title", aiTitle=title))
    return records
