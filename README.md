> **Unofficial.** This project is not affiliated with or endorsed by Anthropic. It relies on undocumented Claude Code internals that can change in any Claude Code release.

# cc-blackbox

"cc" stands for Claude Code. cc-blackbox is a typed Python library for tools that work next to a running [Claude Code](https://code.claude.com): find the Claude Code profiles on the machine, list the sessions that run under them, and send a message into one of them.

A typical user is a background watcher started from a Claude Code session, for example one that polls CI and tells the session when a pipeline fails.

Claude Code does not document any of this. The library reads the files Claude Code writes and speaks the socket protocol Claude Code uses between its own sessions, as found in Claude Code 2.1.x. Read [Limitations](#limitations) before you depend on it.

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
cc-blackbox = { git = "https://github.com/fprochazka/cc-blackbox", tag = "v0.2.0" }
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

## Limitations

- **This is an undocumented, reverse-engineered interface.** Any Claude Code release can change the record format, the socket protocol or the feature flags behind them. The library parses records tolerantly and checks what each session declares, see [Compatibility](#compatibility). Pin a tag and upgrade on purpose.
- **Delivery is best effort.** The receiver never answers on the socket, so a successful send means only that the bytes were written. The receiver then drops a message without telling you when its sender exceeds 30 messages in a burst or one every 2 seconds, when the same text arrived in the last 30 seconds, or when 50 messages already wait in its queue. Put a sequence number or a time into each message so that two real events with the same text both arrive.
- **A session in bypass-permissions mode holds peer messages** until its user approves each one. A tool that messages such a session looks silent until then.
- **The receiver does not verify the sender.** Any process of the same user can connect to the socket and claim any name. Do not put another session's name or address into your messages.
- **Messages always reach the main session.** A subagent cannot be addressed.

### Compatibility

cc-blackbox has its own version numbers and never compares Claude Code versions. Claude Code updates more often than the tools built on this library, so a session running a newer Claude Code is fine as long as what it declares is aligned: if the protocol is aligned, it just works.

`check_compatibility(session)`, also available as `session.compatibility`, checks each feature against the session's registry record:

- `messaging` needs a `peerProtocol` this version speaks (currently 1) and a `messagingSocketPath`.
- `scratchpad` needs the `sessionId` and `cwd` strings.
- `liveness` needs the `pid`, and on Linux the `procStart` string.

New record fields and new peer features are additions and do not count against a session. `send_message()` raises `UnsupportedProtocol`, with the reasons, only when `messaging` is not aligned. Nothing else warns.

`TESTED_CLAUDE_CODE_VERSIONS` lists the Claude Code versions that the [live contract test](#live-contract-test) passed against: 2.1.319. It is information only.

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

The tests build config dirs with anonymised session records in a temp dir, and check the messaging bytes against a Unix socket server started by the test. They never touch a real Claude Code.

### Live contract test

The live contract test checks that the installed Claude Code still matches what cc-blackbox expects. Run it after a Claude Code update, and add the version to `TESTED_CLAUDE_CODE_VERSIONS` when it passes:

```shell
uv run pytest -m live -s
```

The test starts a headless session with `claude -p` in a temporary directory and a socket path of its own, using the profile in your `CLAUDE_CONFIG_DIR`. It waits for the session's registry record, checks `check_compatibility()`, sends a marker message, and checks that the marker is in the session's transcript and in its reply. Then it stops the session and deletes the transcript.

- It needs the `claude` CLI on `PATH`, logged in.
- It spends model usage: two short turns on `haiku`. Set `CC_BLACKBOX_LIVE_MODEL` to use another model.
- The default `pytest` run and CI skip it.

## License

MIT
