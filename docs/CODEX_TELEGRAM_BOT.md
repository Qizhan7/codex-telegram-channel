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

Requirements:

- Python 3.12.
- Codex installed and signed in. The bridge can use `codex` from `PATH` or the
  binary bundled with the Codex/ChatGPT macOS app.
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and the
  owner's numeric Telegram user id.

From a fresh checkout, create a virtual environment and install the test-only
dependency:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install "pytest>=8"
```

Optionally install `tiktoken` (`.venv/bin/python -m pip install "tiktoken>=0.9"`)
so the shared-session content budget counts text with the exact `o200k_base`
tokenizer; without it the bridge uses a byte-based estimate.

Create the private config skeleton:

```bash
.venv/bin/python scripts/codex_telegram_bot.py init-config
```

Set the token and owner id in:

```text
~/.codex/channels/codex-telegram/.env
```

At minimum:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
```

Confirm `CODEX_TELEGRAM_CWD` and `CODEX_TELEGRAM_CODEX_BIN` in the generated
file if the repository or Codex binary is not in the detected location. Then
verify the bot and configuration:

```bash
.venv/bin/python scripts/codex_telegram_bot.py get-me
.venv/bin/python scripts/codex_telegram_bot.py doctor
.venv/bin/python -m py_compile scripts/codex_telegram_bot.py scripts/session_budget.py
.venv/bin/python -m pytest -q
```

Start the foreground service and send the bot a private message:

```bash
.venv/bin/python scripts/codex_telegram_bot.py serve
```

In another terminal, verify the chat and delivery. A private chat id is the
owner's Telegram user id:

```bash
.venv/bin/python scripts/codex_telegram_bot.py status --chat-id <telegram-chat-id>
.venv/bin/python scripts/codex_telegram_bot.py verify-channel \
  --chat-id <telegram-chat-id> --expect reply
```

The generated `.env` includes the following reference configuration:

