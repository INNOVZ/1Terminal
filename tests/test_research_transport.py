import asyncio
from datetime import timedelta

import httpx
import pytest

from algotrader.adapters.binance.research import OutcomePassBudgetReached, ResearchMarketData
from algotrader.application.shadow_budget import ShadowBudget
from algotrader.domain.shadow_discovery import ShadowPolicy
from tests.test_shadow_discovery import NOW, parent_fixture


async def test_outcome_transport_attempt_31_never_reaches_io():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    budget = ShadowBudget(ShadowPolicy(max_attempts=30, deadline_seconds=90))
    market = ResearchMarketData(budget, {}, transport=httpx.MockTransport(handler))
    instrument = parent_fixture().cohort[0]
    try:
        for _ in range(30):
            await market.minute_candles_between(instrument, NOW, NOW + timedelta(hours=24))
        with pytest.raises(OutcomePassBudgetReached):
            await market.minute_candles_between(instrument, NOW, NOW + timedelta(hours=24))
        assert len(seen) == budget.attempts == 30
    finally:
        await market.close()


async def test_actual_network_retries_share_the_transport_budget():
    seen = []

    async def handler(request):
        seen.append(request)
        raise httpx.ConnectError("injected network failure", request=request)

    budget = ShadowBudget(ShadowPolicy(max_attempts=3, deadline_seconds=90))
    market = ResearchMarketData(budget, {}, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OutcomePassBudgetReached):
            await market.minute_candles_between(
                parent_fixture().cohort[0], NOW, NOW + timedelta(hours=24)
            )
        assert len(seen) == budget.attempts == 3
    finally:
        await market.close()


@pytest.mark.parametrize(
    "status,headers",
    [
        (429, {"Retry-After": "7200"}),
        (418, {"Retry-After": "bad"}),
        (200, {"x-mbx-used-weight-1m": "2150"}),
    ],
)
async def test_headers_observed_before_retries_and_pass_stops(status, headers):
    seen = []
    notified = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(status, json=[], headers=headers)

    metadata = {"on_response": notified.append}
    market = ResearchMarketData(
        ShadowBudget(ShadowPolicy(max_attempts=30, deadline_seconds=90)),
        metadata,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(OutcomePassBudgetReached):
            await market.minute_candles_between(
                parent_fixture().cohort[0], NOW, NOW + timedelta(hours=24)
            )
        assert len(seen) == len(notified) == 1
        assert notified[0]["status_code"] == status
        assert notified[0]["retry_after"] == headers.get("Retry-After")
    finally:
        await market.close()


async def test_deadline_stops_minute_acquisition_as_bounded_pass():
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    budget = ShadowBudget(ShadowPolicy(max_attempts=30, deadline_seconds=1))
    market = ResearchMarketData(budget, {}, transport=httpx.MockTransport(blocked))
    try:
        with pytest.raises(OutcomePassBudgetReached):
            await market.minute_candles_between(
                parent_fixture().cohort[0], NOW, NOW + timedelta(hours=24)
            )
    finally:
        await market.close()


async def test_response_journal_failure_cannot_be_swallowed_as_a_symbol_failure():
    async def handler(request):
        return httpx.Response(200, json=[])

    def cannot_persist(response):
        raise OSError("injected journal failure")

    market = ResearchMarketData(
        ShadowBudget(ShadowPolicy(max_attempts=30, deadline_seconds=90)),
        {"on_response": cannot_persist},
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(OutcomePassBudgetReached):
            await market.minute_candles_between(
                parent_fixture().cohort[0], NOW, NOW + timedelta(hours=24)
            )
    finally:
        await market.close()


async def test_external_cancellation_is_not_a_bounded_pass_stop():
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    market = ResearchMarketData(
        ShadowBudget(ShadowPolicy(max_attempts=30, deadline_seconds=90)),
        {},
        transport=httpx.MockTransport(blocked),
    )
    entered.clear()
    pending = asyncio.create_task(
        market.minute_candles_between(parent_fixture().cohort[0], NOW, NOW + timedelta(hours=24))
    )
    await entered.wait()
    pending.cancel()
    try:
        with pytest.raises(asyncio.CancelledError) as caught:
            await pending
        assert not isinstance(caught.value, OutcomePassBudgetReached)
    finally:
        await market.close()
