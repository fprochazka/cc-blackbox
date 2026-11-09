from __future__ import annotations

from pathlib import Path
from typing import Any

from transcript_records import (
    assistant,
    attachment,
    jsonl,
    result_record,
    system,
    text,
    thinking,
    tool_use,
    user,
    write_subagent,
    write_transcript,
)

from cc_blackbox import (
    AssistantText,
    Entry,
    Prompt,
    SubagentRun,
    Thinking,
    UsageTotals,
    assistant_texts,
    compactions,
    conversation,
    failures,
    files_touched,
    iter_entries,
    list_session_transcripts,
    shell_commands,
    subagent_runs,
    token_usage,
    tool_calls,
)

AGENT_ID = "a1b2c3d4e5f60718"


def entries(tmp_path: Path, *records: dict[str, Any]) -> list[Entry]:
    path = tmp_path / "t.jsonl"
    path.write_text(jsonl(records), encoding="utf-8")
    return list(iter_entries(path))


def agent_session() -> list[dict[str, Any]]:
    return [
        user("Find where the config is loaded.", minutes=0),
        assistant(
            [
                thinking("I should delegate this."),
                text("Delegating the search."),
                tool_use("toolu_1", "Agent", description="Find loader", subagent_type="Explore", prompt="Look for it."),
            ],
            minutes=1,
        ),
        result_record(
            "toolu_1",
            [text("It is in config.py.")],
            minutes=2,
            tool_use_result={"status": "completed", "agentId": AGENT_ID},
        ),
        assistant([text("The config is loaded in config.py.")], minutes=3, message_id="msg_02"),
    ]


def test_tool_calls_are_joined_with_their_results(tmp_path: Path) -> None:
    found = list(
        tool_calls(
            entries(
                tmp_path,
                assistant([tool_use("toolu_1", "Bash", command="ls"), tool_use("toolu_2", "Read", file_path="/a")]),
                result_record("toolu_1", "a\nb"),
                result_record("toolu_2", "No such file", is_error=True),
                assistant([tool_use("toolu_3", "Bash", command="sleep 60")], message_id="msg_02"),
            )
        )
    )
    assert [(c.name, c.result.text if c.result else None, c.is_error) for c in found] == [
        ("Bash", "a\nb", False),
        ("Read", "No such file", True),
        ("Bash", None, False),
    ]
    assert found[0].input == {"command": "ls"}


