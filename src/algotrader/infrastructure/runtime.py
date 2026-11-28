"""Composition root for public market data and screening only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.application.scan_recording import capture_scan
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity, ScreenerMode
from algotrader.domain.models import Candle, Direction, Quote
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.scan_archive import LocalScanArchive

SPOT_LIVE_URL = "https://api.binance.com"
FUTURES_LIVE_URL = "https://fapi.binance.com"


@dataclass(frozen=True)
class ScreeningRun:
    opportunities: list[CryptoOpportunity]
    counts: dict[str, int]
    receipt: ScanReceipt
    stage0_quotes: dict[str, Quote] = field(default_factory=dict)
    stage1_candles_1h: dict[str, tuple[Candle, ...]] = field(default_factory=dict)
    stage1_candles_4h: dict[str, tuple[Candle, ...]] = field(default_factory=dict)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    clients: tuple[BinanceHttpClient, ...]
    spot_market: BinanceSpotAdapter
    futures_market: BinanceUsdmAdapter
    macro_provider: MacroSentimentProvider
    last_scan_counts: dict[str, int] = field(default_factory=dict)
    _canary_task: asyncio.Task[None] | None = field(default=None, repr=False, init=False)
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
        feature_contract: str = "legacy",
    ) -> ScreeningRun:
        self.last_scan_counts = {}
        effective = settings or self.settings
        captured = await capture_scan(self.futures_market, self.futures_market, self.macro_provider, effective,
            {"min_score": min_score, "target_direction": target_direction, "top_n": top_n,
             "max_symbols": max_symbols, "mode": mode, "coi_score": coi_score, "enforce_quality": True},
            feature_contract=feature_contract)
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
        if (effective.bearish_continuation_canary_enabled and receipt.status == "saved"
                and captured.record.status == "complete" and not self._closed):
            if self._canary_task is None or self._canary_task.done():
                self._canary_task = asyncio.create_task(self._continuation_canary(captured.record, effective))
            else:
                structlog.get_logger(__name__).warning("continuation_canary_skipped_busy")
        stage0_quotes: dict[str, Quote] = {}
        stage1_candles_1h: dict[str, tuple[Candle, ...]] = {}
        stage1_candles_4h: dict[str, tuple[Candle, ...]] = {}
        for call in captured.record.inputs:
            if call.error is not None or call.result is None:
                continue
            if call.method == "quotes" and isinstance(call.result, (list, tuple)):
                for q in call.result:
                    if isinstance(q, Quote):
                        stage0_quotes[q.instrument_id.symbol] = q
            elif call.method == "candles" and isinstance(call.result, (list, tuple)):
                inst_arg = call.arguments.get("instrument")
                interval_arg = call.arguments.get("interval")
                sym_arg = getattr(getattr(inst_arg, "id", None), "symbol", None)
                if isinstance(sym_arg, str) and sym_arg:
                    bars = tuple(c for c in call.result if isinstance(c, Candle))
                    if interval_arg == "1h" and len(bars) > len(stage1_candles_1h.get(sym_arg, ())):
                        stage1_candles_1h[sym_arg] = bars
                    elif interval_arg == "4h" and len(bars) > len(stage1_candles_4h.get(sym_arg, ())):
                        stage1_candles_4h[sym_arg] = bars
        return ScreeningRun(
            captured.opportunities,
            dict(captured.record.counts),
            receipt,
            stage0_quotes=stage0_quotes,
            stage1_candles_1h=stage1_candles_1h,
            stage1_candles_4h=stage1_candles_4h,
        )

    async def _continuation_canary(self, record: Any, settings: Settings) -> None:
        from algotrader.application.continuation_canary import evaluate_canary, write_canary
        try:
            report = await asyncio.to_thread(evaluate_canary, record,
                                            max_symbols=settings.continuation_canary_max_symbols,
                                            seconds=settings.continuation_canary_seconds)
            await asyncio.to_thread(write_canary, settings.research_archive_dir, report,
                                    keep=settings.continuation_canary_reports)
        except Exception as exc:
            structlog.get_logger(__name__).error("continuation_canary_failed", error=type(exc).__name__)

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
        if self._canary_task is not None:
            await asyncio.shield(self._canary_task)
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
