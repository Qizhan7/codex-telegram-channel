# Codex Telegram Channel

Standalone Telegram bridge for talking to Codex from Telegram.

The bridge keeps runtime state under `~/.codex/channels/codex-telegram/` and
uses its own Bot API token, sqlite database, logs, and LaunchAgent label. It is
designed for an owner-controlled bot: keep real tokens and runtime state out of
the repo.

## Features

- Telegram text, persistent Rich Messages, photos, files, albums, reactions, and edited messages.
- Codex app-server backend with `CODEX_TELEGRAM_ENGINE=app-server`.
- One merged Codex Desktop thread across Telegram chats with
  `CODEX_TELEGRAM_SESSION_SCOPE=shared`.
- Bidirectional Desktop sync:
  - `CODEX_TELEGRAM_DESKTOP_SYNC=1` renames and previews the Telegram-backed
    Codex thread in Desktop.
  - `CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` mirrors new user text typed into that
    shared Desktop thread back to the current active Telegram chat.
- Visible Telegram tools: `reply`, `rich_reply`, `send_photos`, `send_files`,
  `react`, `edit_message`, and `edit_rich_message`.
- Retained per-turn progress messages that stream the model's visible
  commentary and fold into a collapsed summary when the turn ends.
- Three group chat modes:
  - `decide`: every allowed group message enters Codex; the model decides
    whether a visible reply helps.
  - `smart`: only messages that match wake words, watch phrases, `@` mentions,
    or replies to the bot enter Codex.
  - `mention`: traditional mention mode; only `@`, replies to the bot, or
    identity-name calls enter Codex.
- Direct background continuation for long turns: quick tasks finish inline; if a
  single-message or batched group Codex turn runs past
  `CODEX_TELEGRAM_DIRECT_BACKGROUND_AFTER_SECONDS`, the bridge sends a short,
  task-specific acknowledgement, keeps the same Codex task running, and delivers
  the final result back to the original Telegram chat with the normal channel
  tools.
- Worker supervision without text-triggered dispatch: Telegram messages enter the
  resident Codex thread first. If the resident judges that a separate worker
  would help, it asks for owner confirmation naturally; only after confirmation
  does it start a worker. Running checks are bridge-only, transient failures get
  at most one retry, and hard failures open a circuit. Successful read-only work
  gets one deterministic closure summary without another resident model turn;
  writes, retries, failures, uncertainty, owner choices, and external-app work
  enter the resident once for review.
- Public, neutral base prompt: the model is a generic Codex collaborator reached
  through Telegram, with no private persona dependency.

## Layout

```text
scripts/codex_telegram_bot.py      # service, CLI, app-server bridge
scripts/session_budget.py          # shared-session content budget counter
tests/                             # unit tests
docs/CODEX_TELEGRAM_BOT.md         # setup and operations guide
launchd/com.codex.telegram.plist   # macOS LaunchAgent template
config/*.example                   # safe config examples
```

## Quick Start

### 1. Prepare Codex, Python, and Telegram

You need:

- Python 3.12.
- Codex installed and signed in. The bridge can use `codex` from `PATH` or the
  binary bundled with the Codex/ChatGPT macOS app.
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your
  numeric Telegram user id.

Clone the repository and create an isolated Python environment:

```bash
git clone https://github.com/Qizhan7/codex-telegram-channel.git
cd codex-telegram-channel
python3.12 -m venv .venv
.venv/bin/python -m pip install "pytest>=8"
```

The app-server path has no required third-party runtime dependency; `pytest` is
only for the included verification suite. Optionally install `tiktoken`
(`.venv/bin/python -m pip install "tiktoken>=0.9"`) for exact token counts in
the shared-session content budget; without it the bridge uses an estimate.

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python` and
see [WINDOWS.md](WINDOWS.md) for background service scripts (`start_windows.ps1`,
`stop_windows.ps1`, and the logon task installer). `init-config` detects the
npm-installed Codex wrapper (`%APPDATA%\npm\codex.cmd`) automatically; the bare
`codex` alias under WindowsApps cannot be launched from scripts.

### 2. Create the private runtime config

```bash
.venv/bin/python scripts/codex_telegram_bot.py init-config
```

This creates private state outside the repository. Edit:

```text
~/.codex/channels/codex-telegram/.env
~/.codex/channels/codex-telegram/access.json
```

Set the two required values in `.env`:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
```

`init-config` fills in the current repository path and a detected Codex binary.
If the repository moves or Codex is installed elsewhere, update
`CODEX_TELEGRAM_CWD` or `CODEX_TELEGRAM_CODEX_BIN` in `.env`.

Owner ids are automatically allowed in private chat. For a group, add its
numeric chat id to `allowedChats` in `access.json` before starting the bridge.

### 3. Verify the setup

Confirm the token reaches the expected bot, inspect configuration health, and
run the included checks:

```bash
.venv/bin/python scripts/codex_telegram_bot.py get-me
.venv/bin/python scripts/codex_telegram_bot.py doctor
.venv/bin/python -m py_compile scripts/codex_telegram_bot.py scripts/session_budget.py
.venv/bin/python -m pytest -q
```

Fix any `doctor` failure before continuing.

### 4. Start the bridge and send a private message

```bash
.venv/bin/python scripts/codex_telegram_bot.py serve
```

Send the bot a private Telegram message. In another terminal, verify the stored
chat and successful delivery; for a private chat, the chat id is your Telegram
user id:

```bash
.venv/bin/python scripts/codex_telegram_bot.py status --chat-id <telegram-chat-id>
.venv/bin/python scripts/codex_telegram_bot.py verify-channel \
  --chat-id <telegram-chat-id> --expect reply
```

