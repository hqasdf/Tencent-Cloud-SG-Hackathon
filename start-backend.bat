@echo off
REM Starts the RydeResolve FastAPI backend on http://127.0.0.1:8000
REM
REM The Gemini API key is read from backend/.env (gitignored) or from the
REM GEMINI_API_KEY environment variable set by your deploy platform.
REM Never hardcode the key here — this file is committed to git.
cd /d "%~dp0backend"
set PYTHONPATH=%CD%
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
