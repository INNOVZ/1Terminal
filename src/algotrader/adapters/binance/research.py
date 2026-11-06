"""Public research transport: observe throttling before retries; bound minute paths."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.shadow import ShadowMarketData, _BudgetedTransport
from algotrader.application.shadow_budget import ShadowBudget, ShadowBudgetExceeded
from algotrader.domain.models import Candle, Instrument


class OutcomePassBudgetReached(asyncio.CancelledError):
    """Controlled pass termination that the unchanged observer cannot swallow."""


class ResearchPassStopped(RuntimeError):
    """A response requires research cooldown, not another in-pass retry."""


class _ObservedTransport(_BudgetedTransport):
    def __init__(
        self, budget: ShadowBudget, inner: httpx.AsyncBaseTransport, metadata: dict[str, Any]
    ) -> None:
        super().__init__(budget, inner)
        self.metadata = metadata

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        observed = {
            "status_code": response.status_code,
            "retry_after": response.headers.get("Retry-After"),
            "observed_weight_1m": self.budget.observed_weight_1m,
        }
        self.metadata["last_response"] = observed
        callback = self.metadata.get("on_response")
        if callback is not None:
            try:
                callback(observed)
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await response.aclose()
                raise ResearchPassStopped("response_journal_failed") from exc
        if response.status_code in (418, 429) or (self.budget.observed_weight_1m or 0) >= 2150:
            await response.aclose()
            raise ResearchPassStopped("exchange_cooldown")
        return response


class ResearchMarketData(ShadowMarketData):
    def __init__(
        self,
        budget: ShadowBudget,
        metadata: dict[str, Any],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.budget = budget
        self.client = BinanceHttpClient(
            "https://fapi.binance.com",
            transport=_ObservedTransport(
                budget, transport if transport is not None else httpx.AsyncHTTPTransport(), metadata
            ),
        )
        self.snapshot_started_at = None
        self.snapshot_completed_at = None
        BinanceUsdmAdapter.__init__(self, self.client)

    async def minute_candles_between(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> Sequence[Candle]:
        try:
            async with asyncio.timeout(self.budget.remaining_seconds):
                return await super().minute_candles_between(instrument, start, end)
        except (ShadowBudgetExceeded, ResearchPassStopped, TimeoutError) as exc:
            raise OutcomePassBudgetReached(type(exc).__name__) from exc