```env
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_OWNER_IDS=<telegram-user-id>
CODEX_TELEGRAM_MODEL=gpt-5.5
CODEX_TELEGRAM_ALLOWED_MODELS=gpt-5.5,gpt-6-astra
CODEX_TELEGRAM_ENGINE=app-server
CODEX_TELEGRAM_EFFORT=high
CODEX_TELEGRAM_PRIVATE_EFFORT=high
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
CODEX_TELEGRAM_ROLLOVER_NEW_CONTENT_TOKENS=1000000
CODEX_TELEGRAM_BATCH_DELAY_SECONDS=2.5
CODEX_TELEGRAM_PRIVATE_BATCH_DELAY_SECONDS=2
CODEX_TELEGRAM_STREAM_PROGRESS=1
CODEX_TELEGRAM_PROGRESS_EDIT_INTERVAL_SECONDS=0.8
CODEX_TELEGRAM_READY_NOTICE=1
CODEX_TELEGRAM_MEDIA_GROUP_DELAY_SECONDS=1.5
CODEX_TELEGRAM_DENY_UNKNOWN=0
CODEX_TELEGRAM_IGNORE_USER_CONFIG=1
CODEX_TELEGRAM_MEMORY_DIR=
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

## Runtime Model And Effort Switching

`CODEX_TELEGRAM_MODEL`, `CODEX_TELEGRAM_EFFORT`, `CODEX_TELEGRAM_PRIVATE_EFFORT`,
and `CODEX_TELEGRAM_TASK_EFFORT` are the startup defaults. The owner can switch
the model and effort at runtime from Telegram:

- `/codex_model` shows the current model and the `CODEX_TELEGRAM_ALLOWED_MODELS`
  allowlist (when set); `/codex_model <model>` switches to that model. With no
  allowlist configured, any model id is accepted.
- `/codex_effort` shows the three effort scopes; `/codex_effort high` switches
  normal turns, and `/codex_effort private xhigh` or `/codex_effort task medium`
  adjust owner private chats or long tasks separately.

Both choices are stored in the bridge's sqlite `meta` table, take effect on the
next message without a restart, and survive bridge restarts. In-flight turns
keep the effort they started with.

## Group Chat Modes

The bridge always enforces chat access first: group messages only matter inside
`allowedChats`. The public build does not include a dashboard or control panel;
set each group from Telegram with:

```text
/codex_mode decide   # free mode: every allowed group message enters Codex
/codex_mode smart    # only matching wake words/watch phrases invoke Codex
/codex_mode mention  # traditional @/reply/name-only mode
```

Group turns are single-message by default. Use `/codex_batch batch` only when
you intentionally want a short window of messages merged into one Codex turn;
use `/codex_batch single` to return to immediate one-by-one handling.

`decide` forwards every allowed group message to Codex. The model then chooses
whether to send a visible Telegram reply or stay silent.

`smart` is edge-triggered. A message wakes the bot when it mentions the bot by
`@username`, replies to a bot message, contains a configured
`CODEX_TELEGRAM_WAKE_PHRASES` entry, or matches an item in the watch phrase
file. Only that matching message enters Codex; later ordinary messages remain
cached as shared context but do not invoke another turn unless they match on
their own.

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

Private messages sent within the configured 2-second quiet window are merged into one Codex turn, so short multi-message thoughts are read together. Group batching remains controlled per chat with `/codex_batch`.

Pulled Telegram updates are processed as chat-local batches, and each Codex
turn receives recent messages and relationship rows from that chat only. Shared
session mode still keeps one long-lived Codex thread, but turn prompts no longer
carry a mixed-chat recent-message block.

Each group turn can include a `<recent_chat_window>` block with the last five
same-chat messages before the trigger or batch that are not already in the
recent context, so Codex has the local conversation lead-in when deciding.
Photos and files attached to same-chat context messages are downloaded when a
turn includes those messages, so the prompt carries their local paths.

If a private turn fails because the Codex account is out of usage quota, the
bridge sends one clear quota message instead of a retry prompt. When quota
returns, `decide` groups do not backfill the messages that piled up during the
outage; private chats and `smart`/`mention` groups keep their normal history.

## Progress Messages

With `CODEX_TELEGRAM_STREAM_PROGRESS=1` (app-server engine), private turns and
explicitly addressed human group turns create a retained progress message after
the app-server emits its first visible `commentary` text. Hidden reasoning and
the `final_answer` are excluded, and the normal Telegram result remains a
separate reply. Failures that happen before real commentary, including an
exhausted usage quota, do not leave a generic progress template behind.

The bridge streams the active process through persistent Rich Message edits at
most every `CODEX_TELEGRAM_PROGRESS_EDIT_INTERVAL_SECONDS` and folds the
completed process into a closed `details` block that stays in the chat. If the
Rich Message endpoint fails or the process outgrows its rich-text limit, the
same turn falls back to retained plain-text edits and 4096-character overflow
messages. If the service restarts mid-turn, the existing progress message is
edited to say the turn was interrupted.

For `decide` and `smart` to receive ordinary group messages, disable Telegram
BotFather privacy mode for the bot or otherwise make sure the bot can read all
group messages.

## Prompt Contract

The public base prompt is neutral:

```text
You are a Codex collaborator reached through Telegram.
```

Telegram only sees messages sent with the channel tools: `reply`, `rich_reply`,
`send_photos`, `send_files`, `react`, `edit_message`, and
`edit_rich_message`. Normal final answers stay in the private Codex transcript
so Codex Desktop can show what happened. After visible tool calls, the model
mirrors a short `TG sent: ...` summary into the private transcript.

The prompt includes source-labeled current-chat Telegram context, current chat metadata,
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
private supervisor alarm. Running workers are rechecked by the bridge without
opening resident model turns. Refreshed worker state is also injected into later
resident turns, so routine routing uses that state directly instead of issuing
another status call or alarm. A clearly transient failure is retried once;
configuration, version, permission, and file-descriptor failures open the retry
circuit immediately.

The bridge records a completion-delivery policy when each worker starts. A
successful task with explicit read-only scope is sent as a deterministic terminal
summary, without waking the resident. Code/configuration/data changes, service or
external actions, failed checks, retries, ambiguous scope, failures,
`needs_input`, and legacy worker records enter the shared resident thread once for
review. Runtime file-change events override a read-only classification. The
terminal state and worker result are injected into that review turn, so the
resident does not need a separate worker-status call. If review emits no visible
message, the bridge still sends the deterministic terminal summary so the task
cannot disappear silently.

Worker prompts bound discovery: workers inspect exact paths supplied by the task
first, never recursively scan a whole home or app-state directory, cap each
read-only search at about 30 seconds, and report `unknown` with the checked
scope instead of widening the search.

`CODEX_TELEGRAM_AUTO_WORKER=0` is the default. The legacy auto-worker
supervision loop can still be enabled to migrate old `auto_delivery` records,
but it is not a text-triggered dispatch path. `CODEX_TELEGRAM_AUTO_WORKER_CHECK_SECONDS`
sets the first supervisor check delay for workers started from Telegram and for
legacy pending auto-delivery records.

### On-demand app/plugin workers

The resident and ordinary workers do not inherit the Desktop app/plugin set.
Ordinary `codex exec` workers use `--ignore-user-config`, enable the shared
Codex memory feature, and explicitly disable `apps`, `plugins`,
`remote_plugin`, and `hooks`.

An external app is available only through an optional, exact
`capability_profile` on `codex_worker_start`. The bridge accepts it only during
an owner-private turn. The profile name is validated against
`<state-dir>/capability-profiles.json`.

The file has a closed schema. Only a profile id, one already-installed plugin
id, and one app slug are accepted; paths and extra config keys are rejected.
Capability worker `cwd` is also confined to the bridge's configured workspace.
Start from [the example](../config/capability-profiles.example.json):

```json
{
  "version": 1,
  "profiles": {
    "gmail": {
      "plugin": "gmail@openai-curated",
      "app": "gmail"
    }
  }
}
```

For each accepted task, the bridge creates
`<state-dir>/capability-runs/<task-id>/` with a private Codex home and a local
marketplace containing exactly that one plugin. It derives the connector id
from the installed plugin manifest, disables all other apps by default, starts
a separate app-server with an ephemeral thread and no Telegram dynamic tools,
then terminates that app-server when the worker exits. The capability worker
cannot be resumed. Its private result always enters resident review with the
refreshed terminal state already attached.

This is a process and context boundary, not a dynamic edit of the resident's
global config. The Desktop plugin registry is read only to resolve an
allowlisted, installed, enabled local plugin. The bridge does not enable,
disable, or install anything in the Desktop Codex home. A task fails closed if
the plugin is absent, disabled, its manifest changes, the requested app is not
present, or an external write requires an approval the one-shot worker cannot
complete.

Codex's current app/plugin semantics are session-oriented: installed plugins
contribute skills/connectors to new chats, skill metadata can occupy some model
context before a skill is opened, and fuller skill instructions are loaded on
demand. Connector data is sent to the external service when its tool is
actually used. The current CLI exposes plugin install/list/remove, profiles,
feature flags, `--ignore-user-config`, and ephemeral execution, but no reliable
public per-turn `--plugin <id>` mount. This bridge therefore uses a separate
single-plugin Codex home and process instead of loading Desktop's full set into
the shared resident.

## Shared Desktop Thread

`CODEX_TELEGRAM_ENGINE=app-server` uses Codex's local app-server protocol.

With `CODEX_TELEGRAM_IGNORE_USER_CONFIG=1`, the app-server child uses a minimal
Codex home under `<state-dir>/codex-home`. Authentication and SQLite state remain
connected to the main Codex home, while user MCP servers, plugins, and apps stay
out of Telegram turns. Memories also stay out by default. Set
`CODEX_TELEGRAM_MEMORY_DIR` to one explicit local Codex `memories` directory to
enable memory use and generation through that shared store without loading the
rest of the user config. The bridge refuses to replace an existing non-link
`<state-dir>/codex-home/memories` path. This prevents each supervisor/thread
start from accumulating unrelated MCP child processes and file descriptors.

With `CODEX_TELEGRAM_SESSION_SCOPE=shared`, private chats and group chats use
one shared Codex session. Each turn's recent context comes from the current
chat only and stays source-labeled by chat id, chat type, title, sender, and
message id, so the model knows where each message came from while keeping one
continuous thread.

Set `CODEX_TELEGRAM_SESSION_SCOPE=per-chat` if each Telegram chat should use its
own Codex thread.

### Shared-session lifetime

The shared thread relies on Codex's native automatic compaction. The bridge
prepares a handover to a fresh thread only after the thread has accumulated
`CODEX_TELEGRAM_ROLLOVER_NEW_CONTENT_TOKENS` (default `1000000`) of newly
appended content; `0` disables the automatic handover. This replaces the old
`CODEX_TELEGRAM_ROLLOVER_INPUT_TOKENS` threshold, which is no longer read.

The counter is durable in `chats.sqlite`. It counts new user-input text and
textual tool results as `o200k_base` tokens (exact with `tiktoken` installed,
estimated otherwise) plus reported generated output tokens, including
reasoning. Binary images/audio, history replay, and compaction output are not
counted, so this is a stable content budget, not billed input or the live
context size. `/codex_status` shows the current counts.

Before switching, every earlier delivery must have a terminal result and the old
thread must be idle with no running background terminals. A read-only,
ephemeral fork of the old thread writes a source-labeled summary; the summary and
the candidate thread are saved before the shared mapping is switched in one
transaction. If preparation fails, the old thread stays active and the bridge
retries no sooner than five minutes later. `/codex_rollover` requests the same
handover before the next turn.

Inference errors (transport, account/model, remote compaction) keep the current
thread. Only a stored thread that can no longer be resumed is replaced, once;
the replacement id is recorded before inference so a failing first response
cannot keep creating new threads.

## Desktop Sync

`CODEX_TELEGRAM_DESKTOP_SYNC=1` updates Codex Desktop metadata for
Telegram-backed sessions:

- shared sessions are titled `Telegram Codex - All Chats`;
- per-chat sessions are titled `Telegram Codex - <chat title>`;
- previews show the current Telegram source label and message preview.

Native rollout transcripts belong to the app-server and are never rewritten by
the bridge, so Desktop shows each Telegram turn's prompt exactly as Codex
recorded it.

`CODEX_TELEGRAM_DESKTOP_OUTBOUND=1` tails the shared Codex Desktop rollout file
from a stored offset and mirrors new Desktop-authored user text back to the
current active Telegram chat. If the assistant answers that Desktop turn with a
normal visible final answer, that answer is also forwarded to the same Telegram
target once.

The outbound path skips historical content, Telegram `<channel>` inbound events,
`TG sent:` / `TG skipped:` mirrors, worker alarms, environment context, and
`(silent)` final answers. Telegram messages are still sent by the bot account,
not by a personal Telegram account.

## Rich Messages

`rich_reply` sends a formal, persistent Telegram Rich Message and returns its
`message_id` when current-chat delivery completes immediately.
`edit_rich_message` updates a bot-sent rich message in place. Supply exactly one
of `markdown`, `html`, or `blocks`; native blocks cover tables, mathematical
expressions, lists/checklists, quotations, and collapsible `details`. Use the
ordinary `reply` tool for source code and commands intended for copy/paste.

The bridge deliberately does not expose `sendRichMessageDraft`: drafts are
private-chat-only, expire after about 30 seconds, and do not satisfy the retained
per-turn process history contract.

Rich events use the same current-chat aliases, target allowlist, thread/reply
handling, system-prompt-echo guard, retry path, and `channel_deliveries` ledger
as ordinary replies.

## Media And Files

Incoming Telegram photos, documents, video, audio, voice, stickers, and albums
are stored under the private `incoming/` directory. Codex receives compact
metadata plus local paths when a turn needs the files. Transient network or
Telegram 5xx errors during `getFile` or the download itself are retried up to
three attempts with a short backoff; permanent errors are reported at once.

Outbound file tools accept local paths and `file://` URI objects. Use:

