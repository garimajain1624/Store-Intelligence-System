from __future__ import annotations

import json
import time
from typing import Callable

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.database import DBUnavailableError, ensure_db_available, new_trace_id
from app.models import init_db
from app.routers.analytics import router as analytics_router
from app.routers.events import router as events_router
from app.routers.health import router as health_router
from app.routers.dashboard import router as dashboard_router


def _json_log(payload: dict) -> None:
    # Structured JSON logs (stdout). Docker will collect these.
    print(json.dumps(payload, ensure_ascii=False))


def create_app() -> FastAPI:
    app = FastAPI(title="Store Intelligence", version="0.1.0")

    @app.middleware("http")
    async def trace_and_log(request: Request, call_next: Callable):  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("x-trace-id") or new_trace_id()
        request.state.trace_id = trace_id

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000.0)
            _json_log(
                {
                    "trace_id": trace_id,
                    "endpoint": f"{request.method} {request.url.path}",
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "store_id": getattr(request.state, "store_id", None),
                    "event_count": getattr(request.state, "event_count", None),
                }
            )

    @app.exception_handler(DBUnavailableError)
    async def db_unavailable_handler(request: Request, exc: DBUnavailableError) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or exc.trace_id or new_trace_id()
        payload = exc.to_dict()
        payload["trace_id"] = trace_id
        return JSONResponse(status_code=503, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or new_trace_id()
        return JSONResponse(status_code=500, content={"error": "internal_server_error", "trace_id": trace_id})

    @app.on_event("startup")
    async def _startup() -> None:
        _json_log({"event": "service_start", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        ensure_db_available()
        init_db()

    app.include_router(events_router)
    app.include_router(analytics_router)
    app.include_router(health_router)
    app.include_router(dashboard_router)
    return app


app = create_app()

