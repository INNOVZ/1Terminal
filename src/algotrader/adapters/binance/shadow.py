"""Child-owned public market adapter with actual-transport attempt accounting."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from algotrader.adapters.binance.base import BinanceMarketDataBase
from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.shadow_budget import ShadowBudget, ShadowBudgetExceeded
from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument


class _BudgetedTransport(httpx.AsyncBaseTransport):
    def __init__(self, budget: ShadowBudget, inner: httpx.AsyncBaseTransport) -> None:
        self.budget, self.inner = budget, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.budget.before_attempt()
        try:
            async with asyncio.timeout(self.budget.remaining_seconds):
                response = await self.inner.handle_async_request(request)
        except TimeoutError as exc:
            raise ShadowBudgetExceeded("child deadline exhausted during HTTP attempt") from exc
        weight = response.headers.get("x-mbx-used-weight-1m")
        if weight is not None:
            with contextlib.suppress(ValueError):
                self.budget.observed_weight_1m = int(weight)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class ShadowMarketData(BinanceUsdmAdapter):
    def __init__(
        self, budget: ShadowBudget, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.budget = budget
        self.client = BinanceHttpClient(
            "https://fapi.binance.com",
            transport=_BudgetedTransport(
                budget, transport if transport is not None else httpx.AsyncHTTPTransport()
            ),
        )
        self.snapshot_started_at: datetime | None = None
        self.snapshot_completed_at: datetime | None = None
        super().__init__(self.client)

    async def decision_tickers(self) -> list[dict[str, Any]]:
        self.snapshot_started_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(self.budget.remaining_seconds):
                rows = await self.client.request("GET", "/fapi/v1/ticker/24hr")
        except TimeoutError as exc:
            raise ShadowBudgetExceeded(
                "child deadline exhausted during ticker acquisition"
            ) from exc
        finally:
            self.snapshot_completed_at = datetime.now(UTC)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid whole-market ticker snapshot")
        return rows

    async def close(self) -> None:
        await self.client.close()

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        if interval == "1w":
            # Successful empty/short listing history is distinct from a failed HTTP request.
            # The runner validates nonempty weekly bars and records the core resampling fallback.
            return await BinanceMarketDataBase.candles(self, instrument, interval, limit)
        return await super().candles(instrument, interval, limit)

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        try:
            async with asyncio.timeout(self.budget.remaining_seconds):
                result = await super().derivatives_snapshot(instrument)
        except TimeoutError as exc:
            raise ShadowBudgetExceeded(
                "child deadline exhausted during derivatives acquisition"
            ) from exc
        except Exception as exc:
            if self.budget.exhausted_reason:
                raise ShadowBudgetExceeded(self.budget.exhausted_reason) from exc
            raise
        if self.budget.exhausted_reason:
            raise ShadowBudgetExceeded(self.budget.exhausted_reason)
        return result
