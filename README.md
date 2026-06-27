# Codex Telegram Channel

Standalone Telegram bridge for talking to Codex from Telegram.

The bridge keeps runtime state under `~/.codex/channels/codex-telegram/` and
uses its own Bot API token, sqlite database, logs, and LaunchAgent label. It is
designed for an owner-controlled bot: keep real tokens and runtime state out of
the repo.

## Features

- Telegram text, photos, files, albums, reactions, and edited messages.
- Codex app-server backend with `CODEX_TELEGRAM_ENGINE=app-server`.
- One merged Codex Desktop thread across Telegram chats with
  `CODEX_TELEGRAM_SESSION_SCOPE=shared`.
- Bidirectional Desktop sync:
  - `CODEX_TELEGRAM_DESKTOP_SYNC=1` renames and previews the Telegram-backed
    Codex thread in Desktop.
  - `CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` mirrors new user text typed into that
    shared Desktop thread back to the current active Telegram chat.
- Visible Telegram tools: `reply`, `send_photos`, `send_files`, `react`, and
  `edit_message`.
- Three group chat modes:
  - `decide`: every allowed group message enters Codex; the model decides
    whether a visible reply helps.
  - `smart`: wake words, watch phrases, `@` mentions, or replies wake the bot;
    after wake, every group message enters Codex for three minutes. A visible
    bot reply extends the window.
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
  does it start a worker and supervise it with private checks.
- Public, neutral base prompt: the model is a generic Codex collaborator reached
  through Telegram, with no private persona dependency.

## Layout

```text
scripts/codex_telegram_bot.py      # service, CLI, app-server bridge
tests/test_codex_telegram_bot.py   # unit tests
docs/CODEX_TELEGRAM_BOT.md         # setup and operations guide
launchd/com.codex.telegram.plist   # macOS LaunchAgent template
config/*.example                   # safe config examples
```

## Quick Start

Create the private runtime config:

```bash
python3.12 scripts/codex_telegram_bot.py init-config
```

Then edit:

```text
~/.codex/channels/codex-telegram/.env
~/.codex/channels/codex-telegram/access.json
```

At minimum, set:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
CODEX_TELEGRAM_CWD=/path/to/codex-telegram-channel
```

Run checks:

```bash
python3.12 -m py_compile scripts/codex_telegram_bot.py
python3.12 -m pytest -q tests/test_codex_telegram_bot.py
```

Run one poll pass:

```bash
python3.12 scripts/codex_telegram_bot.py poll-once
```

Run the service in the foreground:

```bash
python3.12 scripts/codex_telegram_bot.py serve
```

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

- `/codex_status`: show bot state.
- `/codex_new`: start a fresh Codex session on the next message.
- `/codex_resume <session_id>`: bind to a Codex session.
- `/codex_rollover`: start a clean shared session with a short handoff.
- `/codex_mode decide|smart|mention`: set group trigger mode.
- `/codex_batch single|batch|status`: set group batching.
- `/codex auto|single|multi|status`: set visible reply bubble shape.
- `/codex_debug on|off|status`: show or hide raw Desktop prompts.
- `/codex_off` / `/codex_on`: disable or re-enable the chat.

## Group Modes

The public build has no dashboard or control panel. Add a group id to
`allowedChats`, then use `/codex_mode` in that Telegram group:

```text
/codex_mode decide   # free mode: pass every allowed group message to Codex
/codex_mode smart    # wake words/watch phrases open a 3-minute decide window
/codex_mode mention  # traditional @/reply/name-only mode
```

Group turns are single-message by default. Use `/codex_batch batch` only when
you intentionally want a short window of messages merged into one Codex turn;
use `/codex_batch single` to return to immediate one-by-one handling.

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
- `CODEX_TELEGRAM_SESSION_SCOPE=shared`,
  `CODEX_TELEGRAM_DESKTOP_SYNC=1`, and
  `CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` are enabled in the example config so the
  public build includes the merged Desktop thread and bidirectional sync path.
- Channel tools default omitted `chat_id` to the current chat. They also accept
  numeric Telegram ids, `current` / `here` / `this`, and `owner` /
  `owner_private` / `dm` when exactly one owner is configured.
- `mcp-channel` is only needed for the older `exec` channel-tool path and
  requires the optional `mcp` Python package.
