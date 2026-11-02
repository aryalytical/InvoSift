"""FastAPI application entry point.

Run from the project root:   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import BASE_DIR, get_settings
from backend.database import init_db
from backend.routes import health, invoices, chat

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("invoice_ai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    s = get_settings()
    logger.info("InvoSift started. Model=%s (all processing is local)", s.ollama_model)
    yield


app = FastAPI(title="AI Invoice Data Extractor", version="1.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(invoices.router)
app.include_router(chat.router)


@app.middleware("http")
async def require_desktop_session(request: Request, call_next):
    """Require the per-launch Electron session secret for every API route."""
    if request.url.path.startswith("/api/") or request.url.path == "/api":
        expected = os.getenv("INVOSIFT_SESSION_TOKEN", "")
        supplied = request.headers.get("x-invosift-session", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized."})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) or "request" for e in exc.errors()[:5])
    return JSONResponse(status_code=422, content={"detail": f"Invalid request data ({fields})."})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    # Log the type/trace server-side only; users never see Python stack traces.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Please try again."})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store") if request.url.path.startswith("/api") else None
    return response


# Frontend (static). Mounted last so /api/* routes win. Only the frontend folder is served - never data/.
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
