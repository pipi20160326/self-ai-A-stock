$ErrorActionPreference = "Stop"
$taskName = "AStockTrendDailyReport"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $project "run_daily_report.bat"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$script`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At 16:00
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task created: $taskName at 16:00 daily"
Write-Host "Project: $project"
Write-Host "Action: $script"
