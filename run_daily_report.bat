@echo off
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
python -m src.daily_job >> "logs\daily_report.log" 2>&1
