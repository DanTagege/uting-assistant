@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set UC_HOST=0.0.0.0
set UC_UPSTREAM=unipus
set UC_REAL_SUBMIT=1
if not exist "data\logs" mkdir "data\logs"
".venv\Scripts\python.exe" run.py >> "data\logs\server.log" 2>> "data\logs\server.err.log"
