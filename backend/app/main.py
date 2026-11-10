"""Groundwork: the one backend application.

``uvicorn app.main:app`` locally; the same image on Cloud Run. When ``frontend/dist`` exists
it is served from this process too, so production is a single service.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1 import router as v1
from app.core.config import get_settings
from app.core.logging import configure as configure_logging
from app.core.logging import event, new_request_id, parse_cloud_trace, request_id, trace_id
from app.core.resilience import PROVIDER_STATS
from app.infrastructure import storage
from app.infrastructure.database.session import engine, pool_status
from app.services import ai

logger = logging.getLogger("groundwork")
access = logging.getLogger("groundwork.access")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(json_logs=settings.json_logs, project=settings.gcp_project, level=settings.log_level)
    settings.validate_runtime()
    event(logger, "startup", environment=settings.environment, version=__version__)
    yield
    # Cloud Run sends SIGTERM and waits; uvicorn drains in-flight requests, then we release the pool.
    engine().dispose()
    event(logger, "shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Groundwork API",
        version=__version__,
        description="Evidence-backed inherited-property resolution cases.",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization", "x-groundwork-client"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        rid = new_request_id(request.headers.get("x-request-id"))
        request_id.set(rid)
        trace_id.set(parse_cloud_trace(request.headers.get("x-cloud-trace-context")))
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in ("/health", "/ready"):
            route = request.scope.get("route")
            event(
                access, "request", method=request.method, path=getattr(route, "path", "unmatched"),
                status=response.status_code, duration_ms=round((time.perf_counter() - started) * 1000, 1),
                user_id=getattr(request.state, "user_id", None), organization_id=getattr(request.state, "organization_id", None),
            )
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        first = fields[0] if fields else {"field": "", "message": "invalid request"}
        return JSONResponse({"error": f"{first['field']}: {first['message']}".strip(": "), "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse({"error": "Internal error"}, status_code=500)

    @app.get("/health", tags=["health"], include_in_schema=False)
    def liveness() -> dict:
        """Process-alive only: no database or provider calls, so a slow dependency never restarts the container."""
        return {"ok": True, "service": settings.service_name, "version": __version__}

    @app.get("/ready", tags=["health"], include_in_schema=False)
    def readiness() -> JSONResponse:
        """Can this instance serve traffic: database reachable, pool not exhausted, config complete."""
        checks: dict[str, dict] = {}
        try:
            with engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = {"ok": True, "dialect": engine().dialect.name}
        except Exception as exc:
            checks["database"] = {"ok": False, "error": type(exc).__name__}
        pool = pool_status()
        capacity = (pool["size"] or 0) + settings.db_max_overflow
        checks["db_pool"] = {"ok": pool["checked_out"] is None or pool["checked_out"] < capacity, **pool}
        try:
            settings.validate_runtime()
            checks["config"] = {"ok": True}
        except RuntimeError:
            checks["config"] = {"ok": False}
        stats = PROVIDER_STATS.snapshot()
        checks["providers"] = {"ok": True, **{name: {"calls": v["calls"], "failures": v["failures"]} for name, v in stats.items()}}
        ok = all(c["ok"] for c in checks.values())
        return JSONResponse({"ok": ok, "version": __version__, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict:
        try:
            with engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            database = {"ok": True, "dialect": engine().dialect.name}
        except Exception as exc:  # reported, not raised: the health check must answer
            database = {"ok": False, "error": type(exc).__name__}
        return {
            "ok": database["ok"],
            "service": settings.service_name,
            "version": __version__,
            "environment": settings.environment,
            "database": database,
            "storage": storage.status()["backend"],
            "extraction": settings.extraction_backend,
            "ai": ai.status()["backend"] if ai.enabled() else "none",
            "pii": settings.pii_backend,
        }

    app.include_router(v1, prefix="/api/v1")

    dist = Path(settings.frontend_dist)
    if (dist / "index.html").exists():
        root = dist.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, detail="Not found")
            candidate = (root / path).resolve()
            if path and root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    return app


app = create_app()
