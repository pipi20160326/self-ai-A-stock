@echo off
cd /d "%~dp0"
uvicorn api_manual:app --host 127.0.0.1 --port 8601
