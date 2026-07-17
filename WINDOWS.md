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

## Rollback

Only one process may long-poll a Telegram Bot token. Stop this bridge before
starting the previous Node bridge:

```powershell
.\scripts\stop_windows.ps1
Set-Location "C:\Users\yx\Documents\Codex\2026-04-21-gemini-cli-telegram"
.\start-telegram-codex-bridge.cmd
```
