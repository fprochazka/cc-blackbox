> **Unofficial.** This project is not affiliated with or endorsed by Anthropic. It relies on undocumented Claude Code internals that can change in any Claude Code release.

# cc-blackbox

"cc" stands for Claude Code. cc-blackbox is a typed Python library for tools that work next to [Claude Code](https://code.claude.com): find the Claude Code profiles on the machine, list the sessions that run under them, send a message into one of them, read what past and running sessions recorded in their transcripts, and write hooks in Python.

A typical user is a background watcher started from a Claude Code session, for example one that polls CI and tells the session when a pipeline fails.

Claude Code does not document any of this except hooks. The library reads the files Claude Code writes and speaks the socket protocol Claude Code uses between its own sessions, as found in Claude Code 2.1.x. It never writes to a transcript. Read [Limitations](#limitations) before you depend on it.

## Install

```shell
uv add cc-blackbox
```

or `pip install cc-blackbox`. It needs Python 3.11 or newer and depends only on PyYAML.

To use changes that are not released to PyPI yet, pin a tag of this repository as a git source:

```toml
[project]
dependencies = ["cc-blackbox"]

[tool.uv.sources]
cc-blackbox = { git = "https://github.com/fprochazka/cc-blackbox", tag = "v0.4.0" }
```

## Usage

List the profiles, list the sessions that run under them, pick one and send it a message:

```python
from cc_blackbox import find_session, list_active_sessions, list_profiles, send_message

for profile in list_profiles():
    print(profile.name, profile.source, profile.config_dir)

sessions = list_active_sessions()
for session in sessions:
    print(session.address, session.status, session.cwd)

target = find_session("personal/my-project", sessions=sessions)
send_message(
    target,
    "Pipeline #1234 failed in job `test`. Logs: /tmp/ci-1234/",
    sender="ci-watcher",
    sender_name="CI watcher",
)
```

A tool started from inside a session, directly or from one of its subagents, finds that session without any arguments:

```python
from cc_blackbox import RateLimitedSender, current_session

session = current_session()
if session is not None:
    print(session.scratchpad_dir)
    notifier = RateLimitedSender("ci-watcher", sender_name="CI watcher")
    notifier.send(session, "Pipeline #1234 passed.")
```

### Profiles

Claude Code reads its config dir from `CLAUDE_CONFIG_DIR`, or uses `~/.claude`. `list_profiles()` collects every config dir it can find:

- `~/.claude`, named `default`;
- the profiles of [claude-code-auth-switch](https://github.com/fprochazka/claude-code-auth-switch), from `~/.claude/auth-switch.yaml` or the legacy `~/.claude-profiles/config.yaml`;
- other config dirs under the auth-switch profiles dir, for example a profile removed from the config whose dir stayed behind;
- `$CLAUDE_CONFIG_DIR`.

Each profile has a name that is unique in the result and stable across runs. A dir whose basename clashes with another name gets `cfg-<8 hex chars>`.

### Sessions

`list_active_sessions()` reads `<config dir>/sessions/<pid>.json`, which every running session writes, and drops records whose process has ended. Every field except the pid can be `None`, because the record shape changes between Claude Code versions. `session.raw` holds the whole record.

`find_session()` accepts an address (`<profile>/<name>`), a session name, a session id, `pid:<n>`, or a unique prefix of a session id. Session names are not unique: Claude Code generates names like `my-project-a1` without checking for collisions, so an ambiguous query raises `AmbiguousSession` with the candidates.

`scratchpad_dir` is the session's scratchpad directory. Claude Code creates it on first use, so it may not exist yet.

### Messages

`send_message()` writes one frame to the session's messaging socket. The receiving model sees your text after the line `Another Claude session sent a message:` and before a fixed footer that says the message did not come from the user. It treats the message as a request from a teammate, not as an instruction from the user.

- `sender` is the receiver's rate-limit key. The model never sees it. Give each independent sending process its own label.
- With `sender_name`, the text is wrapped in a `<cross-session-message>` envelope, so the receiver's terminal shows it as a message from that name. The envelope's reply address is `did:<sender>`, which the model cannot send a reply to.
- `RateLimitedSender` stays below the receiver's rate limit and joins queued messages into one.
- `rename_session()` asks a session to rename itself.

### Transcripts

Claude Code writes every session to `<config dir>/projects/<slug>/<session id>.jsonl`, where `<slug>` is the launch directory with every non-alphanumeric character replaced by `-`. Subagent transcripts and large tool results sit under `<slug>/<session id>/`. `list_session_transcripts()` lists the sessions of every profile, reading only the start and the end of each file:

```python
from cc_blackbox import list_session_transcripts, sessions_containing, shell_commands, tool_calls

sessions = list_session_transcripts(cwd_within=["/home/me/src/my-project"])
for session in sessions[:10]:
    print(session.last_activity_at, session.session_id, session.title or session.first_prompt, session.cwd)

for session in sessions_containing(sessions, ["flaky test"]):
    entries = list(session.entries(types={"assistant", "user"}))
    for command in shell_commands(entries):
        if command.is_error:
            print(session.session_id, command.command)
    print(sum(1 for call in tool_calls(entries) if call.name == "Edit"), "edits")
```

- **Profiles share transcripts.** Profiles often symlink `projects/` to one directory, so a listing covers each distinct projects dir once and `session.projects_dir.profiles` names every profile that uses it. A transcript does not record which of them wrote it.
- **Which files are sessions.** Only a file named by a session id, which is a UUID, is a session. Other tools put their own `.jsonl` files next to transcripts, and `session_id_for_path()` maps any file under a projects dir to the session that owns it, or to `None`.
- **`cwd_within`.** It keeps the sessions launched in a dir or below it, and it reads only the project dirs whose name can belong to such a session. A session stored under a project dir of another name, for example one resumed from elsewhere, is found only by a listing without `cwd_within`.
- **Titles.** `title` is the newest title the user set, else the newest generated title. `first_prompt` is the first prose prompt typed by a human, for sessions without a title.
- **Running sessions.** `transcript_for(active_session)` and `active_session_for(transcript)` link the two views by session id. The registry's `cwd` changes when a session enters a worktree; the transcript's `cwd` stays the launch directory.

`iter_entries()` and `session.entries()` stream the records one line at a time. `user`, `assistant`, `system` and `attachment` records get their own classes with typed content blocks, every other record type is a plain `Entry`, and `raw` always holds the whole record. With `types=`, lines that cannot hold a wanted type are skipped before they are decoded.

The extractors take entries and yield typed facts in file order: `prompts()`, `assistant_texts()`, `tool_calls()` joined with their results, `subagent_runs()`, `files_touched()`, `shell_commands()`, `failures()`, `token_usage()`, `compactions()`, and `conversation()`, which is prompts, assistant text and subagent results without tool noise.

- **Who sent a prompt.** `Prompt.origin` is `human`, `peer`, `task-notification` or `coordinator`. Prompts typed while Claude works are recorded as attachments, and `prompts()` returns them with `queued` set.
- **Token usage.** Claude Code writes one record per content block and repeats the message's whole usage on each, so `token_usage()` counts a message once.
- **Branches.** Entries come in file order, which includes branches the conversation left, for example after a rewind.

`sessions_containing()` finds the sessions whose main transcript, subagent transcripts or tool results contain every term (or any, with `match_any=True`). It is literal and ignores case, and it reads raw bytes, so it also matches ids and tool output. It does not need ripgrep. An ASCII term ignores ASCII case only, which keeps it fast; a term with other characters is compared after Unicode case folding, several times slower. `workers=` scans in several processes.

### Hooks

`cc_blackbox.hooks` is for writing [Claude Code hooks](https://code.claude.com/docs/en/hooks) in Python. It parses the JSON a hook gets on stdin into one typed class per event, builds the reply from typed constructors, and runs the two with a defined failure policy. This PreToolUse hook denies one kind of Bash command and has no opinion on the rest:

```python
from cc_blackbox.hooks import PreToolUseInput, PreToolUseReply, ShellInput, run_hook


def guard(event: PreToolUseInput) -> PreToolUseReply | None:
    command = event.typed_tool_input()
    if isinstance(command, ShellInput) and command.command.startswith("glab ci view"):
        return PreToolUseReply.deny("Use `glab-pipeline inspect` instead.")
    return None  # no output: the normal permission flow decides


if __name__ == "__main__":
    run_hook(guard, expect=PreToolUseInput)
```

Register the script as a `command` hook on `PreToolUse` with the matcher `Bash`. One script can serve several events: drop `expect=` and `match` on the event class in the handler. `run_hook(first, second, ...)` calls several handlers in order until one returns a reply, so one small entry point can cover several rule sets, each importing what it needs only when it runs.

- **Input.** All 33 events of the hooks reference have a class, such as `PreToolUseInput`, `StopInput` or `SessionStartInput`. An unknown event becomes `UnknownHookInput`. Unknown fields stay in `raw`, and a field with an unexpected type reads as `None` and is named in `type_mismatches`. `tool_input` is a read-only mapping. `typed_tool_input()` gives a typed view for the documented built-in tools: `ShellInput` for Bash and PowerShell, and `ReadInput`, `WriteInput`, `EditInput`, `GlobInput`, `GrepInput`, `WebFetchInput`, `WebSearchInput` and `AgentInput`.
- **Replies.** Each event that can decide something has a reply class with named constructors, such as `PreToolUseReply.deny(reason)`, `StopReply.block(reason)` or `SessionStartReply(additional_context=...)`. A combination Claude Code rejects or ignores raises `ValueError`, such as a deny without a reason. `HookReply(system_message=...)` fits any event, and `RawReply` writes a mapping as given, for fields this library does not model yet.
- **Exit codes.** Replies answer with exit 0 and JSON. JSON wins the `deny > defer > ask > allow` vote between parallel hooks, and JSON next to an exit-2 block cannot change the outcome. Only the TaskCompleted and TeammateIdle blocks exit 2, and a WorktreeRemove failure exits 1, because those events read nothing else.
- **An allow is weaker than it looks.** An explicit allow skips the user's prompt and overrides other hooks' asks, so return `None` when the hook has no opinion. In auto mode, Claude Code still sends an allowed call to its classifier. A deny rule in the user's settings still beats a hook's allow.
- **Failures.** If the payload cannot be read, a handler raises, or a reply is for another event, the default policy `on_error="pass"` prints nothing and exits 1. Claude Code then shows a hook error notice with the first stderr line and goes ahead. `on_error="block"` writes the event's blocking reply, such as a deny, and `on_error="ask"` sends PreToolUse calls to the permission prompt. A crash before Python runs your code, such as a missing interpreter or a broken install, is out of the library's reach. A hook that must fail closed therefore also sets `"onFailure": "block"` on its hook entry.
- **Import time.** A hook process starts before every tool call it matches. `cc_blackbox` and `cc_blackbox.hooks` load their names on first use, and parsing a payload imports only its event's module. A PreToolUse hook built on `run_hook()` loads none of the session, transcript or messaging code, and takes about 10 ms more than `python -c "import json, dataclasses"`. Keep your own heavy imports inside the handler.
- **Links.** `event.transcript()` reads the session's transcript, and `event.active_session()` finds the running session, for example to message it later. Both import the rest of the library only when called. The transcript can lag the conversation: on Stop and SubagentStop, read `last_assistant_message` instead.

## Limitations

- **This is an undocumented, reverse-engineered interface.** Any Claude Code release can change the record format, the socket protocol or the feature flags behind them. The library parses records tolerantly and checks what each session declares, see [Compatibility](#compatibility). Pin a tag and upgrade on purpose.
- **Delivery is best effort.** The receiver never answers on the socket, so a successful send means only that the bytes were written. The receiver then drops a message without telling you when its sender exceeds 30 messages in a burst or one every 2 seconds, when the same text arrived in the last 30 seconds, or when 50 messages already wait in its queue. Put a sequence number or a time into each message so that two real events with the same text both arrive.
- **A session in bypass-permissions mode holds peer messages** until its user approves each one. A tool that messages such a session looks silent until then.
- **The receiver does not verify the sender.** Any process of the same user can connect to the socket and claim any name. Do not put another session's name or address into your messages.
- **Messages always reach the main session.** A subagent cannot be addressed.
- **Hooks are the one documented interface here.** The library still parses hook input tolerantly, because the hooks reference adds events and fields often. The tests check every reply against the output schema of Claude Code 2.1.319.
- **Transcripts are internal to Claude Code.** Anthropic documents the format as unstable. Claude Code also deletes transcripts older than its `cleanupPeriodDays` setting, 30 days by default.

### Compatibility

cc-blackbox has its own version numbers and never compares Claude Code versions. Claude Code updates more often than the tools built on this library, so a session running a newer Claude Code is fine as long as what it declares is aligned: if the protocol is aligned, it just works.

`check_compatibility(session)`, also available as `session.compatibility`, checks each feature against the session's registry record:

- `messaging` needs a `peerProtocol` this version speaks (currently 1) and a `messagingSocketPath`.
- `scratchpad` needs the `sessionId` and `cwd` strings.
- `liveness` needs the `pid`, and on Linux the `procStart` string.

New record fields and new peer features are additions and do not count against a session. `send_message()` raises `UnsupportedProtocol`, with the reasons, only when `messaging` is not aligned. Nothing else warns.

Transcripts carry no protocol number, so their check looks at the records. Pass a `ReadDiagnostics` to `iter_entries()`: `aligned` is `False` only when a field the library models, such as `message.content`, has an unexpected type. Malformed lines, unknown record types and unknown content blocks are counted but never raise and never count against alignment, because Claude Code adds record types every few releases.

`TESTED_CLAUDE_CODE_VERSIONS` lists the Claude Code versions that the [live contract test](#live-contract-test) passed against: 2.1.319 and 2.1.321. It is information only.

### Platform support

- **Linux:** everything works. Liveness checks compare the process start time, which catches reused pids. The scratchpad path uses the temp dir from the session's settings files or its process environment.
- **macOS:** profiles, sessions and messaging use the same code as on Linux. Liveness checks the pid only. When the session's settings files do not set `CLAUDE_CODE_TMPDIR`, the scratchpad path is a guess based on this process's environment, and `scratchpad_dir_inferred` is `True`. This has not yet been checked against a real macOS install.
- **Windows:** profiles and sessions can be read, but liveness is not checked. Messaging raises `MessagingUnavailable`: Claude Code uses named pipes with a token there, which this version does not implement.

## Development

```shell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

`uv run pytest -m perf -s` also measures how long a PreToolUse hook built on `run_hook()` takes, against `python -c "import json, dataclasses"`. Run it before a release; wall-clock time on a shared CI runner is too noisy to gate the build on.

The tests build config dirs with anonymised session records and transcripts in a temp dir, and check the messaging bytes against a Unix socket server started by the test. They never touch a real Claude Code.

### Live contract test

The live contract tests check that the installed Claude Code still matches what cc-blackbox expects. Run it after a Claude Code update, and add the version to `TESTED_CLAUDE_CODE_VERSIONS` when it passes:

```shell
uv run pytest -m live -s
```

The test starts a headless session with `claude -p` in a temporary directory and a socket path of its own, using the profile in your `CLAUDE_CONFIG_DIR`. It waits for the session's registry record, checks `check_compatibility()`, sends a marker message, and checks that the reply repeats it. Then it reads the session's transcript, checks that it is aligned, that the marker arrived as a prompt from a peer, and that the reply is assistant text. Then it stops the session and deletes the transcript.

A second test registers a PreToolUse hook built on `run_hook()` through `--settings`, and asks a headless session to run `touch ran`, which an allow rule would let through. It checks that the hook's deny kept the file from being created, that the tool result carries the deny reason, and that the real payload parsed with no type mismatches.

- It needs the `claude` CLI on `PATH`, logged in.
- It spends model usage: three short turns on `haiku`, about 0.004 USD in total. Set `CC_BLACKBOX_LIVE_MODEL` to use another model.
- The default `pytest` run and CI skip them.

## License

MIT
