# RydeResolve Case API

Minimal FastAPI backend for the frontend-first RydeResolve milestone.

## Local development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Endpoints:

- `GET /api/health`
- `GET /api/cases`
- `GET /api/cases/{case_id}`

The app deliberately uses local synthetic data through `MockCaseRepository`. API routes depend on `CaseService`, so a future `PostgresCaseRepository` can replace the in-memory source without changing route behavior.
