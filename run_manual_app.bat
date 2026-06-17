@echo off
cd /d "%~dp0"
streamlit run app_manual.py --server.port 8502