def test_subagent_runs_link_to_their_transcripts(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    main = write_transcript(projects, agent_session())
    child = write_subagent(main, AGENT_ID, [user("Look for it.", isSidechain=True)])
    (session,) = list_session_transcripts(projects_dirs=[projects])

    (run,) = subagent_runs(session.entries(), session)

    assert (run.description, run.subagent_type, run.prompt, run.result) == (
        "Find loader",
        "Explore",
        "Look for it.",
        "It is in config.py.",
    )
    assert run.agent_id == AGENT_ID
    assert run.transcript is not None and run.transcript.path == child


def test_a_subagent_without_a_final_response(tmp_path: Path) -> None:
    (run,) = subagent_runs(
        entries(
            tmp_path,
            assistant([tool_use("toolu_1", "Agent", prompt="Go.", run_in_background=True)]),
            result_record("toolu_1", "Async agent launched.", tool_use_result={"status": "async_launched"}),
        )
    )
    assert run.result is None and run.transcript is None


def test_conversation_keeps_prompts_text_and_subagents_in_order(tmp_path: Path) -> None:
    items = list(conversation(entries(tmp_path, *agent_session())))
    assert [type(i) for i in items] == [Prompt, AssistantText, SubagentRun, AssistantText]
    with_thinking = list(conversation(entries(tmp_path, *agent_session()), include_thinking=True))
    assert isinstance(with_thinking[1], Thinking) and with_thinking[1].text == "I should delegate this."


def test_conversation_skips_empty_thinking(tmp_path: Path) -> None:
    items = list(conversation(entries(tmp_path, assistant([thinking(""), text("ok")])), include_thinking=True))
    assert [type(i) for i in items] == [AssistantText]


def test_files_touched(tmp_path: Path) -> None:
    found = list(
        files_touched(
            entries(
                tmp_path,
                assistant(
                    [
                        tool_use("t1", "Read", file_path="/repo/a.py"),
                        tool_use("t2", "Edit", file_path="/repo/a.py", old_string="x", new_string="y"),
                        tool_use("t3", "Write", file_path="/repo/b.py", content=""),
                        tool_use("t4", "NotebookEdit", notebook_path="/repo/c.ipynb"),
                        tool_use("t5", "Bash", command="cat /repo/d.py"),
                    ]
                ),
            )
        )
    )
    assert [(f.path, f.operation) for f in found] == [
        ("/repo/a.py", "read"),
        ("/repo/a.py", "edit"),
        ("/repo/b.py", "write"),
        ("/repo/c.ipynb", "edit"),
    ]


def test_shell_commands_from_tools_and_from_the_user(tmp_path: Path) -> None:
    found = list(
        shell_commands(
            entries(
                tmp_path,
                user("<bash-input>git status</bash-input><bash-stdout>clean</bash-stdout><bash-stderr></bash-stderr>"),
                assistant([tool_use("t1", "Bash", command="make test")]),
                result_record("t1", "1 failed", is_error=True),
            )
        )
    )
    assert [(c.command, c.output, c.is_error, c.source) for c in found] == [
        ("git status", "clean", False, "user"),
        ("make test", "1 failed", True, "tool"),
    ]


def test_failures(tmp_path: Path) -> None:
    found = list(
        failures(
            entries(
                tmp_path,
                assistant([tool_use("t1", "Bash", command="rm -rf build")]),
                result_record("t1", "User rejected tool use", toolDenialKind="user-rejected"),
                assistant([tool_use("t2", "Read", file_path="/missing")], message_id="msg_02"),
                result_record("t2", "File does not exist.", is_error=True),
                user([text("[Request interrupted by user for tool use]")], origin=None),
            )
        )
    )
    assert [(f.kind, f.tool_name) for f in found] == [("denied", "Bash"), ("tool-error", "Read"), ("interrupted", None)]


def test_token_usage_counts_each_message_once(tmp_path: Path) -> None:
    usage = {"input_tokens": 2, "output_tokens": 100, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 50}
    totals = token_usage(
        entries(
            tmp_path,
            assistant([thinking()], message_id="msg_01", usage=usage),
            assistant([text("a")], message_id="msg_01", usage=usage),
            assistant([tool_use("t1", "Bash", command="ls")], message_id="msg_01", usage=usage),
            assistant([text("b")], message_id="msg_02", usage=usage, model="claude-haiku-4-5"),
            assistant([text("No response requested.")], message_id="msg_03", usage=usage, model="<synthetic>"),
        )
    )
    assert totals == {
        "claude-opus-5": UsageTotals(1, 2, 100, 10, 50),
        "claude-haiku-4-5": UsageTotals(1, 2, 100, 10, 50),
    }


def test_compactions_carry_their_summary(tmp_path: Path) -> None:
    found = list(
        compactions(
            entries(
                tmp_path,
                user("start"),
                system(
                    "compact_boundary",
                    content="Conversation compacted",
                    compactMetadata={"trigger": "manual", "preTokens": 120000},
                ),
                user(
                    "This session is being continued from a previous conversation. Summary: ...",
                    origin=None,
                    isCompactSummary=True,
                ),
                system("compact_boundary", compactMetadata={"trigger": "auto"}),
            )
        )
    )
    assert [(c.trigger, c.pre_tokens, c.summary is not None) for c in found] == [
        ("manual", 120000, True),
        ("auto", None, False),
    ]


def test_assistant_texts_and_queued_prompts(tmp_path: Path) -> None:
    items = entries(
        tmp_path,
        assistant([text("first"), text("  ")]),
        attachment({"type": "queued_command", "prompt": "and the docs", "commandMode": "prompt"}),
    )
    assert [t.text for t in assistant_texts(items)] == ["first"]
    prompts_only = [i for i in conversation(items) if isinstance(i, Prompt)]
    assert [(p.text, p.queued) for p in prompts_only] == [("and the docs", True)]
