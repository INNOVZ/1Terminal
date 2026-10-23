from unittest.mock import AsyncMock

import httpx
import pytest

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.domain.errors import ConfigurationError, RateLimitError, VenueError


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/fapi/v1/order"), ("DELETE", "/fapi/v1/order"),
    ("GET", "/fapi/v2/account"), ("GET", "/api/v3/account"),
    ("GET", "/fapi/v1/openOrders"), ("GET", "/fapi/v1/listenKey"),
    ("POST", "/fapi/v1/klines"), ("GET", "https://example.test/fapi/v1/klines"),
    ("GET", "/fapi/v1/klines?signature=secret"),
])
async def test_non_public_requests_never_reach_transport(method, path):
    handler = AsyncMock(return_value=httpx.Response(200, json={}))
    client = BinanceHttpClient("https://example.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ConfigurationError):
            await client.request(method, path)
        handler.assert_not_called()
    finally:
        await client.close()


async def test_public_request_has_no_credentials_even_when_environment_has_keys(monkeypatch):
    monkeypatch.setenv("ATLAS_BINANCE_FUTURES_API_KEY", "old-deployed-key")
    monkeypatch.setenv("ATLAS_BINANCE_FUTURES_API_SECRET", "old-deployed-secret")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])
    client = BinanceHttpClient("https://example.test", transport=httpx.MockTransport(handler))
    try:
        assert await client.request("GET", "/fapi/v1/klines", params={"symbol": "BTCUSDT"}) == []
        assert "x-mbx-apikey" not in requests[0].headers
        assert "signature" not in str(requests[0].url)
        with pytest.raises(ConfigurationError):
            await client.request("GET", "/fapi/v1/klines", params={"signature": "x"})
        assert len(requests) == 1
    finally:
        await client.close()


async def test_rate_limit_honors_retry_after(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "90"}, json={})
        return httpx.Response(200, json=[], headers={"x-mbx-used-weight-1m": "42"})
    sleep = AsyncMock()
    monkeypatch.setattr("algotrader.adapters.binance.client.asyncio.sleep", sleep)
    client = BinanceHttpClient("https://example.test", transport=httpx.MockTransport(handler))
    try:
        assert await client.request("GET", "/fapi/v1/klines") == []
        assert calls == 2
        assert sleep.call_args.args[0] >= 90
        assert client.used_weight_1m == 42
    finally:
        await client.close()


async def test_rate_limit_retry_budget_is_bounded():
    client = BinanceHttpClient(
        "https://example.test", max_rate_limit_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={})),
    )
    try:
        with pytest.raises(RateLimitError):
            await client.request("GET", "/fapi/v1/klines")
    finally:
        await client.close()


@pytest.mark.parametrize("status", [302, 500, 403])
async def test_failed_public_response_is_not_success(status):
    client = BinanceHttpClient(
        "https://example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})),
    )
    try:
        with pytest.raises(VenueError):
            await client.request("GET", "/fapi/v1/klines")
    finally:
        await client.close()

