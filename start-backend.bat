@echo off
REM Starts the RydeResolve FastAPI backend on http://127.0.0.1:8000
cd /d "%~dp0backend"
set PYTHONPATH=%CD%
"C:\Users\Han Qian\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