- `send_photos` for `.gif`, `.jpeg`, `.jpg`, `.png`, and `.webp`;
- `send_files` for documents, video, audio, voice, archives, and other files;
- `reply(files=[...])` when text and attachments should be sent together.

The tool surface accepts common aliases such as `files`, `file_paths`, `paths`,
`local_paths`, `uris`, `file_uris`, `photos`, `images`, `documents`, and
`attachments`. Remote HTTP URLs should be downloaded to local files first.

## Commands

The bridge registers its main commands with Telegram's command menu at startup.

- `/status`: show the Codex account usage windows, used/remaining percentage,
  and reset times (owner, private chat only; app-server engine).
- `/codex_status`: show bot state, policy, session, Desktop sync, content budget,
  and last run.
- `/codex_new`: start a fresh Codex session on the next message.
- `/codex_resume <session_id>`: bind the chat or shared context to a session.
- `/codex_rollover`: hand the shared session over to a fresh thread with a
  summary before the next turn (see Shared-session lifetime).
- `/codex_model [model]`: show the current model and optional allowlist, or
  switch the model. The choice is owner-only, applies from the next message,
  and survives bridge restarts.
- `/codex_effort [private|task] low|medium|high|xhigh`: show or switch the
  reasoning effort for normal turns, owner private chats, or long tasks.
