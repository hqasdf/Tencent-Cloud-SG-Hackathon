@echo off
REM Starts the RydeResolve FastAPI backend on http://127.0.0.1:8000
REM
REM AI configuration is read from backend/.env (gitignored) or AGENT_* variables.
REM Never hardcode the key here — this file is committed to git.
cd /d "%~dp0backend"
set PYTHONPATH=%CD%
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  goto :eof
)
echo Python was not found. Create backend\.venv and install requirements first.
exit /b 1
