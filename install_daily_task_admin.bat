@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%I in ('powershell -NoProfile -Command "(Get-Command python).Source"') do set "ASTOCK_PYTHON=%%I"
set "ASTOCK_SETUP=%~dp0setup_daily_task.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$argList=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$env:ASTOCK_SETUP,'-PythonExe',$env:ASTOCK_PYTHON); Start-Process powershell -Verb RunAs -ArgumentList $argList"
echo If a UAC window appeared, approve it to install AStockTrendDailyReport.
pause
