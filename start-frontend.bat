@echo off
REM Starts the RydeResolve React frontend on http://127.0.0.1:5173
cd /d "%~dp0frontend"
call npm run dev
