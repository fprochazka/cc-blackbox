"""A PreToolUse hook runs before every tool call it matches, so what it imports is a cost on every call.

The module-set test runs by default: it fails when a change makes the hook path import something heavy. The timing test
is opt-in (``pytest -m perf -s``) because wall-clock time on a shared CI runner is noisy.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = """
import atexit, json, sys
from cc_blackbox.hooks import PreToolUseInput, PreToolUseReply, ShellInput, run_hook

if "--modules" in sys.argv:
    atexit.register(lambda: sys.stderr.write(json.dumps(sorted(sys.modules))))

def guard(event: PreToolUseInput) -> PreToolUseReply | None:
    view = event.typed_tool_input()
    if isinstance(view, ShellInput) and view.command.startswith("glab ci view"):
        return PreToolUseReply.deny("Use glab-pipeline inspect instead.")
    return None

run_hook(guard, expect=PreToolUseInput)
"""

PAYLOAD = json.dumps(
    {
        "session_id": "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11",
        "transcript_path": "/home/user/.claude/projects/x/5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11.jsonl",
        "cwd": "/home/user/projects/example",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "glab ci view 1000", "description": "Show the pipeline"},
        "tool_use_id": "toolu_01EXAMPLE",
    }
)

# Modules a PreToolUse hook must not load. The first group is the rest of the library and its dependencies; the second
# is the event families a PreToolUse payload does not need.
FORBIDDEN = (
    "yaml",
    "pathlib",
    "concurrent",
    "multiprocessing",
    "traceback",
    "cc_blackbox.sessions",
    "cc_blackbox.transcripts",
    "cc_blackbox.messaging",
    "cc_blackbox.profiles",
    "cc_blackbox.compatibility",
    "cc_blackbox.hooks._rare_tool_events",
    "cc_blackbox.hooks._turn_events",
    "cc_blackbox.hooks._session_events",
    "cc_blackbox.hooks._links",
)


@pytest.fixture
def hook(tmp_path: Path) -> Path:
    path = tmp_path / "guard.py"
    path.write_text(HOOK, encoding="utf-8")
    return path


def test_a_pre_tool_use_hook_imports_only_what_it_needs(hook: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(hook), "--modules"], input=PAYLOAD, capture_output=True, text=True, check=True
    )
    assert '"permissionDecision":"deny"' in result.stdout
    loaded = set(json.loads(result.stderr))
    assert [m for m in sorted(loaded) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)] == []


def _median_ms(command: list[str], runs: int) -> float:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(command, input=PAYLOAD, capture_output=True, text=True, check=True)
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times)


@pytest.mark.perf
def test_a_pre_tool_use_hook_stays_within_its_time_budget(hook: Path) -> None:
    baseline = _median_ms([sys.executable, "-c", "import json, dataclasses"], 11)
    measured = _median_ms([sys.executable, str(hook)], 11)
    bare = _median_ms([sys.executable, "-c", "pass"], 11)
    print(f"\nhook {measured:.1f} ms, `import json, dataclasses` {baseline:.1f} ms, bare interpreter {bare:.1f} ms")
    assert measured - baseline <= 12, f"the hook takes {measured - baseline:.1f} ms more than its stdlib baseline"
