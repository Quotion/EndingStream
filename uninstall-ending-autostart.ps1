$ErrorActionPreference = "Stop"

$taskName = "Ending Backend"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed Windows autostart task: $taskName"
} else {
    Write-Host "Autostart task was not found: $taskName"
}
