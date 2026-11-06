from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.shadow_budget import ShadowBudget, ShadowBudgetExceeded
from algotrader.domain.errors import ConfigurationError
from algotrader.domain.shadow_discovery import ShadowPolicy
from tests.conftest import make_instrument


async def test_actual_attempts_include_transport_retries_and_stop_before_io():
    seen = []

    async def handler(request):
        seen.append(request.url.path)
        raise httpx.ConnectError("offline", request=request)

    market = ShadowMarketData(
        ShadowBudget(ShadowPolicy(max_attempts=2)), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ShadowBudgetExceeded):
            await market.decision_tickers()
        assert len(seen) == 2
        assert market.budget.snapshot()["attempts"] == 2
    finally:
        await market.close()


async def test_all_150_attempts_allowed_but_151_never_reaches_transport():
    seen = []

    async def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=[])

    market = ShadowMarketData(ShadowBudget(ShadowPolicy()), transport=httpx.MockTransport(handler))
    try:
        for _ in range(150):
            await market.decision_tickers()
        with pytest.raises(ShadowBudgetExceeded):
            await market.decision_tickers()
        assert len(seen) == 150
    finally:
        await market.close()


async def test_deadline_terminates_hanging_io_and_independent_budgets():
    async def hanging(request):
        await asyncio.Event().wait()

    clock = [10.0]
    budget = ShadowBudget(replace(ShadowPolicy(), deadline_seconds=1), monotonic=lambda: clock[0])
    market = ShadowMarketData(budget, transport=httpx.MockTransport(hanging))
    try:
        with pytest.raises(ShadowBudgetExceeded, match="deadline"):
            await market.decision_tickers()
        assert budget.snapshot()["attempts"] == 1
        assert ShadowBudget(ShadowPolicy()).snapshot()["attempts"] == 0
        clock[0] = 12.0
        with pytest.raises(ShadowBudgetExceeded):
            budget.before_attempt()
    finally:
        await market.close()


async def test_snapshot_retains_missing_values_times_and_exchange_weight():
    async def handler(request):
        assert request.url.path == "/fapi/v1/ticker/24hr"
        assert not request.url.query
        return httpx.Response(
            200,
            json=[{"symbol": "TESTUSDT", "lastPrice": "12", "closeTime": 1}],
            headers={"x-mbx-used-weight-1m": "40"},
        )

    market = ShadowMarketData(ShadowBudget(ShadowPolicy()), transport=httpx.MockTransport(handler))
    try:
        rows = await market.decision_tickers()
        assert "priceChangePercent" not in rows[0]
        assert rows[0]["closeTime"] == 1
        assert market.snapshot_started_at <= market.snapshot_completed_at
        assert market.budget.snapshot()["observed_weight_1m"] == 40
        with pytest.raises(ConfigurationError):
            await market.client.request("GET", "/fapi/v2/account")
        assert market.budget.snapshot()["attempts"] == 1
    finally:
        await market.close()


async def test_cancellation_is_not_converted_to_empty_snapshot():
    async def handler(request):
        raise asyncio.CancelledError

    market = ShadowMarketData(ShadowBudget(ShadowPolicy()), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(asyncio.CancelledError):
            await market.decision_tickers()
    finally:
        await market.close()


async def test_nested_derivative_retries_cannot_hide_budget_exhaustion():
    seen = []

    async def handler(request):
        seen.append(request.url.path)
        return httpx.Response(500, json={"error": "unavailable"})

    market = ShadowMarketData(
        ShadowBudget(ShadowPolicy(max_attempts=2)), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ShadowBudgetExceeded):
            await market.derivatives_snapshot(make_instrument())
        assert len(seen) == 2
    finally:
        await market.close()


async def test_deadline_includes_exchange_backoff_without_retrying_early():
    seen = []

    async def handler(request):
        seen.append(request.url.path)
        return httpx.Response(429, json={}, headers={"Retry-After": "10"})

    market = ShadowMarketData(
        ShadowBudget(ShadowPolicy(deadline_seconds=1)), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ShadowBudgetExceeded, match="deadline"):
            await market.decision_tickers()
        assert len(seen) == 1
    finally:
        await market.close()


async def test_concurrent_children_do_not_share_attempt_limits():
    async def handler(request):
        return httpx.Response(200, json=[])

    markets = [
        ShadowMarketData(
            ShadowBudget(ShadowPolicy(max_attempts=1)), transport=httpx.MockTransport(handler)
        )
        for _ in range(2)
    ]
    try:
        assert await asyncio.gather(*(market.decision_tickers() for market in markets)) == [[], []]
        for market in markets:
            with pytest.raises(ShadowBudgetExceeded):
                await market.decision_tickers()
            assert market.budget.snapshot()["attempts"] == 1
    finally:
        await asyncio.gather(*(market.close() for market in markets))


async def test_successful_empty_weekly_history_is_not_a_provider_failure():
    async def handler(request):
        assert request.url.path == "/fapi/v1/klines"
        assert request.url.params["interval"] == "1w"
        return httpx.Response(200, json=[])

    market = ShadowMarketData(ShadowBudget(ShadowPolicy()), transport=httpx.MockTransport(handler))
    try:
        assert await market.candles(make_instrument(), "1w", 40) == []
    finally:
        await market.close()
