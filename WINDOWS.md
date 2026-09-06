# Windows operation

This checkout keeps runtime state and secrets outside the repository at:

```text
%USERPROFILE%\.codex\channels\codex-telegram
```

Start the bridge in the background:

```powershell
.\scripts\start_windows.ps1
```

Stop it:

```powershell
.\scripts\stop_windows.ps1
```

Install automatic startup at Windows logon:

```powershell
.\scripts\install_windows_task.ps1
```

Run diagnostics:

```powershell
.\.venv\Scripts\python.exe .\scripts\codex_telegram_bot.py doctor
.\.venv\Scripts\python.exe .\scripts\codex_telegram_bot.py status
```

The Windows adapter refreshes a private copy of `auth.json` when an isolated
app-server starts because ordinary Windows processes cannot create symbolic
links unless Developer Mode or elevation is enabled.

## Persona and memory

Private runtime knowledge lives outside Git and is **opt-in**: set
`CODEX_TELEGRAM_KNOWLEDGE=1` in `.env` or the files below stay unloaded.

```text
%USERPROFILE%\.codex\channels\codex-telegram\knowledge\
├── CODEX_PERSONA.md
├── MEMORY_SHARED.md
└── MEMORY_PRIVATE.md
```

The full persona is attached to app-server base instructions. A persona hash
change starts a fresh thread for each chat the next time that chat is active.
Memory is re-read on every turn: groups receive only `MEMORY_SHARED.md`; the
configured owner's private chat receives shared plus private memory.

Use `status` or `doctor` to verify loaded paths, character counts, SHA-256
digests, and whether a diagnostic prompt contains private memory.

## Rollback

Only one process may long-poll a Telegram Bot token. Stop this bridge before
starting the previous Node bridge:

```powershell
.\scripts\stop_windows.ps1
Set-Location "C:\Users\yx\Documents\Codex\2026-04-21-gemini-cli-telegram"
.\start-telegram-codex-bridge.cmd
```
