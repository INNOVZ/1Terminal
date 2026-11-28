"""ALFA v3.7 Institutional Hardening End-to-End, Integration, Smoke & Performance Suite.

Verifies every P0, P1, and P2 remediation from the Red-Team Architecture & Trading Audit:
- P0-1: Live-exact contract-quantity OI (`sumOpenInterest`) calibration in `RS_IGN` & `resistance_absorption`
- P0-2: Disjoint `ON_RADAR` vs `WATCHLIST_COIL` lanes preventing passive-coil starvation
- P0-3: Triple-layer Discovery -> T+1h `radar_service`/`radar_lifecycle`/`auto_watchlist` parity (`oi_change_4h_pct`)
- P0-4: `BinanceHttpClient` UTC minute-rollover lock & zero-duplicate `quotes()` reuse across radar/watchlist
- P0-5: Mid-cap `$1.5M-$15M` overflow + `<= 14%` RS leaders in `activity_watch_selection`
- P1-1: Universal Stage-2 orderbook depth floor (`>= $25k`) blocking thin-book traps
- P1-2: True 200-bar `4H EMA50` + dead-coil drawdown eviction (`watchlist_dead_coil_drawdown`)
- P1-3: Sustained BTC bleed cascade detection & `dip_coil_igniting` tightening
- P1-4: 15m `KineticScoutService` publication metadata, base-asset dedup, episode freeze & audit persistence
- P1-5: `EXECUTABLE` structural trigger separation (`breakout_trigger > close`), explicit stops & real pillar scores
- P2-1: SQLite WAL mode + `busy_timeout` under concurrent readers/writers
- Policy: Reversal/climax shorts (`UTAD_DIVERGENCE_SHORT`) remain active when `bearish_continuation_enabled=False`
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.radar_service import reconcile_radar_cohort
from algotrader.config import Settings
from algotrader.domain.auto_watchlist import (
    AutoWatchStatus,
    build_auto_watch_episode,
    classify_radar_eviction,
    evaluate_auto_watch_episode,
)
from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    ScreenerMode,
    SetupLabel,
)
from algotrader.domain.discovery_selection import activity_watch_selection
from algotrader.domain.indicators import snapshot as compute_snapshot
from algotrader.domain.kinetic_readiness import (
    assess_kinetic_readiness,
    evaluate_v33_local_base_or_flag,
)
from algotrader.domain.models import (
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.radar_lifecycle import evaluate_radar_candidate_lifecycle
from algotrader.domain.resistance_absorption import compute_resistance_absorption

D = Decimal
T0 = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
DEF_START_PX = D("1.0000")
DEF_STEP_1H = D("0.0002")
DEF_JUMP_1H = D("0.045")
DEF_VOL_MULT = D("2.5")
DEF_TAKER_SHARE = D("0.62")
DEF_STEP_4H = D("0.0004")


def _inst(symbol: str = "SKYUSDT") -> Instrument:
    return Instrument(
        id=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol),
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        base_asset=symbol.removesuffix("USDT").removesuffix("USDC"),
        quote_asset="USDT",
        price_tick=D("0.0001"),
        quantity_step=D("1"),
        min_quantity=D("1"),
        min_notional=D("5"),
        trading=True,
    )


def _make_1h_series(
    *,
    count: int = 360,
    start_px: Decimal = DEF_START_PX,
    end_at: datetime = T0,
    step_pct: Decimal = DEF_STEP_1H,
    last_jump_pct: Decimal = DEF_JUMP_1H,
    last_vol_mult: Decimal = DEF_VOL_MULT,
    taker_share: Decimal = DEF_TAKER_SHARE,
) -> list[Candle]:
    bars: list[Candle] = []
    px = start_px
    for i in range(count):
        closed_at = end_at - timedelta(hours=(count - 1 - i))
        opened_at = closed_at - timedelta(hours=1)
        is_last = i == count - 1
        op = px
        cl = op * (D("1") + (last_jump_pct if is_last else step_pct))
        hi = max(op, cl) * (D("1.005") if is_last else D("1.004"))
        lo = min(op, cl) * D("0.996")
        vol = D("100000") * (last_vol_mult if is_last else D("1"))
        qv = vol * cl
        bars.append(
            Candle(
                opened_at=opened_at,
                closed_at=closed_at,
                open=op.quantize(D("0.0001")),
                high=hi.quantize(D("0.0001")),
                low=lo.quantize(D("0.0001")),
                close=cl.quantize(D("0.0001")),
                volume=vol,
                quote_volume=qv,
                taker_buy_volume=vol * taker_share,
                taker_buy_quote_volume=qv * taker_share,
                closed=True,
            )
        )
        px = cl
    return bars


def _make_4h_series(
    *,
    count: int = 200,
    start_px: Decimal = DEF_START_PX,
    end_at: datetime = T0,
    step_pct: Decimal = DEF_STEP_4H,
) -> list[Candle]:
    bars: list[Candle] = []
    px = start_px
    for i in range(count):
        closed_at = end_at - timedelta(hours=4 * (count - 1 - i))
        opened_at = closed_at - timedelta(hours=4)
        op = px
        cl = op * (D("1") + step_pct)
        hi = max(op, cl) * D("1.008")
        lo = min(op, cl) * D("0.992")
        bars.append(
            Candle(
                opened_at=opened_at,
                closed_at=closed_at,
                open=op.quantize(D("0.0001")),
                high=hi.quantize(D("0.0001")),
                low=lo.quantize(D("0.0001")),
                close=cl.quantize(D("0.0001")),
                volume=D("400000"),
                quote_volume=D("400000") * cl,
                taker_buy_volume=D("220000"),
                taker_buy_quote_volume=D("220000") * cl,
                closed=True,
            )
        )
        px = cl
    return bars


def test_p0_1_and_p1_5_live_exact_contract_qty_rs_ignition_and_resistance_absorption() -> None:
    """P0-1 + P1-5: Live contract-quantity OI calibration + EXECUTABLE structural hygiene."""
    inst = _inst("SKYUSDT")
    c1h = _make_1h_series(count=360, start_px=D("1.0000"), step_pct=D("0.0001"), last_jump_pct=D("0.055"), last_vol_mult=D("2.4"))
    c4h = _make_4h_series(count=200, start_px=D("0.9500"), step_pct=D("0.0004"))
    last_px = c1h[-1].close
    c4h[-1] = Candle(
        opened_at=c4h[-1].opened_at,
        closed_at=c4h[-1].closed_at,
        open=c4h[-1].open,
        high=max(c4h[-1].high, last_px * D("1.005")),
        low=min(c4h[-1].low, last_px * D("0.97")),
        close=last_px,
        volume=c4h[-1].volume,
        quote_volume=c4h[-1].quote_volume,
        closed=True,
    )
    snap_1h = compute_snapshot(c1h)
    quote = Quote(
        instrument_id=inst.id,
        bid=last_px * D("0.9996"),
        ask=last_px * D("1.0004"),
        last=last_px,
        quote_volume_24h=D("8500000"),
        high_24h=last_px * D("1.01"),
        low_24h=last_px * D("0.93"),
        price_change_pct_24h=D("5.5"),
        observed_at=T0,
    )
    # Exact live Binance contract-qty OI: +1.1% 4h contracts (would have FAILED v3.6's +4.5% USD threshold!)
    deriv_live = DerivativesSnapshot(
        funding_rate=D("0.00005"),
        basis_rate=D("0.0002"),
        open_interest=D("5000000"),
        open_interest_change_1h=D("0.004"),
        open_interest_change_4h=D("0.011"),
        open_interest_change_7d=D("-0.095"),
        taker_buy_sell_ratio=D("1.24"),
        taker_buy_sell_ratio_4h=D("1.22"),
        taker_buy_sell_ratio_24h=D("1.10"),
        bid_depth_notional=D("85000"),
        ask_depth_notional=D("85000"),
        observed_at=T0,
    )
    opp = evaluate_v33_local_base_or_flag(
        inst=inst,
        quote=quote,
        snap_1h=snap_1h,
        closed_1h=c1h,
        closed_4h=c4h,
        derivatives=deriv_live,
        macro_multiplier=D("1.0"),
        macro_narrative="Neutral",
        is_btc_cascade=False,
        beta=D("1.0"),
        residual_24h=D("0.05"),
        is_discovery_tier=False,
        evaluated_at=T0,
        btc_relative_6h_pp=D("5.2"),
    )
    assert opp is not None
    assert opp.publication_lane == "ON_RADAR"
    # P1-5: trigger strictly above close, explicit stops populated, non-flat score breakdown
    assert opp.breakout_trigger is not None and opp.breakout_trigger > opp.current_price
    assert opp.retest_stop == opp.suggested_stop
    assert opp.breakout_stop == opp.suggested_stop
    sb = opp.score_breakdown
    assert len({sb.trend_score, sb.volume_score, sb.momentum_score, sb.derivatives_score, sb.alpha_score, sb.structure_score}) >= 3

    # Negative guard: collapsing contract-qty OI (-1.5% 4h) must still be rejected
    deriv_bad = DerivativesSnapshot(
        funding_rate=D("0.00005"),
        basis_rate=D("0.0002"),
        open_interest=D("5000000"),
        open_interest_change_1h=D("-0.012"),
        open_interest_change_4h=D("-0.015"),
        open_interest_change_7d=D("-0.095"),
        taker_buy_sell_ratio=D("1.24"),
        taker_buy_sell_ratio_4h=D("1.22"),
        bid_depth_notional=D("85000"),
        ask_depth_notional=D("85000"),
        observed_at=T0,
    )
    assert (
        evaluate_v33_local_base_or_flag(
            inst=inst,
            quote=quote,
            snap_1h=snap_1h,
            closed_1h=c1h,
            closed_4h=c4h,
            derivatives=deriv_bad,
            macro_multiplier=D("1.0"),
            macro_narrative="Neutral",
            is_btc_cascade=False,
            beta=D("1.0"),
            residual_24h=D("0.05"),
            is_discovery_tier=False,
            evaluated_at=T0,
            btc_relative_6h_pp=D("5.2"),
        )
        is None
    )

    # Resistance absorption contract-qty inflection parity: 7d OI -11% exempted when 4h OI = +1.1% & taker_share = 0.54
    score_ok, status_ok, _ = compute_resistance_absorption(
        cvd_zscore_24h=D("1.1"),
        taker_buy_ratio_4h=D("1.18"),
        oi_delta_4h=D("0.011"),
        oi_delta_7d=D("-0.11"),
        funding_bps=D("0.5"),
        low_4h=c4h[-1].low,
        low_4h_prev=c4h[-2].low,
        low_4h_prev2=c4h[-3].low,
        close=last_px,
        ema20_1h=snap_1h.ema20,
        atr14_1h=snap_1h.atr14,
        ret_24h=D("0.05"),
        vpvr_bins=[],
        poc_bin_volume=D("100"),
        vah_30d=last_px * D("1.02"),
        high_30d=last_px * D("1.08"),
    )
    assert status_ok != "HIGH_REJECTION_RISK" and score_ok >= 35.0


@pytest.mark.asyncio
async def test_p0_3_triple_layer_discovery_to_t1h_retention_and_watchlist_parity() -> None:
    """P0-3: radar_service populates oi_change_4h_pct; SENTUSDT & RS_IGN survive T+1h 5/5 without split-brain eviction."""
    inst = _inst("SENTUSDT")
    c1h = _make_1h_series(count=360, start_px=D("0.0220"), end_at=T0 + timedelta(hours=1), step_pct=D("0.0001"), last_jump_pct=D("0.015"))
    c4h = _make_4h_series(count=200, start_px=D("0.0210"), end_at=T0 + timedelta(hours=1), step_pct=D("0.0003"))
    px = c1h[-1].close
    disc_px = c1h[-2].close

    class MockFutures:
        async def instruments(self) -> list[Instrument]:
            return [inst]

        async def quotes(self, _insts: list[Instrument]) -> list[Quote]:
            return [
                Quote(
                    instrument_id=inst.id,
                    bid=px * D("0.9996"),
                    ask=px * D("1.0004"),
                    last=px,
                    quote_volume_24h=D("12000000"),
                    observed_at=T0 + timedelta(hours=1),
                )
            ]

        async def candles(self, _inst: Instrument, interval: str, limit: int) -> list[Candle]:
            return (c1h if interval == "1h" else c4h)[-limit:]

        async def derivatives_snapshot(self, _inst: Instrument) -> DerivativesSnapshot:
            return DerivativesSnapshot(
                funding_rate=D("0.00005"),
                basis_rate=D("0.0001"),
                open_interest=D("2000000"),
                open_interest_change_1h=D("0.002"),
                open_interest_change_4h=D("0.012"),
                open_interest_change_7d=D("-0.106"),
                taker_buy_sell_ratio=D("1.14"),
                taker_buy_sell_ratio_4h=D("1.15"),
                bid_depth_notional=D("100000"),
                ask_depth_notional=D("100000"),
                observed_at=T0 + timedelta(hours=1),
            )

    prior_card: dict[str, Any] = {
        "symbol": "SENTUSDT",
        "base_asset": "SENT",
        "direction": "long",
        "setup_label": "COIL_SPRING_LONG",
        "execution_mode": "CEILING_BREAKOUT",
        "first_seen_at": T0.isoformat(),
        "evaluated_at": T0.isoformat(),
        "discovery_price": str(disc_px),
        "current_price": str(disc_px),
        "suggested_entry": str(disc_px),
        "breakout_trigger": str(disc_px * D("1.003")),
        "suggested_stop": str(disc_px * D("0.92")),
        "suggested_targets": [str(disc_px * D("1.10")), str(disc_px * D("1.20")), str(disc_px * D("1.35"))],
        "poc_price": str(disc_px * D("0.98")),
        "vah_price": str(disc_px * D("1.03")),
        "val_price": str(disc_px * D("0.94")),
        "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in (
            "Trend Baseline", "Anti-Chasing Validated", "Value Acceptance", "Volatility Compression", "Smart Money Absorption"
        )],
    }
    preloaded_q = {
        "SENTUSDT": Quote(
            instrument_id=inst.id,
            bid=px * D("0.9996"),
            ask=px * D("1.0004"),
            last=px,
            quote_volume_24h=D("12000000"),
            observed_at=T0 + timedelta(hours=1),
        )
    }
    quote_calls = 0

    class CountingFutures(MockFutures):
        async def quotes(self, insts: list[Instrument]) -> list[Quote]:
            nonlocal quote_calls
            quote_calls += 1
            return await super().quotes(insts)

    retained = await reconcile_radar_cohort(
        [prior_card],
        set(),
        CountingFutures(),
        now=T0 + timedelta(hours=1),
        preloaded_quotes=preloaded_q,
    )
    # P0-4: Zero duplicate bulk quotes() call when fresh preloaded_quotes supplied
    assert quote_calls == 0
    assert len(retained) == 1
    r0 = retained[0]
    assert "oi_change_4h_pct" in r0["radar_metrics"]
    assert r0["gates_passed"] == 5
    assert r0["failed_gates"] == []

    # Also verify AutoWatchlist classify_radar_eviction respects fresh 4h OI inflection
    ep = build_auto_watch_episode(prior_card, published_at=T0, scan_id="s1")
    assert ep is not None
    ev = {
        "state": "INVALIDATED",
        "reason": "radar_gates_degraded",
        "candidate": {
            **prior_card,
            "checklist": [
                *prior_card["checklist"][:4],
                {"title": "Smart Money Absorption", "passed": False, "status": "failed", "detail": "dip"},
            ],
            "radar_metrics": {
                "oi_change_7d_pct": "-10.5%",
                "oi_change_4h_pct": "+1.2%",
                "oi_change_1h_pct": "+0.2%",
                "taker_buy_sell_ratio": "1.12",
                "funding_rate_bps": "+0.50 bps",
                "volume_contraction_ratio": "0.85",
            },
        },
    }
    status, _reason, _summary, _chk = classify_radar_eviction(ep, event=ev, now=T0 + timedelta(hours=2))
    assert status is AutoWatchStatus.WATCHLIST


@pytest.mark.asyncio
async def test_p0_4_binance_client_utc_rollover_pacing_and_weight_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-4: BinanceHttpClient sleeps ONLY until UTC minute boundary (not 60s) and resets weight to 0."""
    slept: list[float] = []
    # Simulate clock at XX:01:55.0 UTC -> only 5.5s until next minute boundary!
    monkeypatch.setattr("algotrader.adapters.binance.client.time.time", lambda: 1700000095.0)

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr("algotrader.adapters.binance.client.asyncio.sleep", fake_sleep)

    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        hdr = "2200" if calls == 1 else "10"
        return httpx.Response(200, json={"ok": True}, headers={"x-mbx-used-weight-1m": hdr})

    client = BinanceHttpClient("https://fapi.binance.com", transport=httpx.MockTransport(handler))
    try:
        await client.request("GET", "/fapi/v1/time")
        assert client.used_weight_1m == 2200
        # Next 3 concurrent /fapi/ requests must coalesce under lock, sleep ~5.5s (NOT 60s!), and reset weight
        await asyncio.gather(
            client.request("GET", "/fapi/v1/time"),
            client.request("GET", "/fapi/v1/time"),
            client.request("GET", "/fapi/v1/time"),
        )
        assert len(slept) == 1
        assert 4.0 <= slept[0] <= 6.5
        assert client.used_weight_1m == 10
    finally:
        await client.close()


