@echo off
cd /d "%~dp0"
uvicorn api_server:app --host 127.0.0.1 --port 8600
