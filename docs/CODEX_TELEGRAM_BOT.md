# Codex Telegram Bot

This is a standalone, owner-controlled Telegram bridge for Codex. It uses its
own Telegram bot token and keeps runtime state outside the repository.

## Runtime State

```text
~/.codex/channels/codex-telegram/
  .env
  access.json
  chats.sqlite
  logs/
  out/
  incoming/
```

## First-Time Setup

Create the private config skeleton:

```bash
python3.12 scripts/codex_telegram_bot.py init-config
```

Create a Telegram bot with BotFather, then set the token and owner id in:

```text
~/.codex/channels/codex-telegram/.env
```

Example:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
CODEX_TELEGRAM_MODEL=gpt-5.5
CODEX_TELEGRAM_ENGINE=app-server
CODEX_TELEGRAM_EFFORT=high
CODEX_TELEGRAM_TASK_EFFORT=xhigh
CODEX_TELEGRAM_SESSION_SCOPE=shared
CODEX_TELEGRAM_CWD=/path/to/codex-telegram-channel
CODEX_TELEGRAM_SANDBOX=danger-full-access
CODEX_TELEGRAM_APPROVAL=never
CODEX_TELEGRAM_BYPASS_PERMISSIONS=1
CODEX_TELEGRAM_REPLY_TIMEOUT_SECONDS=300
CODEX_TELEGRAM_DIRECT_BACKGROUND=1
CODEX_TELEGRAM_DIRECT_BACKGROUND_AFTER_SECONDS=20
CODEX_TELEGRAM_DIRECT_BACKGROUND_TIMEOUT_SECONDS=3600
CODEX_TELEGRAM_CONTEXT_MESSAGES=24
CODEX_TELEGRAM_SHARED_CONTEXT_MESSAGES=8
CODEX_TELEGRAM_STEADY_CONTEXT_MESSAGES=0
CODEX_TELEGRAM_CONTEXT_TEXT_CHARS=800
CODEX_TELEGRAM_ROLLOVER_INPUT_TOKENS=80000
CODEX_TELEGRAM_BATCH_DELAY_SECONDS=2.5
CODEX_TELEGRAM_MEDIA_GROUP_DELAY_SECONDS=1.5
CODEX_TELEGRAM_GROUP_DECISION_SOURCE=bridge
CODEX_TELEGRAM_DENY_UNKNOWN=0
CODEX_TELEGRAM_IGNORE_USER_CONFIG=1
CODEX_TELEGRAM_CHANNEL_TOOLS=1
CODEX_TELEGRAM_DESKTOP_SYNC=1
CODEX_TELEGRAM_DESKTOP_OUTBOUND=1
CODEX_TELEGRAM_CODEX_BIN=/Applications/Codex.app/Contents/Resources/codex
CODEX_TELEGRAM_WAKE_PHRASES=codex,assistant,bot
```

Example access policy:

```json
{
  "dmPolicy": "allowlist",
  "groupPolicy": "decide",
  "botPolicy": "ai-decide",
  "allowedUsers": [],
  "allowedChats": [],
  "allowedBots": []
}
```

Owner ids from `.env` are automatically allowed. Add group chat ids to
`allowedChats`, and trusted bot ids to `allowedBots` if bot-to-bot context is
needed.

## Prompt Contract

The public base prompt is neutral:

```text
You are a Codex collaborator reached through Telegram.
```

Telegram only sees messages sent with the channel tools: `reply`,
`send_photos`, `send_files`, `react`, and `edit_message`. Normal final answers
stay in the private Codex transcript so Codex Desktop can show what happened.
After visible tool calls, the model mirrors a short `TG sent: ...` summary into
the private transcript.

The prompt includes source-labeled Telegram context, current chat metadata,
attachment paths when files are downloaded, and a compact instruction describing
whether silence is acceptable for the current turn.

## Direct Background Turns

`CODEX_TELEGRAM_DIRECT_BACKGROUND=1` lets the bridge keep a Codex turn running
after Telegram's visible wait window. Quick turns still finish inline. When a
single-message turn or batched group turn runs longer than
`CODEX_TELEGRAM_DIRECT_BACKGROUND_AFTER_SECONDS`, the bridge sends a short
acknowledgement, leaves the same Codex task running in the background, and
delivers the final channel-tool output back to the original Telegram chat.

`CODEX_TELEGRAM_DIRECT_BACKGROUND_TIMEOUT_SECONDS` controls the longer timeout
used after a turn moves into that background path. This is separate from Codex
worker tools: the task stays in the Telegram-backed Codex thread instead of
opening a separate worker session.

## Shared Desktop Thread

`CODEX_TELEGRAM_ENGINE=app-server` uses Codex's local app-server protocol.

With `CODEX_TELEGRAM_SESSION_SCOPE=shared`, private chats and group chats use
one shared Codex session. Recent context remains source-labeled by chat id, chat
type, title, sender, and message id, so the model can distinguish where each
message came from while keeping one continuous thread.

Set `CODEX_TELEGRAM_SESSION_SCOPE=per-chat` if each Telegram chat should use its
own Codex thread.

## Desktop Sync

`CODEX_TELEGRAM_DESKTOP_SYNC=1` updates Codex Desktop metadata for
Telegram-backed sessions:

- shared sessions are titled `Telegram Codex - All Chats`;
- per-chat sessions are titled `Telegram Codex - <chat title>`;
- previews show the current Telegram source label and message preview.

`CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` tails the shared Codex Desktop rollout file
from a stored offset and mirrors new Desktop-authored user text back to the
current active Telegram chat. If the assistant answers that Desktop turn with a
normal visible final answer, that answer is also forwarded to the same Telegram
target once.

The outbound path skips historical content, Telegram `<channel>` inbound events,
`TG sent:` / `TG skipped:` mirrors, worker alarms, environment context, and
`(silent)` final answers. Telegram messages are still sent by the bot account,
not by a personal Telegram account.

## Media And Files

Incoming Telegram photos, documents, video, audio, voice, stickers, and albums
are stored under the private `incoming/` directory. Codex receives compact
metadata plus local paths when a turn needs the files.

Outbound file tools accept local paths and `file://` URI objects. Use:

