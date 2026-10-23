from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from algotrader.api import portal
from algotrader.api.app import create_app
from algotrader.config import Settings


def test_supported_entry_points_are_the_same_screener():
    from algotrader.api.app import app as old_entry
    from algotrader.api.portal_app import app as new_entry
    assert old_entry is new_entry
    paths = set(new_entry.openapi()["paths"])
    assert "/api/screener/live" in paths
    assert not any(path.startswith(("/v1/", "/api/testnet")) for path in paths)


async def test_removed_trading_endpoints_are_unreachable():
    app = create_app(Settings(_env_file=None, screener_cron_enabled=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        for path in ("/api/testnet/status", "/api/testnet/watchlist", "/v1/status", "/v1/run-once"):
            for method in ("GET", "POST"):
                response = await client.request(method, path)
                assert response.status_code == 404
        root = (await client.get("/")).json()
        assert root["mode"] == "screener"
        assert root["order_execution_enabled"] is False


def test_lifespan_closes_runtime_and_cancels_cron(monkeypatch):
    runtime = AsyncMock()
    runtime.readiness.return_value = (True, {"status": "ready"})
    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    app = create_app(Settings(_env_file=None, screener_cron_enabled=True))
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        assert portal._cron_task is not None
    runtime.close.assert_awaited_once()
    assert portal._cron_task is None
    assert portal._shared_runtime is None


async def test_readiness_does_not_claim_ready_before_startup():
    app = create_app(Settings(_env_file=None, screener_cron_enabled=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/readyz")).status_code == 503
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"


def test_production_disables_interactive_schema():
    app = create_app(Settings(_env_file=None, environment="production"))
    assert app.docs_url is None
    assert app.openapi_url is None
