"""Recorded live acquisition/decision boundary; offline replay consumes the same event."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from algotrader.application.scan_recording import CallTape
from algotrader.domain.models import Candle, Instrument, Quote
from algotrader.domain.ports import MarketDataPort


class ContinuationContext:
    def __init__(
        self, market: MarketDataPort, tape: CallTape, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.market, self.tape = market, tape
        self.clock = clock or (lambda: datetime.now(UTC))
        self.used = 0
        self.weight_remaining = 177

    def reset(self) -> None:
        self.used = 0
        self.weight_remaining = 177

    def set_budget(self, baseline_weight: int) -> None:
        self.weight_remaining = min(177, max(0, 2300 - baseline_weight))

    async def publication_time(self) -> datetime:
        async def read_clock() -> datetime:
            return self.clock()

        return cast(datetime, await self.tape.capture("continuation_publication_time", {}, read_clock))

    async def get(
        self,
        instrument: Instrument,
        quote: Quote,
        hourly: Sequence[Candle],
        four_hour: Sequence[Candle],
    ) -> dict[str, Any]:
        if self.used >= 3:
            raise ValueError("continuation refresh capacity exceeded")
        self.used += 1

        async def acquire() -> dict[str, Any]:
            current = self.clock()
            requests, weight = 0, 0

            def reserve(cost: int) -> None:
                nonlocal requests, weight
                if self.weight_remaining < cost:
                    raise ValueError("continuation refresh weight budget exhausted")
                self.weight_remaining -= cost
                requests += 1
                weight += cost

            try:
                h, q = tuple(hourly), tuple(four_hour)
                live_quote = quote
                if not quote.is_valid_book or not quote.is_fresh_at(
                    current, max_age_seconds=60.0, max_future_seconds=5.0
                ):
                    reserve(55)
                    quotes = await asyncio.wait_for(self.market.quotes((instrument,)), timeout=5)
                    live_quote = next(v for v in quotes if v.instrument_id == instrument.id)
                current = self.clock()
                for hours in (1, 4):
                    bars = h if hours == 1 else q
                    cutoff = int(current.timestamp()) // (hours * 3600)
                    latest = max((c.closed_at.timestamp() for c in bars if c.closed), default=0)
                    if int(latest + 0.001) // (hours * 3600) < cutoff:
                        reserve(2)
                        fresh = tuple(
                            await asyncio.wait_for(
                                self.market.candles(
                                    instrument, f"{hours}h", 360 if hours == 1 else 480
                                ),
                                timeout=5,
                            )
                        )
                        if hours == 1:
                            h = fresh
                        else:
                            q = fresh
                decision = self.clock()
                return {
                    "status": "current",
                    "decision_at": decision,
                    "quote": live_quote,
                    "hourly": h,
                    "four_hour": q,
                    "refresh_requests": requests,
                    "refresh_weight": weight,
                }
            except Exception as exc:
                return {
                    "status": "unavailable",
                    "error_type": type(exc).__name__,
                    "decision_at": self.clock(),
                    "refresh_requests": requests,
                    "refresh_weight": weight,
                }

        return cast(
            dict[str, Any],
            await self.tape.capture("continuation_context", {"instrument": instrument}, acquire),
        )


class ReplayContinuationContext:
    def __init__(self, tape: Any) -> None:
        self.tape = tape

    async def publication_time(self) -> datetime:
        return cast(datetime, self.tape.take("continuation_publication_time", {}))

    async def get(
        self,
        instrument: Instrument,
        quote: Quote,
        hourly: Sequence[Candle],
        four_hour: Sequence[Candle],
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any], self.tape.take("continuation_context", {"instrument": instrument})
        )
