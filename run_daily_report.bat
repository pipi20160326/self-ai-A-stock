@echo off
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0trigger_daily_report.ps1"
exit /b %ERRORLEVEL%
