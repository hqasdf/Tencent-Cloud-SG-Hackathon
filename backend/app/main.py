from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.config import AgentConfigurationError, AgentSettings
from app.api.cases import router as case_router
from app.api.intake import router as intake_router

app = FastAPI(title="RydeResolve Case API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    # Credentials stay disabled: this API needs no cookies or auth headers, and
    # keeping it off avoids the wildcard-plus-credentials hazard.
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)
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