def test_p0_5_midcap_overflow_and_14pct_rs_leaders_in_activity_watch() -> None:
    """P0-5: Mid-cap $1.5M-$15M overflow and +9%..+14% RS leaders enter watch slots when enabled."""
    liquid = [_inst(f"MC{i:03d}USDT") for i in range(60)]
    quotes: dict[str, Quote] = {}
    for i, inst in enumerate(liquid):
        # Mix of $4M mid-cap overflow with +11.5% 24h RS and $800k micro-caps with +2% 24h
        is_midcap_leader = i < 15
        quotes[inst.id.symbol] = Quote(
            instrument_id=inst.id,
            bid=D("0.9995"),
            ask=D("1.0005"),
            last=D("1.0000"),
            quote_volume_24h=D("4000000") if is_midcap_leader else D("800000"),
            high_24h=D("1.01"),
            low_24h=D("0.89"),
            price_change_pct_24h=D("11.5") if is_midcap_leader else D("2.0"),
            observed_at=T0,
        )
    # Default legacy mode excludes >= $1.5M and > 9%
    legacy = activity_watch_selection([], liquid, quotes, 30, T0, max_watch_slots=20)
    assert all(quotes[i.id.symbol].quote_volume_24h < D("1500000") for i in legacy)

    # ALFA v3.7 mode captures mid-cap overflow AND +11.5% RS leaders!
    v37 = activity_watch_selection(
        [],
        liquid,
        quotes,
        45,
        T0,
        max_watch_slots=45,
        include_midcap_overflow=True,
        max_change_pct=D("14"),
    )
    selected_syms = {i.id.symbol for i in v37}
    assert len(v37) == 45
    assert any(f"MC{i:03d}USDT" in selected_syms for i in range(15))


