from __future__ import annotations

from pathlib import Path

from transcript_records import assistant, tool_use, user, write_subagent, write_tool_result_file, write_transcript

from cc_blackbox import SessionTranscript, list_session_transcripts, sessions_containing


def session_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def make(tmp_path: Path) -> list[SessionTranscript]:
    projects = tmp_path / "projects"
    one = write_transcript(projects, [user("Fix the Parser crash.", minutes=2)], session_id=session_id(1))
    write_subagent(one, "a1", [user("Look at tokenizer.py", isSidechain=True)])
    two = write_transcript(projects, [user("Update the README.", minutes=1)], session_id=session_id(2))
    write_tool_result_file(two, "toolu_1.txt", "huge output mentioning Tokenizer")
    write_transcript(
        projects,
        [user('Quote "exactly" this', minutes=0), assistant([tool_use("t1", "Bash", command="printf 'a\\nb'")])],
        session_id=session_id(3),
    )
    write_transcript(projects, [user("Přidej háček do Šablony", minutes=3)], session_id=session_id(4))
    return list_session_transcripts(projects_dirs=[projects])


def ids(found: list[SessionTranscript]) -> list[str]:
    return sorted(s.session_id[-1] for s in found)


def test_terms_match_case_insensitively_across_a_sessions_files(tmp_path: Path) -> None:
    sessions = make(tmp_path)
    assert ids(sessions_containing(sessions, ["parser", "TOKENIZER"])) == ["1"]
    assert ids(sessions_containing(sessions, ["tokenizer"])) == ["1", "2"]
    assert ids(sessions_containing(sessions, ["parser", "readme"], match_any=True)) == ["1", "2"]
    assert sessions_containing(sessions, ["parser", "readme"]) == []


def test_terms_with_quotes_and_escapes_match_the_json_form(tmp_path: Path) -> None:
    sessions = make(tmp_path)
    assert ids(sessions_containing(sessions, ['"exactly"'])) == ["3"]
    assert ids(sessions_containing(sessions, ["a\\nb"])) == ["3"]


def test_non_ascii_terms_fold_unicode_case(tmp_path: Path) -> None:
    sessions = make(tmp_path)
    assert ids(sessions_containing(sessions, ["šablony"])) == ["4"]
    assert ids(sessions_containing(sessions, ["HÁČEK"])) == ["4"]


def test_blank_terms_find_nothing(tmp_path: Path) -> None:
    assert sessions_containing(make(tmp_path), ["  ", ""]) == []


def test_workers_give_the_same_result_in_the_same_order(tmp_path: Path) -> None:
    sessions = make(tmp_path)
    assert sessions_containing(sessions, ["e"], workers=2) == sessions_containing(sessions, ["e"])


def test_results_keep_the_given_order(tmp_path: Path) -> None:
    sessions = list(reversed(make(tmp_path)))
    assert sessions_containing(sessions, ["parser", "readme"], match_any=True) == [sessions[1], sessions[2]]
