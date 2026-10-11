# RydeResolve — How To Run

The FastAPI backend and React frontend run in separate terminals.

For the credential-free submission demonstration, use the fresh-checkout procedure in [secure release preparation](docs/final-release-preparation.md). Production must use the authenticated gateway described in [deployment preparation](docs/phase5a-deployment.md).

## Windows quick start

1. Double-click `start-backend.bat` for `http://127.0.0.1:8000`.
2. Double-click `start-frontend.bat` for `http://127.0.0.1:5173`.
3. Open `http://127.0.0.1:5173`.

`start-backend.bat` first uses `backend\.venv\Scripts\python.exe`, then the
Windows `py -3` launcher, then `python`. It prints a clear setup error if none is
available.

## First-time setup

From the repository root:

```powershell
py -3 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock -r backend\requirements.txt
cd frontend
npm ci
```

Copy `backend/.env.example` to `backend/.env`. The `.env` file is local and
gitignored. Do not put credentials in frontend/Vite environment files.

For offline development, keep:

```dotenv
AGENT_MODE=mock
AGENT_PROVIDER=mock
```

Mock mode does not fabricate an AI intake provider. Intake requests return the
safe `NOT_CONFIGURED` code, while FastAPI, deterministic analysis, and the existing
offline advocate/recovery/replay workflows remain available.

After rotating any exposed key, live Gemini configuration is:

```dotenv
AGENT_MODE=real
AGENT_PROVIDER=gemini_native
AGENT_MODEL=gemini-3.8-flash
AGENT_API_KEY=<new server-side credential>
AGENT_TIMEOUT_SECONDS=30
AGENT_MAX_TOKENS=4096
AGENT_MAX_RETRIES=1
```

`AGENT_API_KEY` and `AGENT_MODEL` are shared by Rider/Driver intake, rider intake
response, Rider/Driver Advocates, Rider/Driver Rebuttals, and Judge. There is no
separate `GEMINI_API_KEY` or `GEMINI_MODEL` intake configuration. The application
does not choose from a hardcoded fallback model list.

Missing or invalid intake configuration is resolved only when an intake call is
made, so it does not prevent FastAPI from starting. Provider failures return safe
codes without credentials or raw provider responses.

## Manual start

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm run dev
```

## Useful URLs

| URL | Purpose |
| --- | --- |
| `http://127.0.0.1:5173` | Frontend UI |
| `http://127.0.0.1:8000/api/health` | Backend health and safe provider metadata |
| `http://127.0.0.1:8000/docs` | Development-only Swagger API documentation; disabled in production |
| `http://127.0.0.1:8000/api/cases` | Case list |
| `http://127.0.0.1:8000/api/intake/trips` | Intake trip list |

## Tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm run build
```

If an older Google Cloud or Gemini key was exposed, revoke or rotate it before any
live test. Never reuse the compromised value.