def test_p1_2_watchlist_true_200bar_4h_ema50_and_magic_dead_coil_eviction() -> None:
    """P1-2: Watchlist uses true 200-bar 4H EMA50 and evicts bleeding MAGICUSDT-style dead coils."""
    ep = build_auto_watch_episode(
        {
            "symbol": "MAGICUSDT",
            "direction": "long",
            "setup_label": "COIL_SPRING_LONG",
            "execution_mode": "CEILING_BREAKOUT",
            "score": "85.0",
            "tick_size": "0.0001",
            "current_price": "0.0727",
            "suggested_entry": "0.0716",
            "breakout_trigger": "0.0755",
            "suggested_stop": "0.0648",  # -10.8% away
            "suggested_targets": ["0.0820", "0.0900", "0.1000"],
            "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in (
                "Trend Baseline", "Anti-Chasing Validated", "Value Acceptance", "Volatility Compression", "Smart Money Absorption"
            )],
        },
        published_at=T0 - timedelta(hours=10),
        scan_id="s-magic",
    )
    assert ep is not None
    ep = replace(ep, status=AutoWatchStatus.WATCHLIST)
    # Bleeds straight down to 0.0666 (-8.4%, peak_mfe < 0%) while still above stop (0.0648) and 4H EMA50 (0.0660)
    c4h_bull = _make_4h_series(count=200, start_px=D("0.0650"), end_at=T0, step_pct=D("0.0001"))
    c1h_bleed = [
        Candle(
            opened_at=T0 - timedelta(hours=10 - h),
            closed_at=T0 - timedelta(hours=9 - h),
            open=D("0.0725") - D("0.0006") * h,
            high=D("0.0725") - D("0.0005") * h,
            low=D("0.0718") - D("0.0006") * h,
            close=D("0.0719") - D("0.0006") * h,
            volume=D("10000"),
            quote_volume=D("700"),
            closed=True,
        )
        for h in range(10)
    ]
    res = evaluate_auto_watch_episode(
        ep,
        candles=c1h_bleed,
        candles_4h=c4h_bull,
        live_price=D("0.0666"),
        live_observed_at=T0,
        now=T0,
    )
    assert res.status is AutoWatchStatus.HARD_INVALIDATED
    assert res.resolution_reason == "watchlist_dead_coil_drawdown"

    # Healthy coil at -1.5% after 10h stays alive in WATCHLIST
    c1h_healthy = [
        Candle(
            opened_at=T0 - timedelta(hours=10 - h),
            closed_at=T0 - timedelta(hours=9 - h),
            open=D("0.0720"),
            high=D("0.0738"),
            low=D("0.0712"),
            close=D("0.0718"),
            volume=D("10000"),
            quote_volume=D("720"),
            closed=True,
        )
        for h in range(10)
    ]
    res_ok = evaluate_auto_watch_episode(
        ep,
        candles=c1h_healthy,
        candles_4h=c4h_bull,
        live_price=D("0.0716"),
        live_observed_at=T0,
        now=T0,
    )
    assert res_ok.status is AutoWatchStatus.WATCHLIST


