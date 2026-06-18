param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$taskName = "AStockTrendDailyReport"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $project "trigger_daily_report.ps1"
if ($PythonExe -and (Test-Path $PythonExe)) {
    $python = $PythonExe
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
}
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -PythonExe `"$python`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At 18:00
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
} catch {
    Write-Warning "Register-ScheduledTask failed: $($_.Exception.Message)"
    Write-Warning "Trying schtasks.exe fallback..."
    $taskRun = "powershell.exe $actionArgs"
    schtasks.exe /Create /TN $taskName /SC DAILY /ST 18:00 /TR $taskRun /F | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot create scheduled task. Please run install_daily_task_admin.bat as Administrator and approve UAC."
    }
}
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "Scheduled task created: $taskName at 18:00 daily"
Write-Host "Project: $project"
Write-Host "Action: $script"
Write-Host "Python: $python"
Write-Host "User: $user"
Write-Host "Next run: $($info.NextRunTime)"
