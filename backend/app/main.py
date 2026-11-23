"""FastAPI skeleton (Step 1.1): starts offline, read-only, no trading routes.

Security (bootstrap/session/CSRF/Host/Origin/CSP) is Step 1.3 and is
NOT IMPLEMENTED here. This skeleton exposes only /healthz and /api/v1/instruments
for contract verification; it binds to 127.0.0.1 by default.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from backend.app.config.settings import Settings, load_instruments
from backend.app.schemas.http_models import HealthResponse, InstrumentsResponse

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Offline-safe startup: no network calls, no feed connections.
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Trading Platform (Stage 1, read-only)", version=VERSION, lifespan=lifespan)


@app.get("/healthz", response_model=HealthResponse, tags=["health"])
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION, network="offline")


@app.get("/api/v1/instruments", response_model=InstrumentsResponse, tags=["instruments"])
def list_instruments() -> InstrumentsResponse:
    instruments = load_instruments()
    return InstrumentsResponse(instruments=instruments)


@app.get("/openapi.json", include_in_schema=False)
def openapi_json():
    return app.openapi()


if __name__ == "__main__":
    import uvicorn

    settings = Settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
