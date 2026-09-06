param(
    [string]$StateDir = "$env:USERPROFILE\.codex\channels\codex-telegram"
)

$ErrorActionPreference = "Stop"
$PidFile = Join-Path $StateDir "windows\bridge.pid"

if (-not (Test-Path -LiteralPath $PidFile)) {
    Write-Output "codex-telegram is not running (no PID file)"
    exit 0
}

$BridgePid = [int](Get-Content -LiteralPath $PidFile -Raw)
$Process = Get-Process -Id $BridgePid -ErrorAction SilentlyContinue
if ($Process) {
    $Descendants = @()
    $PendingParents = @($BridgePid)
    while ($PendingParents.Count -gt 0) {
        $Children = @(
            Get-CimInstance Win32_Process |
                Where-Object { $_.ParentProcessId -in $PendingParents } |
                Select-Object -ExpandProperty ProcessId
        )
        if ($Children.Count -eq 0) {
            break
        }
        $Descendants += $Children
        $PendingParents = $Children
    }
    foreach ($ChildPid in ($Descendants | Select-Object -Unique | Sort-Object -Descending)) {
        Stop-Process -Id $ChildPid -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $BridgePid -ErrorAction SilentlyContinue
    Wait-Process -Id $BridgePid -Timeout 15 -ErrorAction SilentlyContinue
    Write-Output "codex-telegram stopped (PID $BridgePid)"
} else {
    Write-Output "codex-telegram was already stopped (stale PID $BridgePid)"
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
