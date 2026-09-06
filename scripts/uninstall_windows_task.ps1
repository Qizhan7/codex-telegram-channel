param(
    [string]$TaskName = "CodexTelegramChannel"
)

$ErrorActionPreference = "Stop"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task: $TaskName"
} else {
    Write-Output "Scheduled task is not installed: $TaskName"
}