- `/codex_mode decide|smart|mention`: set group trigger behavior.
- `/codex_batch single|batch|status`: switch group batching behavior.
- `/codex auto|single|multi|status`: switch visible reply bubble shape.
- `/codex_probe_channel`: run a reply-tool probe.
- `/codex_off` / `/codex_on`: disable or re-enable the chat.

## Run And Verify

Static checks:

```bash
python3.12 -m py_compile scripts/codex_telegram_bot.py scripts/session_budget.py
python3.12 -m pytest -q
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

After changing bridge code or capability profiles, restart the intended
instance from a Desktop/local terminal, not from a Telegram worker:

```bash
launchctl kickstart -k gui/$(id -u)/<launchd-label>
```

When the service starts and reaches Telegram, each owner receives one short
"bridge restarted" private message (at most once per minute, so restart loops
do not spam). Set `CODEX_TELEGRAM_READY_NOTICE=0` to turn this off. Private turns
cut off by the restart get an interruption notice, and a cut-off turn's progress
message is edited to say so.

Then confirm a new PID, inspect the service error log for startup errors, and
from the owner private chat request a read-only task that genuinely needs the
single allowlisted profile. Verify the resulting worker state names that
profile, has `one_shot: true`, and completes without any other plugin in its
lane. A group attempt with `capability_profile` must be rejected before a child
process starts.

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
