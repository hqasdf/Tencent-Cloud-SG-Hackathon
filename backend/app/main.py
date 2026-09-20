from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.config import AgentConfigurationError, AgentSettings
from app.api.cases import router as case_router

app = FastAPI(title="RydeResolve Case API", version="0.2.0")

# Vite auto-increments its port when 5173 is busy ("Port 5173 is in use, trying
# another one..."), so a fixed pair of origins breaks the moment a stale dev
# server holds 5173. Allow the whole local dev range instead: this is a
# localhost-only prototype and the origin is still restricted to loopback.
DEV_ORIGINS = [
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(5173, 5181)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    # Credentials stay disabled: this API needs no cookies or auth headers, and
    # keeping it off avoids the wildcard-plus-credentials hazard.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(case_router)


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
