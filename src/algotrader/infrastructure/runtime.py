"""Composition root for public market data and screening only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

import structlog

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.application.scan_recording import capture_scan
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity, ScreenerMode
from algotrader.domain.models import Direction
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.scan_archive import LocalScanArchive

SPOT_LIVE_URL = "https://api.binance.com"
FUTURES_LIVE_URL = "https://fapi.binance.com"


@dataclass(frozen=True)
class ScreeningRun:
    opportunities: list[CryptoOpportunity]
    counts: dict[str, int]
    receipt: ScanReceipt


@dataclass(slots=True)
class Runtime:
    settings: Settings
    clients: tuple[BinanceHttpClient, ...]
    spot_market: BinanceSpotAdapter
    futures_market: BinanceUsdmAdapter
    macro_provider: MacroSentimentProvider
    last_scan_counts: dict[str, int] = field(default_factory=dict)
    _closed: bool = False

    async def run_crypto_scan(
        self,
        *,
        settings: Settings | None = None,
        min_score: Decimal | None = None,
        target_direction: Direction | None = None,
        top_n: int | None = None,
        max_symbols: int | None = None,
        mode: ScreenerMode = ScreenerMode.ACCUMULATION,
        coi_score: float | Decimal | None = None,
    ) -> ScreeningRun:
        self.last_scan_counts = {}
        effective = settings or self.settings
        captured = await capture_scan(self.futures_market, self.futures_market, self.macro_provider, effective,
            {"min_score": min_score, "target_direction": target_direction, "top_n": top_n,
             "max_symbols": max_symbols, "mode": mode, "coi_score": coi_score, "enforce_quality": True})
        self.last_scan_counts = dict(captured.record.counts)  # Compatibility only; callers use run.counts.
        try:
            receipt = await asyncio.to_thread(LocalScanArchive(effective.research_archive_dir).write_scan, captured.record)
            if captured.recording_error is not None:
                receipt = ScanReceipt(captured.record.scan_id, "unavailable", captured.recording_error)
                structlog.get_logger(__name__).error("scan_recording_incomplete", scan_id=receipt.scan_id, error=receipt.error)
        except Exception as exc:
            receipt = ScanReceipt(captured.record.scan_id, "unavailable", type(exc).__name__)
            structlog.get_logger(__name__).error("scan_archive_unavailable", scan_id=receipt.scan_id, error=receipt.error)
        if captured.failure is not None:
            structlog.get_logger(__name__).warning("scan_failed", scan_id=receipt.scan_id, archive_status=receipt.status)
            raise captured.failure
        return ScreeningRun(captured.opportunities, dict(captured.record.counts), receipt)

    async def crypto_screen(
        self, *, settings: Settings | None = None, min_score: Decimal | None = None,
        target_direction: Direction | None = None, top_n: int | None = None,
        max_symbols: int | None = None, mode: ScreenerMode = ScreenerMode.ACCUMULATION,
        coi_score: float | Decimal | None = None,
    ) -> list[CryptoOpportunity]:
        run = await self.run_crypto_scan(settings=settings, min_score=min_score, target_direction=target_direction,
            top_n=top_n, max_symbols=max_symbols, mode=mode, coi_score=coi_score)
        return run.opportunities

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
