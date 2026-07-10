"""EvalOps API — FastAPI application factory."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from backend.api.routes import evals, optimization, pipelines, plugins, traces, tuning
from backend.api.websocket import router as ws_router

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiting (simple in-memory token-bucket per IP)
# ---------------------------------------------------------------------------

_rate_buckets: dict[str, tuple[float, int]] = {}
RATE_LIMIT = 120  # requests per window
RATE_WINDOW = 60.0  # seconds
MAX_BUCKETS = 10_000  # prevent unbounded memory growth


def _rate_limit_key(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def _evict_stale_buckets(now: float) -> None:
    """Remove entries whose window has expired to free memory."""
    stale_keys = [
        key for key, (ts, _) in _rate_buckets.items()
        if now - ts > RATE_WINDOW
    ]
    for key in stale_keys:
        del _rate_buckets[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path.startswith("/health"):
            return await call_next(request)

        key = _rate_limit_key(request)
        now = time.monotonic()

        # Evict stale entries when approaching or at capacity
        if len(_rate_buckets) >= MAX_BUCKETS:
            _evict_stale_buckets(now)

        # If still at capacity after eviction, reject the new key
        if len(_rate_buckets) >= MAX_BUCKETS and key not in _rate_buckets:
            return JSONResponse(
                status_code=429,
                content={"detail": "Server is under heavy load. Try again shortly."},
            )

        bucket = _rate_buckets.get(key)

        if bucket is None or now - bucket[0] > RATE_WINDOW:
            _rate_buckets[key] = (now, 1)
        elif bucket[1] >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again shortly."},
            )
        else:
            _rate_buckets[key] = (bucket[0], bucket[1] + 1)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Request-timing middleware
# ---------------------------------------------------------------------------

class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 1),
        )
        return response


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("evalops_api_startup", version=app.version)
    yield
    logger.info("evalops_api_shutdown")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Build and return the FastAPI application with all middleware."""
    application = FastAPI(
        title="EvalOps API",
        description="Unified full-pipeline evaluation & optimization platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # --- Middleware (order matters: first added = outermost) ----------------
    cors_origins_raw = os.environ.get("CORS_ORIGINS", "")
    cors_origins = [
        o.strip() for o in cors_origins_raw.split(",") if o.strip()
    ]
    use_credentials = bool(cors_origins)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=use_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Process-Time-Ms"],
    )
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(TimingMiddleware)

    # --- Exception handlers ------------------------------------------------
    @application.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(Exception)
    async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # --- Routers -----------------------------------------------------------
    application.include_router(pipelines.router)
    application.include_router(evals.router)
    application.include_router(traces.router)
    application.include_router(optimization.router)
    application.include_router(plugins.router)
    application.include_router(tuning.router)
    application.include_router(ws_router)

    # --- Health check -------------------------------------------------------
    @application.get("/health", tags=["ops"], response_model=dict[str, str])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy", "service": "evalops-api", "version": "0.1.0"}

    return application


# Convenience for uvicorn
app = create_app()
