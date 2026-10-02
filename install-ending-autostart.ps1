$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSCommandPath
$launcher = Join-Path $projectDir "start-ending-hidden.vbs"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"
$taskName = "Ending Backend"

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Missing launcher: $launcher"
}

$action = New-ScheduledTaskAction `
    -Execute $wscript `
    -Argument "//B //Nologo `"$launcher`"" `
    -WorkingDirectory $projectDir

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Starts the local Ending backend without a console window." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host "Installed Windows autostart task: $taskName"
Write-Host "Ending should answer at http://127.0.0.1:8080/api/health"
