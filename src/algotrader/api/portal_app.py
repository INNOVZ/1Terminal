from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from algotrader.api import portal
from algotrader.config import Environment, Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runtime = await portal.get_portal_runtime(app_settings)
        if app_settings.screener_cron_enabled:
            portal.start_screener_cron(interval_hours=app_settings.screener_interval_hours)
        try:
            yield
        finally:
            await portal.stop_portal()

    application = FastAPI(
        title="Atlas Crypto Screener API",
        description="Public-market research, screening, charts and watchlists. No order execution.",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None if app_settings.environment is Environment.PRODUCTION else "/docs",
        redoc_url=None,
        openapi_url=None if app_settings.environment is Environment.PRODUCTION else "/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["https://alfa-terminal.vercel.app"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @application.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(portal.portal_router)

    @application.get("/")
    async def root() -> dict[str, object]:
        return {
            "status": "online", "service": "Atlas Crypto Screener",
            "version": "0.2.0", "mode": "screener", "order_execution_enabled": False,
            "validation_status": "uncalibrated_research",
            "endpoints": {
                "health": "/healthz", "ready": "/readyz",
                "screener_live": "/api/screener/live",
                "watchlist": "/api/watchlist", "cron_status": "/api/screener/cron/status",
            },
        }

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "atlas-screener-api"}

    @application.get("/readyz")
    async def readyz() -> JSONResponse:
        runtime = getattr(application.state, "runtime", None)
        if runtime is None:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        ready, details = await runtime.readiness()
        cron_ready = not app_settings.screener_cron_enabled or (
            portal._cron_task is not None and not portal._cron_task.done()
        )
        if not cron_ready:
            details = {**details, "status": "not_ready", "scheduler": "stopped"}
        return JSONResponse(details, status_code=200 if ready and cron_ready else 503)

    return application


app = create_app()


def main() -> None:
    uvicorn.run("algotrader.api.portal_app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
