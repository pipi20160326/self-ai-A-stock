@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File setup_daily_task.ps1
pause
