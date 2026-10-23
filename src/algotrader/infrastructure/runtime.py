"""Composition root for public market data and screening only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity, ScreenerMode
from algotrader.domain.models import Direction

SPOT_LIVE_URL = "https://api.binance.com"
FUTURES_LIVE_URL = "https://fapi.binance.com"


@dataclass(slots=True)
class Runtime:
    settings: Settings
    clients: tuple[BinanceHttpClient, ...]
    spot_market: BinanceSpotAdapter
    futures_market: BinanceUsdmAdapter
    macro_provider: MacroSentimentProvider
    last_scan_counts: dict[str, int] = field(default_factory=dict)
    _closed: bool = False

    async def crypto_screen(
        self,
        *,
        settings: Settings | None = None,
        min_score: Decimal | None = None,
        target_direction: Direction | None = None,
        top_n: int | None = None,
        max_symbols: int | None = None,
        mode: ScreenerMode = ScreenerMode.ACCUMULATION,
        coi_score: float | Decimal | None = None,
    ) -> list[CryptoOpportunity]:
        self.last_scan_counts = {}
        screener = CryptoOpportunityScreener(settings or self.settings, self.macro_provider)
        opportunities = await screener.screen(
            self.futures_market,
            derivatives_port=self.futures_market,
            min_score=min_score,
            target_direction=target_direction,
            top_n=top_n,
            max_symbols=max_symbols,
            mode=mode,
            coi_score=coi_score,
        )
        self.last_scan_counts = dict(screener.scan_counts)
        return opportunities

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        results = await asyncio.gather(
            *(client.close() for client in self.clients),
            self.macro_provider.close(),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise RuntimeError("market-data client cleanup failed") from errors[0]

    async def readiness(self) -> tuple[bool, dict[str, object]]:
        return not self._closed, {
            "status": "not_ready" if self._closed else "ready",
            "mode": "screener",
            "order_execution_enabled": False,
        }


async def build_runtime(settings: Settings) -> Runtime:
    """Build without credentials, account probes, brokers, or trading schedulers."""
    spot_client = BinanceHttpClient(SPOT_LIVE_URL)
    futures_client = BinanceHttpClient(FUTURES_LIVE_URL)
    return Runtime(
        settings=settings,
        clients=(spot_client, futures_client),
        spot_market=BinanceSpotAdapter(spot_client),
        futures_market=BinanceUsdmAdapter(futures_client),
        macro_provider=MacroSentimentProvider(),
    )
