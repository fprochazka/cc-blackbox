from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from transcript_records import (
    SESSION_ID,
    assistant,
    attachment,
    bookkeeping,
    jsonl,
    result_record,
    system,
    text,
    thinking,
    tool_result,
    tool_use,
    user,
)

from cc_blackbox import (
    AssistantEntry,
    AttachmentEntry,
    Entry,
    ImageBlock,
    ReadDiagnostics,
    SystemEntry,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UnknownBlock,
    UserEntry,
    iter_entries,
)


def write(tmp_path: Path, *records: dict[str, object] | str) -> Path:
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(jsonl(records), encoding="utf-8")
    return path


def test_records_become_typed_entries(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        user("Explain the module.", minutes=0),
        assistant(
            [thinking(), text("It reads a file."), tool_use("toolu_1", "Read", file_path="/tmp/x.py")], minutes=1
        ),
        result_record("toolu_1", [text("print('x')")], minutes=2),
        system("turn_duration", minutes=3, durationMs=1200),
        attachment({"type": "date", "date": "2026-11-01"}, minutes=3),
        bookkeeping("ai-title", aiTitle="Module walkthrough"),
    )

    entries = list(iter_entries(path))

    prompt, reply, result, turn, note, title = entries
    assert isinstance(prompt, UserEntry)
    assert prompt.text == "Explain the module."
    assert prompt.origin == "human"
    assert prompt.timestamp == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert prompt.session_id == SESSION_ID
    assert prompt.line_number == 1
    assert isinstance(reply, AssistantEntry)
    assert reply.model == "claude-opus-5"
    assert reply.message_id == "msg_01Example"
    assert reply.usage is not None and reply.usage.output_tokens == 10
    assert reply.content == (
        ThinkingBlock("", True),
        TextBlock("It reads a file."),
        ToolUseBlock("toolu_1", "Read", {"file_path": "/tmp/x.py"}),
    )
    assert isinstance(result, UserEntry)
    assert result.origin is None
    assert result.content == (ToolResultBlock("toolu_1", "print('x')", False, (TextBlock("print('x')"),)),)
    assert isinstance(turn, SystemEntry) and turn.subtype == "turn_duration"
    assert isinstance(note, AttachmentEntry) and note.attachment_type == "date"
    assert type(title) is Entry
    assert title.raw["aiTitle"] == "Module walkthrough"


def test_offsets_let_a_reader_continue_where_it_stopped(tmp_path: Path) -> None:
    path = write(tmp_path, user("one"), user("two"))
    first = next(iter_entries(path))
    rest = list(iter_entries(path, start_offset=first.end_offset))
    assert [e.raw["message"]["content"] for e in rest] == ["two"]
    assert rest[0].offset == first.end_offset


def test_malformed_lines_are_counted_and_skipped(tmp_path: Path) -> None:
    path = write(tmp_path, "not json", "[1, 2]", json.dumps({"no": "type"}), user("kept"))
    diagnostics = ReadDiagnostics()
    entries = list(iter_entries(path, diagnostics=diagnostics))
    assert len(entries) == 1
    assert diagnostics.lines == 4
    assert diagnostics.malformed_lines == 3
    assert diagnostics.aligned


def test_a_half_written_last_line_is_left_for_the_next_read(tmp_path: Path) -> None:
    path = write(tmp_path, user("done"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"user","message":{"role":"us')
    diagnostics = ReadDiagnostics()
    assert len(list(iter_entries(path, diagnostics=diagnostics))) == 1
    assert diagnostics.malformed_lines == 0


def test_unknown_types_and_blocks_are_kept_and_do_not_break_alignment(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        bookkeeping("brand-new-record", value=1),
        assistant([{"type": "hologram", "data": "x"}, text("ok")]),
    )
    diagnostics = ReadDiagnostics()
    first, second = iter_entries(path, diagnostics=diagnostics)
    assert first.type == "brand-new-record"
    assert isinstance(second, AssistantEntry)
    assert second.content[0] == UnknownBlock("hologram", {"type": "hologram", "data": "x"})
    assert diagnostics.unknown_types == {"brand-new-record": 1}
    assert diagnostics.unknown_block_types == {"hologram": 1}
    assert diagnostics.aligned


def test_a_mistyped_modelled_field_breaks_alignment(tmp_path: Path) -> None:
    record = assistant([text("x")])
    record["message"]["content"] = 42
    path = write(tmp_path, record)
    diagnostics = ReadDiagnostics()
    (entry,) = iter_entries(path, diagnostics=diagnostics)
    assert isinstance(entry, AssistantEntry) and entry.content == ()
    assert diagnostics.mistyped_fields == {"assistant.message.content": 1}
    assert not diagnostics.aligned


def test_types_filter_skips_other_records(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        user("prompt"),
        attachment({"type": "date", "date": "2026-11-01"}),
        assistant([text('a reply that mentions "type":"user" in passing')]),
    )
    entries = list(iter_entries(path, types={"user"}))
    assert [e.type for e in entries] == ["user"]


def test_tool_results_with_images_and_errors(tmp_path: Path) -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    record = user([tool_result("toolu_9", [text("see image"), image], is_error=True)], origin=None)
    path = write(tmp_path, record)
    (entry,) = iter_entries(path)
    assert isinstance(entry, UserEntry)
    (block,) = entry.content
    assert isinstance(block, ToolResultBlock)
    assert block.is_error
    assert block.text == "see image"
    assert block.content[1] == ImageBlock("image/png")


def test_plain_text_joins_text_blocks(tmp_path: Path) -> None:
    path = write(tmp_path, user([text("first"), text("second")]))
    (entry,) = iter_entries(path)
    assert isinstance(entry, UserEntry)
    assert entry.text is None
    assert entry.plain_text == "first\nsecond"


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        list(iter_entries(tmp_path / "missing.jsonl"))