- `send_photos` for `.gif`, `.jpeg`, `.jpg`, `.png`, and `.webp`;
- `send_files` for documents, video, audio, voice, archives, and other files;
- `reply(files=[...])` when text and attachments should be sent together.

The tool surface accepts common aliases such as `files`, `file_paths`, `paths`,
`local_paths`, `uris`, `file_uris`, `photos`, `images`, `documents`, and
`attachments`. Remote HTTP URLs should be downloaded to local files first.

## Commands

- `/codex_status`: show bot state, policy, session, Desktop sync, and last run.
- `/codex_new`: start a fresh Codex session on the next message.
- `/codex_resume <session_id>`: bind the chat or shared context to a session.
- `/codex_rollover`: start a clean shared session with a bounded handoff.
- `/codex_mode mention|all|decide`: set group trigger behavior.
- `/codex_batch single|batch|status`: switch group batching behavior.
- `/codex auto|single|multi|status`: switch visible reply bubble shape.
- `/codex_debug on|off|status`: show or hide raw Desktop prompts.
- `/codex_probe_channel`: run a reply-tool probe.
- `/codex_off` / `/codex_on`: disable or re-enable the chat.

## Run And Verify

Static checks:

```bash
python3.12 -m py_compile scripts/codex_telegram_bot.py
python3.12 -m pytest -q tests/test_codex_telegram_bot.py
```

Bot API identity:

```bash
python3.12 scripts/codex_telegram_bot.py get-me
```

Status:

```bash
python3.12 scripts/codex_telegram_bot.py status
python3.12 scripts/codex_telegram_bot.py doctor --chat-id <telegram-chat-id>
```

Run one poll pass:

```bash
python3.12 scripts/codex_telegram_bot.py poll-once
```

Run foreground service:

```bash
python3.12 scripts/codex_telegram_bot.py serve
```

## Launchd

Edit `launchd/com.codex.telegram.plist` and replace:

- `/path/to/codex-telegram-channel`
- `/Users/YOUR_USER`

Then load:

```bash
mkdir -p ~/.codex/channels/codex-telegram/logs
launchctl bootstrap gui/$(id -u) /path/to/codex-telegram-channel/launchd/com.codex.telegram.plist
launchctl enable gui/$(id -u)/com.codex.telegram
launchctl kickstart -k gui/$(id -u)/com.codex.telegram
```

Logs:

```bash
tail -f ~/.codex/channels/codex-telegram/logs/service.out.log
tail -f ~/.codex/channels/codex-telegram/logs/service.err.log
```

Stop:

```bash
launchctl bootout gui/$(id -u) /path/to/codex-telegram-channel/launchd/com.codex.telegram.plist
```

## Safety Notes

- Use a dedicated Telegram bot token for this bridge.
- Keep `.env`, sqlite databases, logs, downloads, and generated output out of
  the repository.
- `CODEX_TELEGRAM_BYPASS_PERMISSIONS=1` grants broad local execution authority;
  use it only for an owner-controlled bot.
- Telegram Bot API polling is single-consumer per token. Do not run two pollers
  for the same bot token.
