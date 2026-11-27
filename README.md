> **Unofficial.** This project is not affiliated with or endorsed by Anthropic. It relies on undocumented Claude Code internals that can change in any Claude Code release.

# cc-blackbox

"cc" stands for Claude Code. cc-blackbox is a typed Python library for tools that work next to [Claude Code](https://code.claude.com): find the Claude Code profiles on the machine, list the sessions that run under them, send a message into one of them, read what past and running sessions recorded in their transcripts, and write hooks in Python.

A typical user is a background watcher started from a Claude Code session, for example one that polls CI and tells the session when a pipeline fails.

Claude Code does not document any of this except hooks. The library reads the files Claude Code writes and speaks the socket protocol Claude Code uses between its own sessions, as found in Claude Code 2.1.x. It never writes to a transcript. Read [Limitations](#limitations) before you depend on it.

## Install

```shell
uv add cc-blackbox
```

or `pip install cc-blackbox`. It needs Python 3.12 or newer and depends only on PyYAML.

To use changes that are not released to PyPI yet, pin a tag of this repository as a git source:

```toml
[project]
dependencies = ["cc-blackbox"]

[tool.uv.sources]
cc-blackbox = { git = "https://github.com/fprochazka/cc-blackbox", tag = "v0.15.0" }
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

`profile.config_dir` is the symlink-resolved path. You can pass it to `claude` as `CLAUDE_CONFIG_DIR`:

- **Symlinks change nothing on disk.** Claude Code uses `CLAUDE_CONFIG_DIR` as given, without resolving symlinks. The resolved path and a symlink to it reach the same files. The project slug comes from the session's cwd alone, so the config dir's spelling does not change it. Only the paths Claude Code reports start with the spelling it got, such as a hook's `transcript_path`.
- **`~/.claude` is the exception.** With `CLAUDE_CONFIG_DIR` unset, Claude Code keeps its global config in `~/.claude.json`, next to the dir. With `CLAUDE_CONFIG_DIR=~/.claude` it reads `~/.claude/.claude.json` instead. To run under the `default` profile, leave the variable unset.
- **`CLAUDE_CODE_PROJECT_DIR_NAME`.** When `CLAUDE_CONFIG_DIR` is set, Claude Code names the project dir after this variable instead of the slug, when it holds 1 to 64 letters, digits, `-` or `_`. `find_session_transcript()` still finds such a session by scanning every project dir.

### Rate limits

Claude Code sends an account's rate limits only to the status line command it runs. The launchers of [claude-code-auth-switch](https://github.com/fprochazka/claude-code-auth-switch) put their own status line in front of yours, and it merges the `rate_limits` of every session of the profile into `<config dir>/auth-switch-rate-limits.json`. `profile.rate_limits()`, `read_rate_limits(profile_or_config_dir)` and `active_session.rate_limits` read that file into a `RateLimits`:

```python
from cc_blackbox import list_profiles

for profile in list_profiles():
    limits = profile.rate_limits()
    window = limits.five_hour if limits is not None else None
    if window is None:
        continue
    if window.is_reset():
        print(f"{profile.name}: reset at {window.resets_at}, was {window.used_percent:.0f}%")
    else:
        print(f"{profile.name}: {window.used_percent:.0f}% until {window.resets_at}, measured {window.measured_at}")
```

- **Windows.** `five_hour` and `seven_day` are the windows of a claude.ai subscription, `spend_limit` the spend limit of a Claude apps gateway, with `used_usd`, `limit_usd` and `period` in its `raw` when Claude Code reported them. `windows` holds every window by name, also ones a newer Claude Code adds. Each `RateLimitWindow` has `used_percent`, `resets_at`, `measured_at` (the last time a session reported the value), `changed_at` and the `session_id` that reported it.
- **Per account, not per session.** Every session of the profile writes to the same file. Within a window usage only grows, so a session that has been idle for hours, and still reports the values of its last response, cannot lower a value or bring back an older window.
- **Reset.** `window.is_reset()` tells whether the window's `resets_at` has passed, compared with the current time on every call, so a long-running process can keep the object and ask again; pass a timezone-aware `now` to choose the moment, a naive one raises `ValueError`. `used_percent` of a reset window is the usage from before the reset. The current usage is unknown until a session reports the new window; it is not 0, because the account may have been used elsewhere since. A window without `resets_at` is never reported as reset.
- **No expiry.** With no session running, the values stay as last observed until their windows reset. They are a lower bound of the account's usage at `measured_at`: other machines and claude.ai use the same account, and this machine's sessions see that only with their next response. The library never drops a value for its age; decide with `measured_at`.
- **None.** `None` when no session of the profile was started through an auth-switch launcher since it learned to write the file, for an account without subscription limits such as an API key or Vertex AI, and for a file that is not readable JSON or has a format version other than 1. Reads the file on every call, except `active_session.rate_limits`, which is read on first access like the other session attributes. With shared sessions dirs, a session's profile may be inferred; see `profile_source`.

### Sessions

`list_active_sessions()` reads `<config dir>/sessions/<pid>.json`, which every running session writes, and drops records whose process has ended. Every field except the pid can be `None`, because the record shape changes between Claude Code versions. `session.raw` holds the whole record.

`find_session()` accepts an address (`<profile>/<name>`), a session name, a session id, `pid:<n>`, or a unique prefix of a session id. Session names are not unique: Claude Code generates names like `my-project-a1` without checking for collisions, so an ambiguous query raises `AmbiguousSession` with the candidates.

`cwd` is the session's current directory from its registry record, which changes when the session enters a worktree or runs `/cd`. `launch_cwd` is the directory the session's process started in. Claude Code does not record it in the registry, so it is read from the first transcript record that process wrote; for a resumed session that takes a few reads spread over the transcript.

**Shared sessions dirs.** Several profiles can share one `sessions/` dir, for example when [claude-code-auth-switch](https://github.com/fprochazka/claude-code-auth-switch) symlinks it to `~/.claude/sessions` so that their sessions can list and message each other. A record does not say which profile wrote it. `list_active_sessions()` reads each real dir once, lists every session once, and sets `session.profile` to the profile that runs it. `session.profile_source` says how that profile was found:

- `log`: the start log that auth-switch's SessionStart hook keeps next to the transcript, matched by pid and start time;
- `environ`: `CLAUDE_CONFIG_DIR` in the session process's environment, which the library can read only on Linux and only while the process runs;
- `claude_json`: exactly one of the profiles that share the dir names the session as `projects[<dir>].lastSessionId` in its `.claude.json`. Claude Code writes that value when a session ends, so this finds a resumed session whose earlier run ended under that profile. Each file is read at most once per call, and only when the steps above did not decide. A file that cannot be read or parsed counts as empty;
- `dir`: only one profile uses the dir;
- `inferred`: a guess, the first selected profile that shares the dir. `session.profile_inferred` is `True`.

A session that runs under a profile you did not select is left out, even when its record sits in a selected profile's dir.

`tmux` is the session's tmux pane as Claude Code recorded it at startup, such as `main:@1.%4`. Pane, window and session ids are numbered per tmux server, so a session started on its own server (`tmux -L <name>`) can record `%0` while the default server has a `%0` of its own. `tmux_socket` is the socket of the server the session runs on, and `tmux_server_pid` that server's pid, which tells a server restarted on the same socket apart: pass `-S <tmux_socket>` to reach the pane. Both come from `TMUX` in the session process's environment, so both are `None` when that cannot be read (outside Linux, a process of another user), without `TMUX`, or for a socket path that is not absolute. `tmux_server_pid` alone is `None` when the socket is known but the pid field does not parse.

`scratchpad_dir` is the session's scratchpad directory, which Claude Code derives from the launch directory and keeps after `/cd`. Claude Code creates it on first use, so it may not exist yet. When the session has written no transcript record yet, `launch_cwd` is `None`, the scratchpad is derived from `cwd`, and `scratchpad_dir_inferred` is `True`.

### Messages

`send_message()` writes one frame to the session's messaging socket. The receiving model sees your text after the line `Another Claude session sent a message:` and before a fixed footer that says the message did not come from the user. It treats the message as a request from a teammate, not as an instruction from the user.

- `sender` is the receiver's rate-limit key. The model never sees it. Give each independent sending process its own label.
- With `sender_name`, the text is wrapped in a `<cross-session-message>` envelope, so the receiver's terminal shows it as a message from that name. The envelope's reply address is `did:<sender>`, which the model cannot send a reply to.
- `RateLimitedSender` stays below the receiver's rate limit and joins queued messages into one.
- `rename_session()` asks a session to rename itself.

### Event streams

A watcher that runs as a background Bash task of a session can report its events in two ways: one JSON line per event on stdout, which the session reads from the task output, or a message into the session. The event stream module covers both, so that every such tool behaves the same way:

```python
import os
import sys

from cc_blackbox import (
    EventEnvelope,
    MessageEventSink,
    MessagingUnavailable,
    StdoutEventSink,
    choose_event_stream,
    current_session_or_none,
)

try:
    choice = choose_event_stream("auto", current_session_or_none())
except MessagingUnavailable as exc:  # only for "claude-message"
    sys.exit(f"cannot message the session: {exc}")
if choice.fallback:
    print(f"ci-watch: not messaging the session: {choice.fallback}", file=sys.stderr)

if choice.mode == "claude-message":
    assert choice.session is not None
    sink = MessageEventSink(
        choice.session,
        EventEnvelope("ci-watcher", {"pipeline": "1234"}, record="/tmp/ci-1234/events.jsonl"),
        sender=f"ci-watch:{os.getpid()}",
        sender_name="ci-watch pipeline 1234",
        status=lambda line: print(f"ci-watch: {line}", file=sys.stderr),
        flush_on={"watch_ended"},
    )
else:
    sink = StdoutEventSink()
```

An event is any object with `kind`, `seq` and `to_json()`, see the `StreamEvent` protocol. The tool calls `sink.emit(events)` for new events, `sink.tick()` whenever `sink.seconds_until_flush()` comes due, ends when `sink.alive()` turns `False`, and calls `sink.close()` at the end.

- **Choice.** `auto` picks `claude-message` when the process runs under a session that can receive messages, see `messaging_problem()`, and stdout otherwise. Under a session, `choice.fallback` says why it did not message it, for example because the session speaks a peer protocol this version does not know. `claude-message` raises `MessagingUnavailable` with that reason instead of falling back.
- **Batching.** Events that arrive close together go out as one message: the first event of a burst waits 3 s (`burst_wait`) for the rest. An event whose kind is in `flush_on` sends the batch at once.
- **Envelope.** Each message is `<tag attributes seq="first-last">`, one JSON line per event, and the closing tag, because the receiving model sees the content but not the sender name. `seq` keeps every body unique, so the receiver's duplicate check never drops a real event. `payload` sets what each line holds; it is `event.to_json()` by default.
- **Announcement.** The first batch that holds an event of kind `announce_on`, or the first batch at all when it is `None`, also carries `announce_attributes`, and its events pass through `announced`. Use it for long values that the receiver needs only once, such as paths.
- **Size cap.** A batch above 64 KB (`max_bytes`) is sent as a count per kind, with `"read": record` when the envelope names a `record`.
- **Rate limits.** The sink sends through a `RateLimitedSender` with `sender` as the rate-limit key and `sender_name` as the name the receiver shows. `close()` waits up to 10 s for messages that the limit held back.
- **Lifetime.** `alive()` turns `False` when the socket refuses a message, or when the session this process runs under can no longer receive messages, checked every 30 s.
- **Runtime fallback.** When a send raises `UnsupportedProtocol` or `MessagingUnavailable`, for example after a Claude Code update, the sink reports `stream fallback: cannot message the session (…); events now go to stdout` through `status` and sends this batch and every later one to stdout, or to the `fallback` sink. Other send errors are reported through `status` and the batch is dropped, so keep a full record of the events elsewhere.

### Transcripts

Claude Code writes every session to `<config dir>/projects/<slug>/<session id>.jsonl`, where `<slug>` is the launch directory with every non-alphanumeric character replaced by `-`. Subagent transcripts and large tool results sit under `<slug>/<session id>/`. `list_session_transcripts()` lists the sessions of every profile, reading only the start and the end of each file:

```python
from cc_blackbox import list_session_transcripts, sessions_containing, shell_commands, tool_calls

sessions = list_session_transcripts(cwd_within=["/home/me/src/my-project"])
for session in sessions[:10]:
    print(session.last_record_at, session.session_id, session.title or session.first_prompt, session.cwd)

for session in sessions_containing(sessions, ["flaky test"]):
    entries = list(session.entries(types={"assistant", "user"}))
    for command in shell_commands(entries):
        if command.is_error:
            print(session.session_id, command.command)
    print(sum(1 for call in tool_calls(entries) if call.name == "Edit"), "edits")
```

- **Profiles share transcripts.** Profiles often symlink `projects/` to one directory, so a listing covers each distinct projects dir once and `session.projects_dir.profiles` names every profile that uses it. A transcript does not record which of them wrote it.
- **Who ran a session.** claude-code-auth-switch can log every start of a session to `<slug>/<session id>/auth-switch-profile-starts.jsonl`. `session.profile_starts()` returns those starts, each with its pid, profile and config dir, and the auth-switch role the run was started with: `role`, `role_prompt` (the file holding the role's prompt) and `role_prompt_mode` (`append` or `replace` when that file is in the system prompt, `none` when a hook injects it). Each role field is `None` when the run had no role or the line was logged before auth-switch had roles. A resumed session has one per run, and the runs can belong to different profiles and roles. `session.owner()` returns the profile that ran the session last and how that was found: the newest start in the log, else exactly one profile's `.claude.json` that names the session, else a projects dir that only one profile uses. `list_session_transcripts(started_by=["work"])` keeps the sessions that a profile started or resumed, and uses the same fallbacks for a session without the log.
- **Which files are sessions.** Only a file named by a session id, which is a UUID, is a session. Other tools put their own `.jsonl` files next to transcripts, and `session_id_for_path()` maps any file under a projects dir to the session that owns it, or to `None`.
- **`cwd_within`.** It keeps the sessions launched in a dir or below it, and it reads only the project dirs whose name can belong to such a session. A session stored under a project dir of another name, for example one resumed from elsewhere, is found only by a listing without `cwd_within`.
- **Symlinked dirs.** Claude Code names the project dir after the launch dir with symlinks resolved, and records that path as `cwd`. `cwd_within` matches a dir under the spelling you pass and under its resolved path. `project_dirs_for(cwd)` returns the project dirs of one dir, the resolved spelling first.
- **Finding one session.** `find_session_transcript(session_id)` checks every project dir of every profile. With `cwd=` set to the dir the session most likely ran in, it checks that dir's project dirs first. `find_session_transcript(session_id, ["personal"])` searches one profile, and `list_profiles([config_dir])` makes a profile of any config dir.
- **Titles.** `title` is the newest title the user set, else the newest generated title. `first_prompt` is the first prose prompt typed by a human, for sessions without a title.
- **When a session was last used.** `last_record_at` is the newest record timestamp, from the same two reads as the rest of the listing, and the listing is sorted by it. Records that involve no model also carry a timestamp: `/login` and other local commands, attachments, PR links, the notices Claude Code adds on a rename. They can land days after the last response. `last_model_activity_at` is the timestamp of the newest `assistant` record, which Claude Code writes for every model response and only for those, across the main transcript and the subagent transcripts, because a background subagent writes only to its own. The file mtime says neither: Claude Code keeps appending untimestamped bookkeeping to idle transcripts.
- **Cost.** `cost_state` is the session's newest `cost-state` record as a `CostState`: total cost in USD, API, tool and wall-clock durations, lines added and removed, and a `ModelUsage` per model with input, output, thinking and cache tokens. Claude Code writes the record only when a session process exits. For an ended session it is the session's totals; a running session that was never resumed has none, and a resumed running session has the totals as of its last exit, without the current run. One transcript suggests the totals accumulate across resumes: its `startTime` stayed the same over three runs. Older Claude Code versions do not write the record, and the value is then `None`.
- **Where a session is now.** `cwd` and `git_branch` come from the first record that has them, the launch directory. `last_cwd` and `last_git_branch` come from the newest one, and follow the session into a worktree or a `/cd`. `name` is the newest name from the `agent-name` or `custom-title` records.
- **Context fill.** `context_window` is a `ContextWindow`: how full the context was at the newest response of the main conversation, as Claude Code's status line shows it. `used` is the input, cache creation and cache read tokens of that response, `size` the window in tokens, and `percent` the two rounded as Claude Code rounds them. Output tokens and subagents do not count; a subagent has a context of its own. After a compaction the session has no measured fill until it answers again, so `used` is 0 and `compacted` is `True`, like the status line's 0%. The model then comes from the response before the compaction. `None` means the transcript has neither a response nor a compaction, as right after a `/clear`.
- **Window size.** No transcript record holds the window size, so `size` comes from the model id, and `size_source` says how: `model_tag` for a `[1m]` suffix on the requested model, `model_table` for this package's table of models, `default` for the 200k Claude Code assumes for an unknown model. A fill above the table's size proves the 1M window: `used_exceeds_table`. `active_session.context_window` also applies `CLAUDE_CODE_DISABLE_1M_CONTEXT` from the settings files and the process environment, with `env_disable_1m`. The transcript does not show a provider that refuses the 1M window, or a fallback to a smaller model, so `size` can be too large for those. The table lives in `sessions/context_window.py` and follows the Claude Code model docs; a new model of a known family needs no change, a new family does.
- **Read on first access.** `last_model_activity_at`, `cost_state`, `context_window`, `name`, `last_cwd` and `last_git_branch` are `functools.cached_property` attributes. Each reads the transcript backwards from its end on first access, usually one 64 KiB chunk, and keeps the value, so a listing of thousands of sessions pays only for the ones you ask about. A transcript that lacks the record is read whole, and `last_model_activity_at` also reads the subagent transcripts written to after the newest response found. The object is a snapshot: read the transcript again for current values.
- **Running sessions.** `active_session.transcript_path` is the transcript the registry listing already located, and `active_session.transcript` reads it into a `SessionTranscript` on first access. `active_session_for(transcript)` goes the other way. `active_session.profile_starts()` reads the auth-switch start log of the session, also before its transcript exists: the start hook writes the log at startup, the transcript comes only with the first prompt. `active_session.process_start()` is the start of the running process among them, so a session resumed by several processes, or one whose pid was reused, gets the role of its own run. After a `/clear` or an in-process `/resume`, the registry record names the new session id and the start log of that id holds the process's start. The registry's `cwd` changes when a session enters a worktree or runs `/cd`; the transcript's `cwd` stays the launch directory of its first run.

`iter_entries()` and `session.entries()` stream the records one line at a time. `user`, `assistant`, `system` and `attachment` records get their own classes with typed content blocks, every other record type is a plain `Entry`, and `raw` always holds the whole record. With `types=`, lines that cannot hold a wanted type are skipped before they are decoded.

The extractors take entries and yield typed facts in file order: `prompts()`, `assistant_texts()`, `tool_calls()` joined with their results, `subagent_runs()`, `files_touched()`, `shell_commands()`, `failures()`, `token_usage()`, `compactions()`, and `conversation()`, which is prompts, assistant text and subagent results without tool noise.

- **Who sent a prompt.** `Prompt.origin` is `human`, `peer`, `task-notification` or `coordinator`. Prompts typed while Claude works are recorded as attachments, and `prompts()` returns them with `queued` set. The prompt of a scheduled task or wakeup, and of a headless or SDK session, has no `origin`. `prompts()` skips such a prompt when Claude Code marked it as meta, as it does most scheduled ones, and otherwise returns it with the origin guessed from its text, usually `human`, as for some scheduled prompts and every SDK prompt. `UserEntry.turn_origin` names what started a turn on the record that opens it, for those prompts too: `human`, `peer`, `scheduled`, `task_notification`, `sdk` or `system`, with the firing wakeup in `wakeup_source`.
- **Tool call timing.** `ToolCall.timestamp` is when the call's block streamed and `result_timestamp` when its result was written. `timestamp` is only a lower bound of a call's start, and the calls of one response, which share a `request_ref`, can overlap: Claude Code can start a call while the response still streams, and runs concurrency-safe tools such as Read, Grep or Agent in parallel. A call of a tool that is not concurrency-safe, such as Bash, Edit or Write, may also have waited for the calls before it. `permission_decision` says who let the call run or stopped it, from allow rules, a hook, the auto mode classifier or the user. Transcripts of Claude Code before about 2.1.294 have no `permission_decision`.
- **Token usage.** Claude Code writes one record per content block and repeats the message's whole usage on each, so `token_usage()` counts a message once.
- **Compactions.** A compaction continues in the same transcript, so the records before its boundary stay in the file. Newer Claude Code keeps the last stretch of the conversation in the context next to the summary, and does not write those records again: they run from `Compaction.preserved_head_uuid` to the boundary record, whose uuid is `Compaction.uuid`. What the model saw after a compaction is the summary, that stretch, and the records after the boundary. Transcripts of Claude Code before about 2.1.266 have no preserved stretch and no `post_tokens`. `Compaction.duration_ms` is how long it took. One that took about a second or more made the session wait, manual or auto alike; one done in the background, or a manual one that reused a summary computed in advance (`compactMetadata.precomputed`), takes tens of milliseconds.
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
- **A session in bypass-permissions mode holds peer messages** until its user approves each one, unless it can verify the sender as one of its own child processes or its user set [`crossSessionInbound`](https://code.claude.com/docs/en/settings-reference#crosssessioninbound). A tool that messages such a session looks silent until then. Start the tool from that session's Bash tool, so that it is the session's child. When the target is the session the process runs under (`CLAUDE_CODE_MESSAGING_SOCKET`) and `CLAUDE_CODE_MESSAGING_TOKEN` is set, `send_message()` and `rename_session()` first send the [documented auth line](https://code.claude.com/docs/en/cross-session-messaging) `{"type":"auth","token":"…"}`, which proves the sender is that session's child. On Linux Claude Code decides that from the process tree and ignores the token; on macOS the token counts when the process check finds no evidence; in a container where Claude Code runs as PID 1 the token is the only evidence. The token never goes to another session, is no signed identity, and changes nothing about how the message is labelled.
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

- **Linux:** everything works. Liveness checks compare the process start time, which catches reused pids. The scratchpad path uses the temp dir from the session's settings files or its process environment. `tmux_socket` and `tmux_server_pid`, the tmux server a session runs on, come from `TMUX` in its process environment.
- **macOS:** profiles, sessions and messaging use the same code as on Linux. Liveness checks the pid only. When the session's settings files do not set `CLAUDE_CODE_TMPDIR`, the scratchpad path is a guess based on this process's environment, and `scratchpad_dir_inferred` is `True`. `tmux_socket` and `tmux_server_pid` are `None`, except for `current_session()`. This has not yet been checked against a real macOS install.
- **Windows:** profiles and sessions can be read, but liveness is not checked. Messaging raises `MessagingUnavailable`: Claude Code uses named pipes with a token there, which this version does not implement.

## Developing

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. These commands set up the repo and run the checks that CI runs:

```shell
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

CI runs the same checks on every push to master and on every pull request. It runs the tests on Ubuntu with Python 3.12, 3.13, 3.14 and 3.15, and on macOS with Python 3.12 and 3.15.

The tests build config dirs with anonymised session records and transcripts in a temp dir, and check the messaging bytes against a Unix socket server started by the test. They never touch a real Claude Code. macOS limits a Unix socket path to 104 bytes, and a pytest `tmp_path` there is close to that, so a test that opens a socket must put it in the `socket_dir` fixture, a short dir under `/tmp`.

`uv run pytest -m perf -s` also measures how long a PreToolUse hook built on `run_hook()` takes, against `python -c "import json, dataclasses"`. Run it when a change touches the hook path: what `cc_blackbox.hooks` imports, or how it parses a hook's input. CI skips it, because wall-clock time on a shared CI runner is too noisy to gate the build on, and releases do not run it.

### Commit hooks

cc-blackbox is developed against real Claude Code config dirs, so project paths, session names and organisation names from them end up in terminals, scratch files and test runs, and from there they can slip into a commit. `scripts/hooks` holds a pre-commit and a commit-msg hook that refuse a commit when a staged line or the commit message matches a private identifier. The identifiers live in a file outside the repository, because listing them here would publish the very strings the hooks exist to keep out. Set it up once per clone:

```shell
git config core.hooksPath scripts/hooks
git config hooks.forbiddenPatternsFile ~/.config/git/forbidden-patterns.txt  # any path outside the repo
```

The file holds one extended regex per line, matched case-insensitively; blank lines and `#` comments are ignored. Until `hooks.forbiddenPatternsFile` points at a file with at least one pattern, both hooks refuse every commit and print how to fix it. Tests and docs use generic paths such as `/home/user/project-a`.

A rebase does not run the hooks. After you reword or squash commits in a rebase, check the new messages against the patterns file yourself before you push.

### Live contract test

The live contract tests check that the installed Claude Code still matches what cc-blackbox expects. Releases do not run them. Run them after a Claude Code update, and when a change touches the socket protocol or the format of the records cc-blackbox reads. When they pass after an update, add the version to `TESTED_CLAUDE_CODE_VERSIONS` in `src/cc_blackbox/compatibility.py`:

```shell
uv run pytest -m live -s
```

The test starts a headless session with `claude -p` in a temporary directory and a socket path of its own, using the profile in your `CLAUDE_CONFIG_DIR`. It waits for the session's registry record, checks `check_compatibility()`, sends a marker message, and checks that the reply repeats it. Then it reads the session's transcript, checks that it is aligned, that the marker arrived as a prompt from a peer, and that the reply is assistant text. Then it stops the session and deletes the transcript.

A second test registers a PreToolUse hook built on `run_hook()` through `--settings`, and asks a headless session to run `touch ran`, which an allow rule would let through. It checks that the hook's deny kept the file from being created, that the tool result carries the deny reason, and that the real payload parsed with no type mismatches.

- It needs the `claude` CLI on `PATH`, logged in.
- It spends model usage: three short turns on `haiku`, about 0.004 USD in total. Set `CC_BLACKBOX_LIVE_MODEL` to use another model.
- The default `pytest` run and CI skip them.

### Trying a change in a project that uses cc-blackbox

To run another project's tests against an unreleased change, add your checkout to that project's environment for one run, and leave its `uv.lock` unchanged:

```shell
uv run --frozen --with-editable ../cc-blackbox pytest
```

Do not commit a path source for cc-blackbox in the other project. Its CI runs `uv sync --locked`, which cannot resolve a path outside the repository. Release cc-blackbox first, then move the other project's pin to the released version.

## Releasing

A release goes to PyPI through GitHub Actions. Pushing a `v*` tag runs `.github/workflows/publish.yml`, which builds the package with `uv build` and publishes it through PyPI trusted publishing, in the `pypi` environment. There is no changelog file: the release notes live in the body of the release commit and in the GitHub release.

Pick the version by the change, within 0.x:

- New public API goes into a minor release, 0.N.0. A fix goes into a patch release, 0.N.M.
- A breaking change also goes into a minor release. Say what breaks in the release commit and in the release notes. Before you release it, check the projects that use cc-blackbox for calls that would break.
- Projects that use cc-blackbox pin a range such as `cc-blackbox>=0.8.0,<0.9`. They get a patch release on their next lock update, but a minor release only after they move the pin.

To release version `X.Y.Z`:

1. Make sure master is green and the commits since the last tag hold no private identifiers.
2. Bump the version in `pyproject.toml`, in `__version__` in `src/cc_blackbox/__init__.py`, and in the pinned `tag = "vX.Y.Z"` in the README's install section. Run `uv lock` to update `uv.lock`.
3. Commit the four files as `Release X.Y.Z`. In the body, summarize what the release adds and changes, and end with "The README pins vX.Y.Z." Add a `Breaking:` paragraph when something breaks.
4. Fast-forward master to the commit with `git push origin HEAD:master`, without a pull request. Wait until the CI run of that commit is green: find its id with `gh run list -w ci.yml -L 1`, then run `gh run watch <run id> --exit-status`.
5. Tag the commit and push only the tag:

   ```shell
   git tag -a vX.Y.Z -m "cc-blackbox X.Y.Z"
   git push origin vX.Y.Z
   ```

6. Wait for the tag's publish run to succeed: find its id with `gh run list -w publish.yml -L 1`, then run `gh run watch <run id> --exit-status`.
7. Check that PyPI serves the version: `curl -s https://pypi.org/pypi/cc-blackbox/X.Y.Z/json | jq -r .info.version`. The version-less URL `https://pypi.org/pypi/cc-blackbox/json` can show the previous version for a few minutes.
8. Write the release notes to a file and create the GitHub release:

   ```shell
   gh release create vX.Y.Z --verify-tag --latest --title vX.Y.Z --notes-file <notes.md>
   ```

9. Check that `gh release list` shows `vX.Y.Z` as `Latest`. A release is not done until it does.

The release title is the tag name, without the project name. The notes have this format:

- One sentence that says what the release does for the user, followed by "Unofficial, not affiliated with or endorsed by Anthropic."
- A code block with `uv add 'cc-blackbox>=X.Y.Z'`.
- `## Added`, `## Changed`, `## Fixed` and `## Breaking` sections, only those that apply. Each bullet names the API and says what it does or what to change.
- The line "All changes are additive; existing calls keep working." only when that is true.

If a project that uses cc-blackbox does not see the new version right after the release, refresh uv's cache of it: `uv lock --refresh-package cc-blackbox`.

## License

MIT
