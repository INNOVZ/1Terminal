"""Tests for BinanceHttpClient /fapi/v1 weight and /futures/data/* 5-minute sliding-window quota."""
from __future__ import annotations

import httpx
import pytest

from algotrader.adapters.binance.client import BinanceHttpClient


@pytest.mark.asyncio
async def test_futures_data_sliding_window_sleeps_at_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    clock = [1000.0]

    monkeypatch.setattr("algotrader.adapters.binance.client.time.monotonic", lambda: clock[0])

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)
        clock[0] += sec

    monkeypatch.setattr("algotrader.adapters.binance.client.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        clock[0] += 1.0
        return httpx.Response(200, json=[], headers={"x-mbx-used-weight-1m": "10"})

    transport = httpx.MockTransport(handler)
    client = BinanceHttpClient(
        "https://fapi.binance.com",
        transport=transport,
        max_futures_data_requests_5m=3,
    )
    try:
        for _ in range(3):
            await client.request(
                "GET",
                "/futures/data/openInterestHist",
                params={"symbol": "BTCUSDT", "period": "1h", "limit": 10},
            )
        assert slept == []
        assert client.futures_data_requests_5m == 3

        # 4th call within 300s window must sleep until oldest timestamp expires
        await client.request(
            "GET",
            "/futures/data/takerlongshortRatio",
            params={"symbol": "BTCUSDT", "period": "1h", "limit": 10},
        )
        assert len(slept) == 1
        assert slept[0] >= 296.0
        assert client.futures_data_requests_5m <= 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_non_futures_data_endpoints_do_not_consume_futures_data_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [5000.0]
    monkeypatch.setattr("algotrader.adapters.binance.client.time.monotonic", lambda: clock[0])

    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={}))
    client = BinanceHttpClient(
        "https://fapi.binance.com",
        transport=transport,
        max_futures_data_requests_5m=2,
    )
    try:
        for _ in range(5):
            await client.request("GET", "/fapi/v1/openInterest", params={"symbol": "BTCUSDT"})
        assert client.futures_data_requests_5m == 0
    finally:
        await client.close()

