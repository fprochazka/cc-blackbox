from __future__ import annotations

from cc_blackbox import (
    ShellCommandPart,
    SlashCommandPart,
    TextPart,
    parse_user_markup,
)


def test_plain_prose_is_a_single_text_part() -> None:
    parts = parse_user_markup("just fix the bug please")
    assert parts == [TextPart(text="just fix the bug please")]


def test_empty_and_whitespace_yield_no_parts() -> None:
    assert parse_user_markup("") == []
    assert parse_user_markup("   \n\t ") == []
    assert parse_user_markup(None) == []


def test_system_reminder_block_is_dropped() -> None:
    raw = "<system-reminder>big injected context</system-reminder>real prompt"
    assert parse_user_markup(raw) == [TextPart(text="real prompt")]


def test_only_system_reminder_yields_nothing() -> None:
    assert parse_user_markup("<system-reminder>noise</system-reminder>") == []


def test_slash_command_with_args() -> None:
    raw = (
        "<command-message>research</command-message>"
        "<command-name>/example:research</command-name>"
        "<command-args>slow test suite</command-args>"
    )
    parts = parse_user_markup(raw)
    assert parts == [SlashCommandPart(name="/example:research", args="slow test suite")]


def test_slash_command_without_args() -> None:
    raw = "<command-name>/pre-plan</command-name>"
    assert parse_user_markup(raw) == [SlashCommandPart(name="/pre-plan", args=None)]


def test_bash_input_with_output() -> None:
    raw = "<bash-input>ls -la</bash-input><bash-stdout>a\nb</bash-stdout><bash-stderr></bash-stderr>"
    parts = parse_user_markup(raw)
    assert len(parts) == 1
    cmd = parts[0]
    assert isinstance(cmd, ShellCommandPart)
    assert cmd.command == "ls -la"
    assert cmd.stdout == "a\nb"
    assert cmd.stderr is None


def test_output_only_entry_has_empty_command() -> None:
    raw = "<bash-stdout>some output</bash-stdout>"
    parts = parse_user_markup(raw)
    assert parts == [ShellCommandPart(command="", stdout="some output", stderr=None)]


def test_empty_stderr_only_is_noise() -> None:
    assert parse_user_markup("<bash-stderr></bash-stderr>") == []


def test_prose_with_angle_brackets_is_preserved() -> None:
    parts = parse_user_markup("use List<String> here")
    assert parts == [TextPart(text="use List<String> here")]
