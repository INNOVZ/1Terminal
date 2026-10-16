from __future__ import annotations

from typing import Any, Protocol, cast
from unittest.mock import AsyncMock

import httpx
from fastapi.routing import APIRoute

from algotrader.api.app import create_app
from algotrader.config import Environment, Settings


class IncludedRouter(Protocol):
    original_router: Any


def test_every_operator_route_inherits_router_level_authentication() -> None:
    app = create_app(Settings.model_validate({"operator_token": "x" * 32}))
    route_groups = [
        cast(IncludedRouter, route).original_router
        for route in app.routes
        if hasattr(route, "original_router")
    ]
    protected: list[Any] = [
        route
        for group in route_groups
        for route in group.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1/")
    ] or [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1/")
    ]

    assert protected
    assert all(route.dependant.dependencies for route in protected)


def test_production_disables_interactive_api_schema() -> None:
    app = create_app(
        Settings.model_validate(
            {
                "environment": Environment.PRODUCTION,
                "operator_token": "x" * 32,
            }
        )
    )

    assert app.docs_url is None
    assert app.openapi_url is None


async def test_operator_route_rejects_missing_bearer_token_before_runtime_access() -> None:
    app = create_app(Settings.model_validate({"operator_token": "x" * 32}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/v1/status")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_security_headers_are_applied_centrally() -> None:
    app = create_app(Settings.model_validate({"operator_token": "x" * 32}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["cache-control"] == "no-store"


async def test_readiness_returns_503_when_runtime_is_not_ready() -> None:
    app = create_app(Settings.model_validate({"operator_token": "x" * 32}))
    runtime = AsyncMock()
    runtime.readiness.return_value = (
        False,
        {"status": "not_ready", "state_store": "ok", "scheduler": "stopped"},
    )
    app.state.runtime = runtime

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json() == {"status": "not_ready"}