def test_p1_3_btc_cascade_tightens_dip_coil_igniting() -> None:
    """P1-3: Sustained BTC bleed tightens `dip_coil_igniting` so marginal coils route to WATCHLIST_COIL."""
    inst = _inst("TRUSTUSDT")
    # Last 1h bar 4.5x volume puts 6h VA6 at ~1.58x (passes normal >=1.35x, fails BTC cascade >=1.80x!)
    c1h = _make_1h_series(count=60, start_px=D("1.0000"), last_jump_pct=D("0.01"), last_vol_mult=D("4.5"))
    px = c1h[-1].close
    opp = CryptoOpportunity(
        evaluated_at=T0,
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.EMERGING_TREND_UP,
        setup_label=SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG,
        score=D("82.0"),
        score_breakdown=CryptoScreenerScoreBreakdown(D("82"), D("82"), D("82"), D("82"), D("82"), D("82"), D("1.0"), D("82")),
        current_price=px,
        atr14=D("0.02"),
        suggested_entry=px,
        breakout_trigger=px * D("1.03"),
        suggested_stop=px * D("0.94"),
        suggested_targets=(px * D("1.08"), px * D("1.16"), px * D("1.24")),
        risk_reward_ratio=D("2.50"),
        btc_beta=D("1.0"),
        residual_return_24h=D("0.01"),
        funding_rate=D("0.0001"),
        oi_change_1h=D("0.001"),
        oi_change_7d=D("0.02"),
        taker_ratio=D("1.10"),
        taker_ratio_1h=D("1.06"),
        taker_ratio_4h=D("1.10"),
        poc_price=px * D("0.99"),
        vah_price=px * D("1.03"),
        val_price=px * D("0.95"),
        rationale=("RETEST_ONLY_WAIT_FOR_DIP",),
        macro_narrative="Neutral",
        checklist=(("A", True, "ok"),) * 5,
        checklist_verification=ChecklistVerification(True, True, True, True, True),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
        execution_mode="RETEST_ACCUMULATION",
    )
    deriv = DerivativesSnapshot(
        funding_rate=D("0.0001"),
        basis_rate=D("0.0001"),
        open_interest=D("1000000"),
        open_interest_change_1h=D("0.001"),
        open_interest_change_4h=D("0.003"),
        open_interest_change_7d=D("0.02"),
        taker_buy_sell_ratio=D("1.06"),
        taker_buy_sell_ratio_4h=D("1.10"),
        bid_depth_notional=D("100000"),
        ask_depth_notional=D("100000"),
        observed_at=T0,
    )
    # Without BTC cascade: va6 ~1.35+ & tk4=1.10 -> ON_RADAR
    kin_normal = assess_kinetic_readiness(opp, c1h, deriv, is_btc_cascade=False)
    # With BTC cascade active: requires va6 >= 1.80 & tk4 >= 1.15 & tk1 >= 1.12 -> safely staged to WATCHLIST_COIL!
    kin_cascade = assess_kinetic_readiness(opp, c1h, deriv, is_btc_cascade=True)
    assert kin_normal.target_lane == "ON_RADAR"
    assert kin_cascade.target_lane == "WATCHLIST_COIL"


