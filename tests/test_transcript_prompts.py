from __future__ import annotations

from pathlib import Path

from transcript_records import SESSION_ID, attachment, jsonl, result_record, text, user

from cc_blackbox import Prompt, SlashCommandPart, TextPart, iter_entries, prompt_from, prompts


def read(tmp_path: Path, *records: dict[str, object]) -> list[Prompt]:
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text(jsonl(records), encoding="utf-8")
    return list(prompts(iter_entries(path)))


def test_origin_decides_who_sent_a_prompt(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        user("typed by hand"),
        user('<cross-session-message from="did:ci">build failed</cross-session-message>', origin="peer", isMeta=True),
        user("<task-notification><status>completed</status></task-notification>", origin="task-notification"),
    )
    assert [(p.origin, p.queued) for p in found] == [("human", False), ("peer", False), ("task-notification", False)]


def test_tool_results_compaction_summaries_and_meta_injections_are_not_prompts(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        result_record("toolu_1", "output"),
        user("This session is being continued from a previous conversation.", isCompactSummary=True),
        user("Base directory for this skill: /opt/skills/example", origin=None, isMeta=True),
        user("   ", origin=None),
    )
    assert found == []


def test_legacy_records_without_origin_are_classified_by_their_opening_tag(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        user("a typed prompt", origin=None),
        user("<task-notification>done</task-notification>", origin=None),
        user("<command-name>/review</command-name><command-args>42</command-args>", origin=None),
    )
    assert [p.origin for p in found] == ["human", "task-notification", "human"]
    assert found[2].parts == (SlashCommandPart("/review", "42"),)


def test_queued_prompts_come_from_attachments(tmp_path: Path) -> None:
    queued = {
        "type": "queued_command",
        "prompt": "also check the docs",
        "commandMode": "prompt",
        "origin": {"kind": "human"},
        "timestamp": "2026-11-01T09:05:00.000Z",
    }
    notification = {"type": "queued_command", "prompt": "<task-notification/>", "commandMode": "task-notification"}
    other = {**queued, "commandMode": "bash"}
    found = read(tmp_path, attachment(queued, minutes=4), attachment(notification, minutes=6), attachment(other))
    prompt, note = found
    assert note.origin == "task-notification" and note.queued
    assert prompt.queued and prompt.origin == "human"
    assert prompt.parts == (TextPart("also check the docs"),)
    assert prompt.timestamp is not None and prompt.timestamp.minute == 5


def test_text_blocks_make_a_prompt_too(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(jsonl([user([text("look at"), text("this")])]), encoding="utf-8")
    (entry,) = iter_entries(path)
    prompt = prompt_from(entry)
    assert prompt is not None and prompt.text == "look at\nthis"
