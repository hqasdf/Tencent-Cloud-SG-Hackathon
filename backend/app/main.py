from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cases import router as case_router
from app.api.intake import router as intake_router

app = FastAPI(title="RydeResolve Case API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(case_router)
app.include_router(intake_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ryderesolve-case-api"}