For a one-shot polling diagnostic instead of the resident service, use
`poll-once`. Once the foreground flow works, install the LaunchAgent below.

## Launchd

Copy `launchd/com.codex.telegram.plist`, replace `/path/to/codex-telegram-channel`
and `/Users/YOUR_USER`, then load it:

```bash
mkdir -p ~/.codex/channels/codex-telegram/logs
launchctl bootstrap gui/$(id -u) /path/to/codex-telegram-channel/launchd/com.codex.telegram.plist
launchctl enable gui/$(id -u)/com.codex.telegram
launchctl kickstart -k gui/$(id -u)/com.codex.telegram
```

## Commands

The main commands are registered with Telegram's command menu at startup.

- `/status`: show Codex account usage limits (owner, private chat).
- `/codex_status`: show bot state.
- `/codex_new`: start a fresh Codex session on the next message.
- `/codex_resume <session_id>`: bind to a Codex session.
- `/codex_rollover`: hand the shared session over to a fresh thread with a
  summary before the next turn.
- `/codex_model [model]`: show or switch the Codex model (owner).
- `/codex_effort [private|task] low|medium|high|xhigh`: show or switch
  reasoning effort (owner).
- `/codex_mode decide|smart|mention`: set group trigger mode.
- `/codex_batch single|batch|status`: set group batching.
- `/codex auto|single|multi|status`: set visible reply bubble shape.
- `/codex_off` / `/codex_on`: disable or re-enable the chat.

## Group Modes

The public build has no dashboard or control panel. Add a group id to
`allowedChats`, then use `/codex_mode` in that Telegram group:

```text
/codex_mode decide   # free mode: pass every allowed group message to Codex
/codex_mode smart    # only matching wake words/watch phrases invoke Codex
/codex_mode mention  # traditional @/reply/name-only mode
```

Group turns are single-message by default. Use `/codex_batch batch` only when
you intentionally want a short window of messages merged into one Codex turn;
use `/codex_batch single` to return to immediate one-by-one handling.

Private messages use a separate 2-second quiet window by default
(`CODEX_TELEGRAM_PRIVATE_BATCH_DELAY_SECONDS=2`). Messages sent together are
passed to Codex as one ordered batch; group behavior is unchanged. Processing
batches and explicit recent-message context never mix Telegram chats.

App-server mode also creates one new retained progress message per private turn
and per explicitly addressed human group turn. It edits that message from
user-visible `commentary` deltas, keeps hidden reasoning and the final answer
out, then leaves the completed process as a collapsed Rich Message in the chat.
If Rich Messages are unavailable, it falls back to retained plain-text edits.
Disable it with `CODEX_TELEGRAM_STREAM_PROGRESS=0`.

`smart` uses `CODEX_TELEGRAM_WAKE_PHRASES` and the optional
`CODEX_TELEGRAM_WATCH_PHRASES_PATH` file. Wake phrases are simple consecutive
substring matches: if `codex` is configured, `codexbot` also wakes the bot and
lets Codex decide whether to reply. A watch phrase file contains one item per
line; use `|` to separate aliases:

```text
codex|assistant
project alpha|alpha
```

`mention` uses `@`, replies to bot messages, and
`CODEX_TELEGRAM_IDENTITY_WAKE_PHRASES`. Keep identity phrases to names for the
bot, especially if `CODEX_TELEGRAM_WAKE_PHRASES` includes topical words for
`smart`.

For `decide` and `smart` to receive ordinary group messages, disable Telegram
BotFather privacy mode for the bot or make sure your bot can read all group
messages.

## Notes

- Keep real tokens out of this repo.
- `CODEX_TELEGRAM_ENGINE=app-server` is the recommended path.
- With `CODEX_TELEGRAM_IGNORE_USER_CONFIG=1`, Telegram app-server turns use a
  minimal state-local Codex home, keeping unrelated user MCP/plugin processes out
  of the resident thread while preserving shared SQLite/Desktop metadata.
- `CODEX_TELEGRAM_SESSION_SCOPE=shared`,
  `CODEX_TELEGRAM_DESKTOP_SYNC=1`, and
  `CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` are enabled in the example config so the
  public build includes the merged Desktop thread and bidirectional sync path.
- Channel tools default omitted `chat_id` to the current chat. They also accept
  numeric Telegram ids, `current` / `here` / `this`, and `owner` /
  `owner_private` / `dm` when exactly one owner is configured.
- `mcp-channel` is only needed for the older `exec` channel-tool path and
  requires the optional `mcp` Python package.
- After each start the bridge sends the owner one short "restarted" private
  message (at most once per minute); set `CODEX_TELEGRAM_READY_NOTICE=0` to
  turn it off.

### Shared-session lifetime

`CODEX_TELEGRAM_ROLLOVER_NEW_CONTENT_TOKENS=1000000` replaces the old
`CODEX_TELEGRAM_ROLLOVER_INPUT_TOKENS` threshold. The shared thread keeps using
Codex's native automatic compaction, and the bridge prepares a handover to a
fresh thread only after this much newly appended content (new user-input text,
textual tool results, and generated output; not history replay or images) has
accumulated. The count is durable in `chats.sqlite`; `0` disables it.

A handover waits until earlier deliveries have finished and the old thread is
idle, then a read-only ephemeral fork writes a source-labeled summary that the
new thread starts from. Failed preparation keeps the old thread and retries no
sooner than five minutes later; `/codex_rollover` requests the same path.
Inference errors keep the current thread; only a stored thread that can no
longer be resumed is replaced, once. Native Codex transcript files are never
rewritten by the bridge. See [the operations guide](docs/CODEX_TELEGRAM_BOT.md)
for details.