@pytest.mark.asyncio
async def test_p2_1_and_p0_4_sqlite_wal_and_preloaded_watchlist_refresh(tmp_path: Path) -> None:
    """P2-1 + P0-4: AutoWatchlistService enables WAL mode and reuses preloaded quotes/1h/4h with 0 network calls."""
    db_path = tmp_path / "atlas_v37_wal.sqlite3"
    svc = AutoWatchlistService(db_path)
    # Verify WAL journal mode is active
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert str(mode).lower() == "wal"

    inst = _inst("SKYUSDT")
    c1h = _make_1h_series(count=36, start_px=D("1.0000"), end_at=T0, step_pct=D("0.0001"), last_jump_pct=D("0.005"))
    c4h = _make_4h_series(count=200, start_px=D("0.9800"), end_at=T0, step_pct=D("0.0002"))
    px = c1h[-1].close
    svc.enroll_pre_ignition_coil(
        {
            "symbol": "SKYUSDT",
            "direction": "long",
            "setup_label": "COIL_SPRING_LONG",
            "execution_mode": "CEILING_BREAKOUT",
            "score": "85.0",
            "tick_size": "0.0001",
            "current_price": str(px),
            "suggested_entry": str(px * D("0.99")),
            "breakout_trigger": str(px * D("1.03")),
            "suggested_stop": str(px * D("0.93")),
            "suggested_targets": [str(px * D("1.10")), str(px * D("1.20")), str(px * D("1.30"))],
            "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in (
                "Trend Baseline", "Anti-Chasing Validated", "Value Acceptance", "Volatility Compression", "Smart Money Absorption"
            )],
        },
        published_at=T0 - timedelta(hours=2),
        scan_id="scan-wal",
    )

    class FailOnNetworkMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            raise AssertionError("Should not call instruments()")

        async def quotes(self, _insts: Any) -> list[Quote]:
            raise AssertionError("Should not call redundant quotes() when preloaded_quotes is fresh!")

        async def candles(self, _inst: Any, _iv: str, _lim: int) -> list[Candle]:
            raise AssertionError("Should not call redundant candles() when preloaded 1h/4h are supplied!")

    pre_q = {
        "SKYUSDT": Quote(
            instrument_id=inst.id,
            bid=px * D("0.9996"),
            ask=px * D("1.0004"),
            last=px,
            quote_volume_24h=D("5000000"),
            observed_at=T0,
        )
    }
    eps = await svc.refresh_and_evaluate(
        FailOnNetworkMarket(),  # type: ignore[arg-type]
        [inst],
        force_refresh=True,
        internal_scheduler=True,
        now=T0,
        preloaded_quotes=pre_q,
        preloaded_candles_1h={"SKYUSDT": c1h},
        preloaded_candles_4h={"SKYUSDT": c4h},
    )
    assert len(eps) == 1
    assert eps[0].status is AutoWatchStatus.WATCHLIST


