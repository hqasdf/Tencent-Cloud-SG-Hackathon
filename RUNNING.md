# RydeResolve — How To Run

Two processes are needed: the FastAPI backend and the React frontend.
They run in separate terminals and both must stay open while you use the app.

---

## Quick start (Windows)

1. Double-click `start-backend.bat`  → backend on http://127.0.0.1:8000
2. Double-click `start-frontend.bat` → frontend on http://127.0.0.1:5173
3. Open http://127.0.0.1:5173 in your browser

---

## First-time setup (only needed on a fresh machine)

### 1. Get a Gemini API key (free)

1. Go to https://aistudio.google.com/app/apikey
2. Create a key — it's free, no credit card required

### 2. Create `backend/.env`

```bash
cd backend
echo GEMINI_API_KEY=your_key_here > .env
```

This file is gitignored — it will never be committed to git.

### 3. Install dependencies

```bash
# backend
cd backend
pip install -r requirements.txt

# frontend
cd frontend
npm install
```

---

## Manual start

### Terminal 1 — Backend

```bash
cd "c:\Users\ephra\OneDrive\SP\TENCENT Hackathon\Tencent-Cloud-SG-Hackathon\backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Add `--reload` during development so the server auto-restarts on file changes:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Terminal 2 — Frontend

```bash
cd "c:\Users\ephra\OneDrive\SP\TENCENT Hackathon\Tencent-Cloud-SG-Hackathon\frontend"
npm run dev
```

---

## How the API key is loaded

The backend reads `GEMINI_API_KEY` from the environment via this priority:

1. **Real environment variable** (production — set in your cloud platform's dashboard)
2. **`backend/.env` file** (local dev — gitignored, never committed)

The `.env` file is loaded at startup before any app imports, so every service
sees the key. If neither source provides a key, the AI intake interview will
return `LLM_UNAVAILABLE` errors. The rest of the app (case list, deterministic
analysis, advocates in mock mode) works without a key.

---

## Useful URLs

| URL | What |
|-----|------|
| http://127.0.0.1:5173 | Frontend UI |
| http://127.0.0.1:8000/api/health | Backend health check |
| http://127.0.0.1:8000/docs | Swagger API docs |
| http://127.0.0.1:8000/api/cases | Case list |
| http://127.0.0.1:8000/api/intake/trips | Available trips for intake |

---

## Tests

```bash
# backend
cd backend
python -m pytest

# frontend production build (type-check + build)
cd frontend
npm run build

# legacy root Node pipeline (not used by the running app)
npm test
```

---

## Notes

- The backend is NOT a background service. Closing its terminal stops it.
  If the backend is down, the frontend shows an API-error state.
- Frontend API base URL defaults to `http://localhost:8000`.
  Override with `VITE_API_BASE_URL` in `frontend/.env` if you change the backend port.
- To use a different backend port, change `--port` AND set `VITE_API_BASE_URL`.

---

## Deployment

For production, **do not** ship a `.env` file. Set `GEMINI_API_KEY` as a
secret/environment variable in your cloud platform's dashboard
(Tencent Cloud, Railway, Render, etc.). The code reads it via
`os.environ.get("GEMINI_API_KEY")` — no `.env` file needed in production.

If the key has been exposed (e.g. shared in terminal commands), rotate it
at https://aistudio.google.com/app/apikey before deploying.

---

## "Port 5173 is in use, trying another one..."

This is Vite port auto-incrementing, not an error. It happens when a previously
started dev server is still alive (e.g. a terminal you closed without Ctrl+C).
Vite picks the next free port — 5174, 5175, etc.

What to do:

1. Use the port Vite prints (e.g. http://127.0.0.1:5174).
   The backend allows browser requests from ports 5173–5180.
2. Or free port 5173 first:

```bash
netstat -ano | findstr :5173      # note the PID in the last column
taskkill /PID <pid> /F            # stop the leftover dev server
```

To check which ports are currently in use:

```bash
netstat -ano | findstr LISTENING | findstr ":517"
```

---

## "Port 8000 is in use"

A stale `uvicorn` process is holding the port. Find and kill it:

```bash
netstat -ano | findstr :8000 | findstr LISTENING
taskkill /PID <pid> /F
```

Wait 2 seconds for the socket to release, then restart the backend.
