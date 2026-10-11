# Load backend/.env before importing routes. Intake resolves the configured
# provider lazily, so missing AI configuration cannot prevent FastAPI startup.
from app.env import load_local_env
load_local_env()

from app.deployment import DEV_ORIGINS, allowed_origins, production_mode, validate_production
validate_production()  # Before route import can initialize SQLite.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from app.security import RequestBoundary, redacted_validation_error

from app.agents.config import AgentConfigurationError, AgentSettings
from app.api.cases import router as case_router
from app.api.intake import router as intake_router

app = FastAPI(title="RydeResolve Case API", version="0.2.0",
              docs_url=None if production_mode() else "/docs",
              redoc_url=None if production_mode() else "/redoc",
              openapi_url=None if production_mode() else "/openapi.json")
app.add_exception_handler(RequestValidationError, redacted_validation_error)

# Vite auto-increments its port when 5173 is busy ("Port 5173 is in use, trying
# another one..."), so a fixed pair of origins breaks the moment a stale dev
# server holds 5173. Allow the whole local dev range instead: this is a
# localhost-only prototype and the origin is still restricted to loopback.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    # Credentials stay disabled: this API needs no cookies or auth headers, and
    # keeping it off avoids the wildcard-plus-credentials hazard.
    allow_credentials=False,
    # POST is required by the Stage 4 advocates endpoint and by every AI intake
    # endpoint. Restricting this to GET silently breaks them in the browser.
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(RequestBoundary)
app.include_router(case_router)
app.include_router(intake_router)


@app.get("/api/health")
def health() -> dict[str, object]:
    try:
        agent_settings = AgentSettings.from_environment().describe()
    except AgentConfigurationError as error:
        agent_settings = {"mode": "invalid", "configured": False, "error": str(error)}
    return {
        "status": "ok",
        "service": "ryderesolve-case-api",
        "agents": agent_settings,
    }
