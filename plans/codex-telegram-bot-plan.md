# Codex Telegram Bot Plan

## Goal

Build a standalone Telegram bot that lets an owner talk to Codex from Telegram
without sharing tokens, runtime state, logs, or pollers with any other Telegram
integration.

The public version must include:

- a neutral, non-personal prompt;
- one shared Codex Desktop thread for Telegram when configured;
- Desktop metadata sync;
- Desktop-to-Telegram outbound mirroring for the shared thread.

## Architecture

```text
Telegram Bot API
-> local codex_telegram_bot service
-> sqlite state store
-> Codex app-server or codex exec
-> Telegram channel tools
```

Use `CODEX_TELEGRAM_ENGINE=app-server` for the current recommended path. Keep
`codex exec` support as a fallback backend.

## State Layout

Runtime state lives outside the repository:

```text
~/.codex/channels/codex-telegram/
  .env
  access.json
  chats.sqlite
  logs/
  out/
  incoming/
```

Example `.env`:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
CODEX_TELEGRAM_MODEL=gpt-5.5
CODEX_TELEGRAM_ENGINE=app-server
CODEX_TELEGRAM_SESSION_SCOPE=shared
CODEX_TELEGRAM_CWD=/path/to/codex-telegram-channel
CODEX_TELEGRAM_DESKTOP_SYNC=1
CODEX_TELEGRAM_DESKTOP_OUTBOUND=1
CODEX_TELEGRAM_WAKE_PHRASES=codex,assistant,bot
```

## Prompt Contract

The base prompt should be understandable without private context:

```text
You are a Codex collaborator reached through Telegram.
```

The prompt should explain:

- Telegram only sees messages sent via channel tools.
- Normal final answers remain private transcript output.
- `reply`, `send_photos`, `send_files`, `react`, and `edit_message` are the
  visible Telegram tools.
- Source labels distinguish private chats, groups, senders, and threads.
- Silence is valid when no visible Telegram response helps.

## Commands

Use the public command namespace:

- `/codex_status`
- `/codex_new`
- `/codex_resume <session_id>`
- `/codex_rollover`
- `/codex_mode mention|all|decide`
- `/codex_batch single|batch|status`
- `/codex auto|single|multi|status`
- `/codex_debug on|off|status`
- `/codex_off`
- `/codex_on`

Legacy private command aliases may remain accepted for backward compatibility,
but public docs should lead with `/codex`.

## Desktop Requirements

`CODEX_TELEGRAM_SESSION_SCOPE=shared` should bind Telegram traffic to one Codex
session. `CODEX_TELEGRAM_DESKTOP_SYNC=1` should keep Desktop metadata readable:

- shared title: `Telegram Codex - All Chats`;
- per-chat title: `Telegram Codex - <chat title>`;
- preview: source-labeled Telegram message preview.

`CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` should tail the shared Desktop rollout file
from a stored offset and forward new Desktop-authored user text to the current
active Telegram chat. The assistant's normal Desktop final answer from the same
turn may be forwarded once to that same Telegram target.

Skip historical rollout content, Telegram inbound channel events, `TG sent:` /
`TG skipped:` mirrors, worker alarms, environment context, and silent answers.

## Verification

```bash
python3.12 -m py_compile scripts/codex_telegram_bot.py
python3.12 -m pytest -q tests/test_codex_telegram_bot.py
```

Runtime checks:

- `scripts/codex_telegram_bot.py get-me`
- `scripts/codex_telegram_bot.py status`
- `scripts/codex_telegram_bot.py doctor --chat-id <chat-id>`
- `scripts/codex_telegram_bot.py poll-once`

## Safety Notes

- Never commit real Telegram bot tokens or runtime sqlite/log/download files.
- Use a dedicated Bot API token.
- Do not run two independent pollers for the same token.
- Keep LaunchAgent paths templated for public release.
