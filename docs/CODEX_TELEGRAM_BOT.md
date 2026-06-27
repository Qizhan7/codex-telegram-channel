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
CODEX_TELEGRAM_AUTO_WORKER=0
CODEX_TELEGRAM_AUTO_WORKER_CHECK_SECONDS=5
CODEX_TELEGRAM_AUTO_WORKER_RESULT_CHARS=3500
CODEX_TELEGRAM_CONTEXT_MESSAGES=24
CODEX_TELEGRAM_SHARED_CONTEXT_MESSAGES=8
CODEX_TELEGRAM_STEADY_CONTEXT_MESSAGES=0
CODEX_TELEGRAM_CONTEXT_TEXT_CHARS=800
CODEX_TELEGRAM_ROLLOVER_INPUT_TOKENS=200000
CODEX_TELEGRAM_BATCH_DELAY_SECONDS=2.5
CODEX_TELEGRAM_MEDIA_GROUP_DELAY_SECONDS=1.5
CODEX_TELEGRAM_DENY_UNKNOWN=0
CODEX_TELEGRAM_IGNORE_USER_CONFIG=1
CODEX_TELEGRAM_CHANNEL_TOOLS=1
CODEX_TELEGRAM_DESKTOP_SYNC=1
CODEX_TELEGRAM_DESKTOP_OUTBOUND=1
CODEX_TELEGRAM_CODEX_BIN=/Applications/Codex.app/Contents/Resources/codex
CODEX_TELEGRAM_WAKE_PHRASES=codex,assistant,bot
CODEX_TELEGRAM_IDENTITY_WAKE_PHRASES=codex,assistant,bot
CODEX_TELEGRAM_WATCH_PHRASES_PATH=~/.codex/channels/codex-telegram/watch_phrases.txt
```

Example access policy:

```json
{
  "dmPolicy": "allowlist",
  "groupPolicy": "decide",
  "allowedUsers": [],
  "allowedChats": []
}
```

Owner ids from `.env` are automatically allowed. Add group chat ids to
`allowedChats`. Group sender identity does not control the group mode: people,
bots, and anonymous group senders follow the same per-chat strategy.
Older access files may contain `botPolicy` or `allowedBots`; those fields are
read for compatibility but do not give bots a separate group-mode strategy.

## Group Chat Modes

The bridge always enforces chat access first: group messages only matter inside
`allowedChats`. The public build does not include a dashboard or control panel;
set each group from Telegram with:

```text
/codex_mode decide   # free mode: every allowed group message enters Codex
/codex_mode smart    # wake words/watch phrases open a 3-minute decide window
/codex_mode mention  # traditional @/reply/name-only mode
```

`decide` forwards every allowed group message to Codex. The model then chooses
whether to send a visible Telegram reply or stay silent.

`smart` is wake-based. A message wakes the bot when it mentions the bot by
`@username`, replies to a bot message, contains a configured
`CODEX_TELEGRAM_WAKE_PHRASES` entry, or matches an item in the watch phrase
file. Once awake, every message in that chat enters Codex for
`DEFAULT_WAKE_WINDOW_SECONDS` (three minutes). A visible bot reply extends the
window by another three minutes; three minutes without a bot reply closes it.

Wake phrases use plain consecutive-character matching. For example, configuring
`codex` means `codexbot` also wakes the bot. Waking only forwards the message to
Codex; it does not force a reply.

The optional watch phrase file is loaded from `CODEX_TELEGRAM_WATCH_PHRASES_PATH`.
Use one item per line and `|` for aliases:

```text
codex|assistant
project alpha|alpha
```

`mention` is the traditional mode. It forwards only `@` mentions, replies to
the bot, and identity-name calls. Identity-name calls use
`CODEX_TELEGRAM_IDENTITY_WAKE_PHRASES`; keep that list to names for the bot if
your `CODEX_TELEGRAM_WAKE_PHRASES` list includes topical words for `smart`.

Each group turn can include a `<recent_chat_window>` block with the last five
same-chat messages before the trigger or batch, so Codex has the local
conversation lead-in when deciding.

For `decide` and `smart` to receive ordinary group messages, disable Telegram
BotFather privacy mode for the bot or otherwise make sure the bot can read all
group messages.

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
the group `<recent_chat_window>` when applicable, attachment paths when files
are downloaded, and a compact instruction describing whether silence is
acceptable for the current turn.

## Direct Background Turns

`CODEX_TELEGRAM_DIRECT_BACKGROUND=1` lets the bridge keep a Codex turn running
after Telegram's visible wait window. Quick turns still finish inline. When a
single-message turn or batched group turn runs longer than
`CODEX_TELEGRAM_DIRECT_BACKGROUND_AFTER_SECONDS`, the bridge sends a short
task-specific acknowledgement, leaves the same Codex task running in the
background, and delivers the final channel-tool output back to the original
Telegram chat.

`CODEX_TELEGRAM_DIRECT_BACKGROUND_TIMEOUT_SECONDS` controls the longer timeout
used after a turn moves into that background path. This is separate from Codex
worker tools: the task stays in the Telegram-backed Codex thread instead of
opening a separate worker session.

## Worker Tools And Supervision

Telegram messages enter the resident Codex thread first. The bridge no longer
starts workers from keyword or text-pattern matches. The resident decides from
the conversation whether a separate worker would help, asks the owner for
confirmation in natural wording, and only calls `codex_worker_start` after the
owner confirms that route.

Workers run with Telegram channel tools disabled. They only write private worker
output; Telegram messages always come from the Telegram resident through the
normal channel tools. When the resident starts a worker, the bridge schedules a
private supervisor alarm. At each alarm, the resident inspects the worker with
`codex_worker_status`, continues the same worker session when needed, sets
another alarm if it is still running, and decides whether a visible Telegram
reply helps.

`CODEX_TELEGRAM_AUTO_WORKER=0` is the default. The legacy auto-worker
supervision loop can still be enabled to migrate old `auto_delivery` records,
but it is not a text-triggered dispatch path. `CODEX_TELEGRAM_AUTO_WORKER_CHECK_SECONDS`
sets the first supervisor check delay for workers started from Telegram and for
legacy pending auto-delivery records.

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
- `/codex_mode decide|smart|mention`: set group trigger behavior.
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
