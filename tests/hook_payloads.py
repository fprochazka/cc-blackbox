"""Hook payload builders, one per event.

Each payload carries the fields of the example in the Claude Code hooks reference plus the keys the Claude Code 2.1.319
input builder sets for that event. Every id, path and text is invented.
"""

from __future__ import annotations

from typing import Any

SESSION_ID = "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11"
CWD = "/home/user/projects/example"
TRANSCRIPT = f"/home/user/.claude/projects/-home-user-projects-example/{SESSION_ID}.jsonl"

COMMON: dict[str, Any] = {
    "session_id": SESSION_ID,
    "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
    "transcript_path": TRANSCRIPT,
    "cwd": CWD,
    "scratchpad_dir": f"/tmp/claude-1000/-home-user-projects-example/{SESSION_ID}/scratchpad",
    "permission_mode": "default",
    "effort": {"level": "high"},
}

_SPECIFIC: dict[str, dict[str, Any]] = {
    "PreToolUse": {
        "tool_name": "Bash",
        "tool_input": {
            "command": "npm test",
            "description": "Run test suite",
            "timeout": 120000,
            "run_in_background": False,
        },
        "tool_use_id": "toolu_01EXAMPLE",
    },
    "PermissionRequest": {
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf node_modules", "description": "Remove node_modules directory"},
        "permission_suggestions": [
            {
                "type": "addRules",
                "rules": [{"toolName": "Bash", "ruleContent": "rm -rf node_modules"}],
                "behavior": "allow",
                "destination": "localSettings",
            }
        ],
    },
    "PermissionDenied": {
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/build", "description": "Clean build directory"},
        "tool_use_id": "toolu_01EXAMPLE",
        "reason": "[Irreversible Local Destruction]",
        "permission_mode": "auto",
    },
    "PostToolUse": {
        "tool_name": "Write",
        "tool_input": {"file_path": "/path/to/file.txt", "content": "file content"},
        "tool_response": {"filePath": "/path/to/file.txt", "type": "create"},
        "tool_use_id": "toolu_01EXAMPLE",
        "duration_ms": 12,
    },
    "PostToolUseFailure": {
        "tool_name": "Bash",
        "tool_input": {"command": "npm test", "description": "Run test suite"},
        "tool_use_id": "toolu_01EXAMPLE",
        "error": "Exit code 1\nError: Cannot find module 'express'",
        "is_interrupt": False,
        "duration_ms": 4187,
    },
    "PostToolBatch": {
        "tool_calls": [
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/path/to/accounts.py"},
                "tool_use_id": "toolu_01",
                "tool_response": "1\tfrom __future__ import annotations\n",
            },
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/path/to/transactions.py"},
                "tool_use_id": "toolu_02",
                "tool_response": "1\timport os\n",
            },
        ]
    },
    "UserPromptSubmit": {"prompt": "Write a function to calculate the factorial of a number", "session_title": "maths"},
    "UserPromptExpansion": {
        "expansion_type": "slash_command",
        "command_name": "example-skill",
        "command_args": "arg1 arg2",
        "command_source": "plugin",
        "prompt": "/example-skill arg1 arg2",
    },
    "Stop": {
        "stop_hook_active": True,
        "last_assistant_message": "I've completed the refactoring.",
        "background_tasks": [
            {"id": "task-001", "type": "shell", "status": "running", "description": "tail logs", "command": "tail -f x"}
        ],
        "session_crons": [
            {"id": "cron-001", "schedule": "0 9 * * 1-5", "recurring": True, "prompt": "check the build"}
        ],
    },
    "SubagentStop": {
        "stop_hook_active": False,
        "agent_id": "def456",
        "agent_type": "Explore",
        "agent_transcript_path": f"/home/user/.claude/projects/x/{SESSION_ID}/subagents/agent-def456.jsonl",
        "last_assistant_message": "Analysis complete.",
        "background_tasks": [],
        "session_crons": [],
    },
    "StopFailure": {
        "error": "rate_limit",
        "error_details": "429 Too Many Requests",
        "last_assistant_message": "API Error: Rate limit reached",
    },
    "SubagentStart": {"agent_id": "agent-abc123", "agent_type": "Explore"},
    "MessageDisplay": {
        "turn_id": "0c9e6a2f-7d41-4f4e-9a15-3f4f7c2b8d10",
        "message_id": "5b2a9c8e-1f63-4d8a-b7c4-9e0d2a6f1c3b",
        "index": 0,
        "final": False,
        "delta": "Here is the plan:\n",
    },
    "Notification": {
        "message": "Claude needs your permission",
        "title": "Permission needed",
        "notification_type": "permission_prompt",
    },
    "TaskCreated": {
        "task_id": "task-001",
        "task_subject": "Implement user authentication",
        "task_description": "Add login and signup endpoints",
        "teammate_name": "implementer",
        "team_name": "session-a1b2c3d4",
    },
    "TaskCompleted": {
        "task_id": "task-001",
        "task_subject": "Implement user authentication",
        "task_description": "Add login and signup endpoints",
        "teammate_name": "implementer",
        "team_name": "session-a1b2c3d4",
    },
    "TeammateIdle": {"teammate_name": "researcher", "team_name": "session-a1b2c3d4"},
    "SessionStart": {
        "source": "resume",
        "model": "claude-opus-5",
        "session_title": "auth-refactor",
        "seconds_since_last_response": 5400,
        "context_tokens": 182340,
        "prompt_cache_likely_expired": True,
        "estimated_cache_write_usd": 1.1396,
    },
    "SessionEnd": {"reason": "other"},
    "Setup": {"trigger": "init"},
    "InstructionsLoaded": {
        "file_path": "/home/user/projects/example/CLAUDE.md",
        "memory_type": "Project",
        "load_reason": "path_glob_match",
        "globs": ["src/**"],
        "trigger_file_path": "/home/user/projects/example/src/a.py",
    },
    "ConfigChange": {"source": "project_settings", "file_path": "/home/user/projects/example/.claude/settings.json"},
    "CwdChanged": {"old_cwd": CWD, "new_cwd": f"{CWD}/src"},
    "DirectoryAdded": {"directory": "/home/user/projects/other", "source": "slash_command"},
    "FileChanged": {"file_path": f"{CWD}/.envrc", "event": "change"},
    "WorktreeCreate": {"name": "feature-auth"},
    "WorktreeRemove": {"worktree_path": f"{CWD}/.claude/worktrees/feature-auth"},
    "PreCompact": {"trigger": "manual", "custom_instructions": None},
    "PostCompact": {"trigger": "manual", "compact_summary": "Summary of the compacted conversation."},
    "PreModelSwitch": {
        "from_model": "claude-sonnet-5",
        "to_model": "claude-opus-5",
        "requested_model": "opus",
        "source": "command",
        "context_tokens": 182340,
        "prompt_cache_warm": True,
        "cache_ttl": "5m",
        "estimated_cache_write_usd": 1.1396,
        "pricing": "catalog",
    },
    "PostModelSwitch": {
        "from_model": "claude-sonnet-5",
        "to_model": "claude-opus-5",
        "requested_model": None,
        "source": "auto",
        "context_tokens": 0,
        "prompt_cache_warm": False,
        "cache_ttl": "1h",
        "estimated_cache_write_usd": 0,
        "pricing": "default",
    },
    "Elicitation": {
        "mcp_server_name": "my-mcp-server",
        "message": "Please provide your project key",
        "mode": "form",
        "requested_schema": {"type": "object", "properties": {"project_key": {"type": "string"}}},
    },
    "ElicitationResult": {
        "mcp_server_name": "my-mcp-server",
        "action": "accept",
        "content": {"project_key": "DOCS"},
        "mode": "form",
    },
}

EVENTS: tuple[str, ...] = tuple(_SPECIFIC)


def payload(event: str, **overrides: Any) -> dict[str, Any]:
    """Return the sample payload of ``event``; an override of ``None`` removes the key."""
    data = {**COMMON, "hook_event_name": event, **_SPECIFIC[event], **overrides}
    return {k: v for k, v in data.items() if v is not None or k in _SPECIFIC[event] and k not in overrides}
