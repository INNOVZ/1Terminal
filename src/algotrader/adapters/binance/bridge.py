"""Real market data with testnet tradability, for testnet execution.

Binance Spot Testnet serves roughly five days of 4h klines (29 candles against the 480 the
screener requires), so screening fails outright there. Its books and prices are also
synthetic: even where enough history exists, a signal computed on testnet prices describes a
market that does not exist, so a testnet run would validate nothing about the strategy.

``MarketDataPort`` and ``ExecutionPort`` are already separate protocols; only the runtime
wiring conflated them by handing the same adapter to both. This bridge takes them apart:

===================  ==================  ====================================================
Concern              Source              Why
===================  ==================  ====================================================
``instruments``      execution venue     Authoritative for what can actually be traded, and
                                         carries the tick size, step size and min notional
                                         that decide whether an order is valid.
``quotes``           live public API     Real spread and price; testnet books are not real.
``candles``          live public API     Real history; testnet does not have enough.
===================  ==================  ====================================================

Only public, unauthenticated endpoints are used against the live venue -- no API key is ever
sent there, and no order can reach it, because this class does not implement ``ExecutionPort``.

Instruments are sourced from the execution venue rather than intersected with the live
universe on purpose: the screener would otherwise rank a symbol that cannot be ordered. A
symbol listed on testnet but absent from live simply returns no quote and is filtered out by
the screener's existing liquidity gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument, Quote, Venue
from algotrader.domain.ports import MarketDataPort


class LiveDataTestnetBridge:
    """Market data for a testnet execution venue, priced from the live public API."""

    venue: Venue

    def __init__(self, *, live: MarketDataPort, execution_venue: MarketDataPort) -> None:
        if live.venue is not execution_venue.venue:
            raise ValueError("market-data bridge requires both sources to describe the same venue")
        self._live = live
        self._execution_venue = execution_venue
        # Instrument identity must match the execution venue, so ids resolve to orderable
        # symbols rather than to the live listing they were priced from.
        self.venue = execution_venue.venue

    async def instruments(self) -> Sequence[Instrument]:
        return await self._execution_venue.instruments()

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return await self._live.quotes(instruments)

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        return await self._live.candles(instrument, interval, limit)

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        provider = getattr(self._live, "derivatives_snapshot", None)
        if provider is None:
            raise TypeError("live market-data source has no derivatives snapshot capability")
        return cast(DerivativesSnapshot, await provider(instrument))
