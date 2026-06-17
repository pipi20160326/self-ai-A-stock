$ErrorActionPreference = "Stop"
$taskName = "AStockTrendDailyReport"
$project = Resolve-Path "."
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "-m src.daily_job" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At 16:00
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task created: $taskName at 16:00 daily"
