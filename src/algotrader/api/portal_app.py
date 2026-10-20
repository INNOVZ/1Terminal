from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from algotrader.api.portal import portal_router, start_screener_cron


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    start_screener_cron()
    yield


app = FastAPI(
    title="Atlas Institutional Crypto Portal API",
    description="High-conviction Wyckoff accumulation screener, interactive verification chart, and multi-day watchlist engine.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portal_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "atlas-portal-api"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready", "service": "atlas-portal-api"}


def main() -> None:
    print("=" * 70)
    print("🚀 Starting Atlas Institutional Portal Backend on http://127.0.0.1:8000")
    print("=" * 70)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
