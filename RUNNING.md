# RydeResolve - How To Run

Two processes are needed: the FastAPI backend and the React frontend.
They run in separate terminals and both must stay open while you use the app.

## Quick start (Windows)

1. Double-click `start-backend.bat`  -> waits, backend on http://127.0.0.1:8000
2. Double-click `start-frontend.bat` -> frontend on http://127.0.0.1:5173
3. Open the URL that Vite prints in its window (usually http://127.0.0.1:5173)

## Manual start

### Terminal 1 - Backend

    cd "C:/Users/Han Qian/Documents/Tencent-Cloud-SG-Hackathon/backend"
    PYTHONPATH=. "C:/Users/Han Qian/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

### Terminal 2 - Frontend

    cd "C:/Users/Han Qian/Documents/Tencent-Cloud-SG-Hackathon/frontend"
    npm run dev

## Alternate start commands

If you activate the project virtual environment first, the plain `python` command
works and you do not need the full interpreter path:

    "C:/Users/Han Qian/.workbuddy-ai/binaries/python/envs/default/Scripts/activate"

    cd backend
    python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

`--reload` restarts the server automatically when backend files change. It is
handy while developing. This project does not otherwise require it.

Important: the backend dependencies (fastapi, uvicorn, pytest) are installed ONLY
in the project virtual environment above. A bare `python -m uvicorn ...` without
activating that environment may resolve to a different Python install and fail
with "No module named 'uvicorn'". If that happens, use the full interpreter path
shown in "Terminal 1 - Backend" instead.

## Useful URLs

    http://127.0.0.1:5173                          Frontend
    http://127.0.0.1:8000/api/health               Backend health check
    http://127.0.0.1:8000/docs                     Swagger API docs
    http://127.0.0.1:8000/api/cases                Case list
    http://127.0.0.1:8000/api/cases/CASE-2026-1041/analysis   Deterministic analysis

## First-time setup (only needed on a fresh machine)

    # frontend dependencies
    npm --prefix frontend install

    # backend dependencies (managed Python venv)
    "C:/Users/Han Qian/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m pip install -r backend/requirements.txt

## Tests

    # backend
    cd backend
    PYTHONPATH=. "C:/Users/Han Qian/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m pytest

    # frontend production build
    npm --prefix frontend run build

    # legacy root Node pipeline
    npm test

## Notes

- The backend is NOT a background service. Closing its terminal stops it.
  If the backend is down, the frontend shows an API-error state instead of cases.
- Frontend API base URL defaults to http://localhost:8000.
  Override with VITE_API_BASE_URL in frontend/.env if you change the backend port.
- To use a different backend port, change --port above AND set VITE_API_BASE_URL.

## "Port 5173 is in use, trying another one..."

This is Vite port auto-incrementing, not an error. It happens when a previously
started dev server is still alive (for example a terminal you closed without
pressing Ctrl+C). Vite picks the next free port - 5174, 5175, and so on.

What to do:

1. Use the port Vite actually prints, for example http://127.0.0.1:5174
   The backend already allows browser requests from ports 5173 and 5174.
2. Or free port 5173 first: find and stop the leftover Node process, then
   restart the frontend so it claims 5173 again.

    netstat -ano | findstr :5173      # note the PID in the last column
    taskkill /PID <pid> /F            # stop the leftover dev server

To check which ports are currently in use:

    netstat -ano | findstr LISTENING | findstr ":517"

If you want to force a specific port instead of auto-incrementing, set
`server.strictPort = true` in frontend/vite.config.ts so startup fails loudly
instead of silently moving to the next port.
