"""
NEXUS — FastAPI application entrypoint.

Run with:
  uvicorn nexus.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from nexus.api import commands, sessions, synthesis
from nexus.api import websocket as ws_router
from nexus.config import settings

# ── Logging ───────────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer() if settings.nexus_env == "development"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}.get(
            settings.nexus_log_level.upper(), 20
        )
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS",
    description="Intelligence engine for the Solve for X Experience Center",
    version="0.1.0",
    docs_url="/docs" if settings.nexus_env == "development" else None,
    redoc_url=None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(sessions.router, prefix="/api/v1")
app.include_router(commands.router, prefix="/api/v1")
app.include_router(synthesis.router, prefix="/api/v1")
app.include_router(ws_router.router)          # WebSocket — no /api/v1 prefix (WS clients are simpler)

# ── Runtime config for tablet UI ──────────────────────────────────────────────
# Serves Azure AD client/tenant IDs as a JS global so the static HTML never
# needs to hard-code secrets or environment-specific values.

@app.get("/config.js", include_in_schema=False)
async def config_js():
    js = (
        "window.NEXUS_CONFIG = {"
        f'  clientId: "{settings.azure_client_id}",'
        f'  authority: "https://login.microsoftonline.com/{settings.azure_tenant_id}"'
        "};"
    )
    return Response(content=js, media_type="application/javascript")

# ── Static files (tablet UI) — mounted last so explicit routes take priority ──

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"], include_in_schema=False)
async def health():
    return {"status": "ok", "service": "nexus"}


# ── Startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    log.info("nexus_started", env=settings.nexus_env)


@app.on_event("shutdown")
async def shutdown():
    log.info("nexus_shutdown")
