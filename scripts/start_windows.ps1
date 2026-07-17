param(
    [string]$StateDir = "$env:USERPROFILE\.codex\channels\codex-telegram",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Bridge = Join-Path $ProjectDir "scripts\codex_telegram_bot.py"
$RuntimeDir = Join-Path $StateDir "windows"
$PidFile = Join-Path $RuntimeDir "bridge.pid"
$StdoutLog = Join-Path $RuntimeDir "bridge.stdout.log"
$StderrLog = Join-Path $RuntimeDir "bridge.stderr.log"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing virtual environment: $Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $StateDir ".env"))) {
    throw "Missing runtime config: $(Join-Path $StateDir '.env')"
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

if (Test-Path -LiteralPath $PidFile) {
    $ExistingPid = [int](Get-Content -LiteralPath $PidFile -Raw)
    $Existing = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
    if ($Existing) {
        Write-Output "codex-telegram already running (PID $ExistingPid)"
        exit 0
    }
    Remove-Item -LiteralPath $PidFile -Force
}

$Arguments = @("`"$Bridge`"", "--state-dir", "`"$StateDir`"", "serve")
if ($Foreground) {
    & $Python $Bridge "--state-dir" $StateDir "serve"
    exit $LASTEXITCODE
}

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $Process.Id -Encoding ascii
Write-Output "codex-telegram started (PID $($Process.Id))"
Write-Output "stdout: $StdoutLog"
Write-Output "stderr: $StderrLog"