def test_alfa_v37_smoke_and_performance_budget() -> None:
    """Smoke + Performance: 50x lifecycle/watchlist/universe cycles complete well under 1.5s; reversal shorts stay active."""
    liquid = [_inst(f"PERF{i:03d}USDT") for i in range(400)]
    quotes = {
        inst.id.symbol: Quote(
            instrument_id=inst.id,
            bid=D("0.9995"),
            ask=D("1.0005"),
            last=D("1.0000"),
            quote_volume_24h=D(str(600000 + i * 50000)),
            high_24h=D("1.04"),
            low_24h=D("0.94"),
            price_change_pct_24h=D(str(i % 13)),
            observed_at=T0,
        )
        for i, inst in enumerate(liquid)
    }
    c1h = _make_1h_series(count=80, start_px=D("1.0000"), end_at=T0)
    c4h = _make_4h_series(count=200, start_px=D("0.9800"), end_at=T0)
    cand_payload = {
        "symbol": "SKYUSDT",
        "direction": "long",
        "setup_label": "MOMENTUM_EMERGING_LONG",
        "execution_mode": "EXECUTABLE",
        "first_seen_at": (T0 - timedelta(hours=2)).isoformat(),
        "evaluated_at": (T0 - timedelta(hours=2)).isoformat(),
        "discovery_price": str(c1h[-3].close),
        "current_price": str(c1h[-1].close),
        "suggested_entry": str(c1h[-3].close),
        "breakout_trigger": str(c1h[-3].close * D("1.003")),
        "suggested_stop": str(c1h[-3].close * D("0.92")),
        "suggested_targets": [str(c1h[-3].close * D("1.12")), str(c1h[-3].close * D("1.24")), str(c1h[-3].close * D("1.36"))],
        "poc_price": str(c1h[-3].close * D("0.98")),
        "vah_price": str(c1h[-3].close * D("1.02")),
        "val_price": str(c1h[-3].close * D("0.95")),
        "radar_metrics": {
            "bb_bandwidth_4h": "0.34",
            "oi_change_7d_pct": "-10.2%",
            "oi_change_4h_pct": "+1.4%",
            "oi_change_1h_pct": "+0.3%",
            "funding_rate_bps": "+0.80 bps",
            "taker_buy_sell_ratio": "1.18",
            "volume_contraction_ratio": "3.10",
        },
        "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in (
            "Trend Baseline", "Anti-Chasing Validated", "Value Acceptance", "Volatility Compression", "Smart Money Absorption"
        )],
    }
    ep = build_auto_watch_episode(cand_payload, published_at=T0 - timedelta(hours=2), scan_id="perf")
    assert ep is not None
    ep = replace(ep, status=AutoWatchStatus.WATCHLIST)
    candles_wire = [
        {"time": int(c.closed_at.timestamp()), "opened_time": int(c.opened_at.timestamp()),
         "open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)}
        for c in c1h
    ]

    t_start = time.perf_counter()
    for _ in range(25):
        sel = activity_watch_selection(
            liquid[:275], liquid, quotes, 320, T0, max_watch_slots=45, include_midcap_overflow=True, max_change_pct=D("14")
        )
        assert len(sel) == 320
        dec = evaluate_radar_candidate_lifecycle(
            cand_payload,
            current_price=c1h[-1].close,
            recent_candles_1h=candles_wire,
            ema50_4h=c4h[-1].close * D("0.98"),
            now=T0,
        )
        assert dec.retained is True and dec.gates_passed == 5
        up_ep = evaluate_auto_watch_episode(
            ep, candles=c1h, candles_4h=c4h, live_price=c1h[-1].close, live_observed_at=T0, now=T0
        )
        assert up_ep.status is AutoWatchStatus.WATCHLIST
    elapsed = time.perf_counter() - t_start
    assert elapsed < 1.5, f"ALFA v3.7 performance regression: {elapsed:.3f}s >= 1.5s"

    class _StaticMacro:
        async def get_context(self) -> Any:
            from algotrader.adapters.external.models import MacroSentimentContext

            return MacroSentimentContext(observed_at=T0)

    # Policy smoke: UTAD/climax reversal shorts remain enabled when bearish_continuation_enabled=False
    s_default = Settings(_env_file=None, bearish_continuation_enabled=False)
    scr = CryptoOpportunityScreener(s_default, macro_provider=_StaticMacro(), feature_contract="discovery_v2")
    assert scr._settings.bearish_continuation_enabled is False
    assert hasattr(scr, "_score_reversal_distribution_setup")


@pytest.mark.asyncio
async def test_p0_2_disjoint_on_radar_vs_watchlist_coil_lanes_prevent_starvation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-2: 15 high-scoring passive WATCHLIST_COIL setups must NEVER crowd out lower-scoring ON_RADAR breakouts."""
    btc = _inst("BTCUSDT")
    coils = [_inst(f"COIL{i:02d}USDT") for i in range(15)]
    radars = [_inst(f"RADAR{i:02d}USDT") for i in range(3)]
    all_insts = [btc, *coils, *radars]
    c1h_base = _make_1h_series(count=360, start_px=D("1.0000"), end_at=T0)
    c4h_base = _make_4h_series(count=200, start_px=D("0.9500"), end_at=T0)

    def _opp_for(inst: Instrument, lane: str, score: Decimal) -> CryptoOpportunity:
        px = D("1.0000")
        return CryptoOpportunity(
            evaluated_at=T0,
            instrument=inst,
            direction=Direction.LONG,
            regime=CryptoMarketRegime.EMERGING_TREND_UP,
            setup_label=SetupLabel.COIL_SPRING_LONG if lane == "WATCHLIST_COIL" else SetupLabel.MOMENTUM_EMERGING_LONG,
            score=score,
            score_breakdown=CryptoScreenerScoreBreakdown(score, score, score, score, score, score, D("1.0"), score),
            current_price=px,
            atr14=D("0.02"),
            suggested_entry=px,
            breakout_trigger=px * D("1.02"),
            suggested_stop=px * D("0.94"),
            suggested_targets=(px * D("1.08"), px * D("1.16"), px * D("1.24")),
            risk_reward_ratio=D("2.50"),
            btc_beta=D("1.0"),
            residual_return_24h=D("0.04"),
            funding_rate=D("0.00005"),
            oi_change_1h=D("0.005"),
            oi_change_7d=D("0.05"),
            taker_ratio=D("1.20"),
            taker_ratio_1h=D("1.20"),
            taker_ratio_4h=D("1.20"),
            poc_price=px * D("0.98"),
            vah_price=px * D("1.02"),
            val_price=px * D("0.95"),
            rationale=("Archetype test",),
            macro_narrative="Neutral",
            checklist=(("G1", True, "ok"), ("G2", True, "ok"), ("G3", True, "ok"), ("G4", True, "ok"), ("G5", True, "ok")),
            checklist_verification=ChecklistVerification(True, True, True, True, True),
            conviction=score,
            expected_value_r=D("0.85"),
            grade="Grade A",
            execution_mode="EXECUTABLE",
            publication_lane=lane,
        )

    class LaneMockMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return all_insts

        async def quotes(self, insts: list[Instrument]) -> list[Quote]:
            return [
                Quote(
                    instrument_id=i.id,
                    bid=D("0.9996"),
                    ask=D("1.0004"),
                    last=D("1.0000"),
                    quote_volume_24h=D("15000000"),
                    high_24h=D("1.02"),
                    low_24h=D("0.98"),
                    price_change_pct_24h=D("2.0"),
                    observed_at=T0,
                )
                for i in insts
            ]

        async def candles(self, _inst: Instrument, interval: str, limit: int) -> list[Candle]:
            return (c1h_base if interval == "1h" else c4h_base)[-limit:]

        async def derivatives_snapshot(self, _inst: Instrument) -> DerivativesSnapshot:
            return DerivativesSnapshot(
                funding_rate=D("0.00005"),
                basis_rate=D("0.0001"),
                open_interest=D("1000000"),
                open_interest_change_1h=D("0.005"),
                open_interest_change_4h=D("0.02"),
                open_interest_change_7d=D("0.05"),
                taker_buy_sell_ratio=D("1.20"),
                taker_buy_sell_ratio_4h=D("1.20"),
                bid_depth_notional=D("100000"),
                ask_depth_notional=D("100000"),
                observed_at=T0,
            )

    class _NoNetMacro:
        async def get_context(self) -> Any:
            from algotrader.adapters.external.models import MacroSentimentContext

            return MacroSentimentContext(observed_at=T0)

    settings = Settings(_env_file=None, kinetic_ignition_v33_enabled=True)
    scr = CryptoOpportunityScreener(settings, macro_provider=_NoNetMacro(), evaluated_at=T0, feature_contract="discovery_v2")

    async def fake_eval_single(
        self: CryptoOpportunityScreener,
        *,
        inst: Instrument,
        candle_cache: dict[str, Any] | None = None,
        explicit_derivatives: Any = None,
        **_kwargs: Any,
    ) -> list[CryptoOpportunity]:
        if inst.id.symbol == "BTCUSDT":
            return []
        if candle_cache is not None:
            candle_cache[inst.id.symbol] = (c1h_base, c4h_base, ())
        if inst.id.symbol.startswith("COIL"):
            return [_opp_for(inst, "WATCHLIST_COIL", D("94.0"))]
        return [_opp_for(inst, "ON_RADAR", D("80.0"))]

    monkeypatch.setattr(CryptoOpportunityScreener, "_evaluate_single", fake_eval_single)
    mkt = LaneMockMarket()
    out = await scr.screen(mkt, derivatives_port=mkt, mode=ScreenerMode.ALL, max_symbols=50, enforce_quality=True)
    radar_out = [o for o in out if o.publication_lane == "ON_RADAR"]
    coil_out = [o for o in out if o.publication_lane == "WATCHLIST_COIL"]
    # ALL 3 lower-scoring ON_RADAR breakouts MUST survive despite 15 higher-scoring (94.0) WATCHLIST_COIL setups!
    assert {o.instrument.id.symbol for o in radar_out} == {"RADAR00USDT", "RADAR01USDT", "RADAR02USDT"}
    assert len(coil_out) == 15


@pytest.mark.asyncio
async def test_p1_1_universal_stage2_orderbook_depth_floor_blocks_thin_books() -> None:
    """P1-1: Stage 2 rejects thin-book (< $25k depth) candidates on standard accumulation paths."""
    btc = _inst("BTCUSDT")
    thin = _inst("THINUSDT")
    c1h = _make_1h_series(count=360, start_px=D("1.0000"), end_at=T0, step_pct=D("0.0001"), last_jump_pct=D("0.055"), last_vol_mult=D("2.4"))
    c4h = _make_4h_series(count=200, start_px=D("0.9500"), end_at=T0, step_pct=D("0.0004"))
    px = c1h[-1].close
    c4h[-1] = Candle(
        opened_at=c4h[-1].opened_at,
        closed_at=c4h[-1].closed_at,
        open=c4h[-1].open,
        high=max(c4h[-1].high, px * D("1.005")),
        low=min(c4h[-1].low, px * D("0.97")),
        close=px,
        volume=c4h[-1].volume,
        quote_volume=c4h[-1].quote_volume,
        closed=True,
    )
    quote = Quote(
        instrument_id=thin.id,
        bid=px * D("0.9996"),
        ask=px * D("1.0004"),
        last=px,
        quote_volume_24h=D("5000000"),
        high_24h=px * D("1.01"),
        low_24h=px * D("0.93"),
        price_change_pct_24h=D("5.5"),
        observed_at=T0,
    )
    thin_deriv = DerivativesSnapshot(
        funding_rate=D("0.00005"),
        basis_rate=D("0.0002"),
        open_interest=D("1000000"),
        open_interest_change_1h=D("0.005"),
        open_interest_change_4h=D("0.020"),
        open_interest_change_7d=D("0.080"),
        taker_buy_sell_ratio=D("1.25"),
        taker_buy_sell_ratio_4h=D("1.25"),
        bid_depth_notional=D("8000"),  # Thin $8k orderbook!
        ask_depth_notional=D("8000"),
        observed_at=T0,
    )
    class _NoNetMacro:
        async def get_context(self) -> Any:
            from algotrader.adapters.external.models import MacroSentimentContext

            return MacroSentimentContext(observed_at=T0)

    settings = Settings(_env_file=None, kinetic_ignition_v33_enabled=True)
    scr = CryptoOpportunityScreener(settings, macro_provider=_NoNetMacro(), evaluated_at=T0, feature_contract="discovery_v2")
    from algotrader.adapters.external.models import MacroSentimentContext

    class DummyMkt:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return [btc, thin]

        async def quotes(self, _i: Any) -> list[Quote]:
            return [quote]

        async def candles(self, _i: Any, iv: str, lim: int) -> list[Candle]:
            return (c1h if iv == "1h" else c4h)[-lim:]

    res = await scr._evaluate_single(
        inst=thin,
        quote=quote,
        market=DummyMkt(),
        derivatives_port=None,
        btc_candles=c1h,
        macro_context=MacroSentimentContext(observed_at=T0),
        mode=ScreenerMode.ALL,
        target_direction=Direction.LONG,
        is_btc_cascade=False,
        btc_ret_1h=D("0"),
        min_score=D("50"),
        is_discovery_tier=False,
        preloaded_candles=(c1h, c4h, ()),
        explicit_derivatives=thin_deriv,
    )
    assert res == []


@pytest.mark.asyncio
async def test_p1_4_merge_scout_promotions_preserves_episode_and_dedups_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """P1-4: _merge_scout_promotions enforces base-asset dedup, preserves same_radar_episode, and writes audit events."""
    from algotrader.api import portal

    monkeypatch.chdir(tmp_path)
    existing_sky = {
        "symbol": "SKYUSDT",
        "base_asset": "SKY",
        "direction": "long",
        "setup_label": "MOMENTUM_EMERGING_LONG",
        "suggested_entry": "0.0500",
        "suggested_stop": "0.0460",
        "first_seen_at": (T0 - timedelta(hours=1)).isoformat(),
        "radar_origin": "retained",
    }
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {"opportunities": [existing_sky], "qualifying_count": 1, "fresh_count": 0, "retained_count": 1},
    )
    monkeypatch.setattr(portal, "_episode_roster", [existing_sky])

    # Duplicate thesis on SKYUSDT (must NOT clobber frozen existing_sky!) + fresh ACTUSDT + duplicate base ACTUSDC
    promoted = [
        {
            "symbol": "SKYUSDT",
            "base_asset": "SKY",
            "direction": "long",
            "setup_label": "MOMENTUM_EMERGING_LONG",
            "suggested_entry": "0.0520",
            "suggested_stop": "0.0480",
            "first_seen_at": T0.isoformat(),
            "radar_origin": "fresh",
        },
        {
            "symbol": "ACTUSDT",
            "base_asset": "ACT",
            "direction": "long",
            "setup_label": "MOMENTUM_EMERGING_LONG",
            "suggested_entry": "0.2100",
            "suggested_stop": "0.1950",
            "first_seen_at": T0.isoformat(),
            "radar_origin": "fresh",
        },
        {
            "symbol": "ACTUSDC",
            "base_asset": "ACT",
            "direction": "long",
            "setup_label": "MOMENTUM_EMERGING_LONG",
            "suggested_entry": "0.2105",
            "suggested_stop": "0.1955",
            "first_seen_at": T0.isoformat(),
            "radar_origin": "fresh",
        },
    ]
    await portal._merge_scout_promotions(promoted, T0, "scout-test-1")
    opps = portal._screener_cache["opportunities"]
    syms = [o["symbol"] for o in opps]
    assert syms == ["ACTUSDT", "SKYUSDT"]
    # Frozen SKYUSDT entry price preserved!
    sky_after = next(o for o in opps if o["symbol"] == "SKYUSDT")
    assert sky_after["suggested_entry"] == "0.0500"
    # Audit event file written!
    event_files = list((tmp_path / "data" / "radar_events").glob("*.json"))
    assert len(event_files) == 1

