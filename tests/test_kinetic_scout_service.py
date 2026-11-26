"""Tests for Task 5: Rate-bounded :16/:31/:46 15-minute Kinetic Scout Service."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.kinetic_scout_service import KineticScoutService
from algotrader.config import Settings
from algotrader.domain.auto_watchlist import AutoWatchStatus
from algotrader.domain.models import (
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)


def _inst(symbol: str) -> Instrument:
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.0001"),
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        min_notional=Decimal("5"),
    )


def _series_15m(now: datetime, *, accel_mult: Decimal = Decimal("1.0"), gain_pct: Decimal = Decimal("0.0")) -> list[Candle]:
    q_end = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    bars: list[Candle] = []
    for i in range(96):
        opened = q_end - timedelta(minutes=15 * (96 - i))
        closed = opened + timedelta(minutes=15)
        is_recent = i >= 72
        qv = Decimal("50000") * (accel_mult if is_recent else Decimal("1.0"))
        base_px = Decimal("1.0000")
        if is_recent:
            step = Decimal(str((i - 71) / 24.0))
            cl = base_px * (Decimal("1") + gain_pct / Decimal("100") * step)
        else:
            cl = base_px
        bars.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=base_px if i == 72 else cl * Decimal("0.998"),
                high=cl * Decimal("1.003"),
                low=cl * Decimal("0.996"),
                close=cl,
                volume=qv / max(cl, Decimal("0.01")),
                quote_volume=qv,
                closed=True,
            )
        )
    return bars


@pytest.mark.asyncio
async def test_scout_skips_top_of_hour_window_with_zero_requests(tmp_path: Path) -> None:
    calls = 0

    class CountingMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            nonlocal calls
            calls += 1
            return []

        async def quotes(self, _insts: Any) -> list[Quote]:
            nonlocal calls
            calls += 1
            return []

        async def candles(self, _inst: Instrument, _iv: str, _lim: int) -> list[Candle]:
            nonlocal calls
            calls += 1
            return []

    svc = KineticScoutService(
        settings=Settings(_env_file=None, kinetic_ignition_v33_enabled=True, kinetic_scout_15m_enabled=True),
        market=CountingMarket(),
        derivatives=None,
        macro_provider=None,
        auto_watchlist=AutoWatchlistService(tmp_path / "state.sqlite3"),
        scan_lock=asyncio.Lock(),
    )
    res = await svc.run_quarter_hour_tick(now=datetime(2026, 11, 24, 12, 2, tzinfo=UTC))
    assert res["status"] == "skipped_hourly_window"
    assert calls == 0


@pytest.mark.asyncio
async def test_scout_stays_within_rate_budget_and_handles_degraded_derivatives_fail_closed(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 11, 24, 12, 16, 10, tzinfo=UTC)
    btc = _inst("BTCUSDT")
    coil = _inst("OUSDT")
    deriv_calls = 0

    watch_svc = AutoWatchlistService(tmp_path / "state.sqlite3")
    watch_svc.enroll_pre_ignition_coil(
        {
            "symbol": "OUSDT",
            "direction": "long",
            "setup_label": "VCP_COMPRESSION_LONG",
            "execution_mode": "RETEST_ACCUMULATION",
            "score": "86.0",
            "tick_size": "0.0001",
            "first_seen_at": (now - timedelta(hours=1)).isoformat(),
            "evaluated_at": (now - timedelta(hours=1)).isoformat(),
            "current_price": "1.0000",
            "suggested_entry": "1.0000",
            "breakout_trigger": "1.0200",
            "suggested_stop": "0.9500",
            "suggested_targets": ["1.1000", "1.1500", "1.2000"],
            "publication_lane": "WATCHLIST_COIL",
            "checklist": [
                {"title": "Trend Baseline", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Anti-Chasing Validated", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Value Acceptance", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Volatility Compression", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Smart Money Absorption", "passed": True, "status": "passed", "detail": "ok"},
            ],
        },
        published_at=now - timedelta(hours=1),
        scan_id="scan-prev",
    )

    class DegradedMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return [btc, coil]

        async def quotes(self, _insts: Any) -> list[Quote]:
            return [
                Quote(
                    instrument_id=btc.id,
                    bid=Decimal("90000"),
                    ask=Decimal("90001"),
                    last=Decimal("90000.5"),
                    quote_volume_24h=Decimal("500000000"),
                    high_24h=Decimal("91000"),
                    low_24h=Decimal("89000"),
                    price_change_pct_24h=Decimal("0.2"),
                    observed_at=now,
                ),
                Quote(
                    instrument_id=coil.id,
                    bid=Decimal("1.0395"),
                    ask=Decimal("1.0405"),
                    last=Decimal("1.0400"),
                    quote_volume_24h=Decimal("4000000"),
                    high_24h=Decimal("1.0450"),
                    low_24h=Decimal("0.9900"),
                    price_change_pct_24h=Decimal("4.2"),
                    observed_at=now,
                ),
            ]

        async def candles(self, inst: Instrument, iv: str, _lim: int) -> list[Candle]:
            if iv == "15m":
                if inst.id.symbol == "BTCUSDT":
                    return _series_15m(now, accel_mult=Decimal("1.0"), gain_pct=Decimal("0.1"))
                return _series_15m(now, accel_mult=Decimal("2.2"), gain_pct=Decimal("4.0"))
            return []

        async def derivatives_snapshot(self, _inst: Instrument) -> DerivativesSnapshot:
            nonlocal deriv_calls
            deriv_calls += 1
            return DerivativesSnapshot(
                funding_rate=Decimal("0.00005"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("1000000"),
                open_interest_change_1h=None,
                taker_buy_sell_ratio=None,
                bid_depth_notional=Decimal("50000"),
                ask_depth_notional=Decimal("50000"),
                degraded_telemetry=True,
                observed_at=now,
            )

    class DummyMacro:
        async def get_context(self) -> MacroSentimentContext:
            return MacroSentimentContext(observed_at=now)

    mkt = DegradedMarket()
    svc = KineticScoutService(
        settings=Settings(_env_file=None, kinetic_ignition_v33_enabled=True, kinetic_scout_15m_enabled=True),
        market=mkt,
        derivatives=mkt,
        macro_provider=DummyMacro(),
        auto_watchlist=watch_svc,
        scan_lock=asyncio.Lock(),
    )
    res = await svc.run_quarter_hour_tick(now=now)
    assert res["status"] == "ok"
    assert res["estimated_fapi_weight"] <= 300
    assert res["futures_data_calls"] <= 40
    assert deriv_calls <= 6
    # Because derivatives were degraded, OUSDT must remain safely in WATCHLIST (neither promoted nor invalidated!)
    eps = watch_svc.list_episodes(now=now)
    assert len(eps) == 1 and eps[0].status is AutoWatchStatus.WATCHLIST


@pytest.mark.asyncio
async def test_scout_slices_96_closed_bars_when_live_wire_includes_trailing_open_kline(
    tmp_path: Path,
) -> None:
    """Regression: live Binance `/fapi/v1/klines?interval=15m` appends 1 open candle (`closed=False`).

    Verify KineticScoutService requests `limit=97` (`weight=1`, `< 100`) and slices the 96 closed bars
    so `scout_features` evaluates `available=True` in production instead of failing `len < 96`.
    """
    now = datetime(2026, 11, 24, 12, 16, 10, tzinfo=UTC)
    btc = _inst("BTCUSDT")
    coil = _inst("VVVUSDT")
    requested_limits: list[int] = []

    watch_svc = AutoWatchlistService(tmp_path / "live_wire.sqlite3")
    watch_svc.enroll_pre_ignition_coil(
        {
            "symbol": "VVVUSDT",
            "direction": "long",
            "setup_label": "VCP_COMPRESSION_LONG",
            "execution_mode": "RETEST_ACCUMULATION",
            "score": "88.0",
            "tick_size": "0.0001",
            "first_seen_at": (now - timedelta(hours=2)).isoformat(),
            "evaluated_at": (now - timedelta(hours=1)).isoformat(),
            "current_price": "1.0000",
            "suggested_entry": "1.0000",
            "breakout_trigger": "1.0300",
            "suggested_stop": "0.9500",
            "suggested_targets": ["1.1000", "1.1500", "1.2200"],
            "publication_lane": "WATCHLIST_COIL",
            "checklist": [
                {"title": "Trend Baseline", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Anti-Chasing Validated", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Value Acceptance", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Volatility Compression", "passed": True, "status": "passed", "detail": "ok"},
                {"title": "Smart Money Absorption", "passed": True, "status": "passed", "detail": "ok"},
            ],
        },
        published_at=now - timedelta(hours=1),
        scan_id="scan-prev",
    )

    def _wire_96_closed_plus_1_open(accel_mult: Decimal, gain_pct: Decimal) -> list[Candle]:
        closed_96 = _series_15m(now, accel_mult=accel_mult, gain_pct=gain_pct)
        last_cl = closed_96[-1]
        open_bar = Candle(
            opened_at=last_cl.closed_at,
            closed_at=last_cl.closed_at + timedelta(minutes=15),
            open=last_cl.close,
            high=last_cl.close * Decimal("1.002"),
            low=last_cl.close * Decimal("0.999"),
            close=last_cl.close * Decimal("1.001"),
            volume=Decimal("1000"),
            quote_volume=Decimal("1000"),
            closed=False,
        )
        # If service requested 96, wire would only have 95 closed + 1 open! With limit=97, wire has 96 closed + 1 open.
        return [*closed_96, open_bar]

    class LiveWireMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return [btc, coil]

        async def quotes(self, _insts: Any) -> list[Quote]:
            return [
                Quote(
                    instrument_id=btc.id,
                    bid=Decimal("90000"),
                    ask=Decimal("90001"),
                    last=Decimal("90000.5"),
                    quote_volume_24h=Decimal("500000000"),
                    high_24h=Decimal("91000"),
                    low_24h=Decimal("89000"),
                    price_change_pct_24h=Decimal("0.2"),
                    observed_at=now,
                ),
                Quote(
                    instrument_id=coil.id,
                    bid=Decimal("1.0395"),
                    ask=Decimal("1.0405"),
                    last=Decimal("1.0400"),
                    quote_volume_24h=Decimal("8000000"),
                    high_24h=Decimal("1.0450"),
                    low_24h=Decimal("0.9900"),
                    price_change_pct_24h=Decimal("4.2"),
                    observed_at=now,
                ),
            ]

        async def candles(self, inst: Instrument, iv: str, lim: int) -> list[Candle]:
            if iv == "15m":
                requested_limits.append(lim)
                wire = (
                    _wire_96_closed_plus_1_open(Decimal("1.0"), Decimal("0.1"))
                    if inst.id.symbol == "BTCUSDT"
                    else _wire_96_closed_plus_1_open(Decimal("2.2"), Decimal("4.0"))
                )
                return wire[-lim:]
            return []

    svc = KineticScoutService(
        settings=Settings(_env_file=None, kinetic_ignition_v33_enabled=True, kinetic_scout_15m_enabled=True),
        market=LiveWireMarket(),
        derivatives=None,
        macro_provider=None,
        auto_watchlist=watch_svc,
        scan_lock=asyncio.Lock(),
    )
    res = await svc.run_quarter_hour_tick(now=now)
    assert res["status"] == "ok"
    assert requested_limits == [97, 97]
    assert res["scouted_count"] == 1
    assert res["ignited_count"] == 1


