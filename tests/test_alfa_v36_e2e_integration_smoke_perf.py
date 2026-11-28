"""ALFA v3.6 End-to-End, Integration, Smoke, and Performance Verification Suite.

Covers all 6 systemic root-cause fixes validated in the Nov 28 forensic research:
  - Bug #1: Spaced T1 EV_R calibration (PORTALUSDT +360% grade/EV regression)
  - Bug #2: Decoupled conviction_floor vs score_floor (CFGUSDT +37.5% regression)
  - Bug #3: Fresh 4h OI inflection override in resistance absorption (SENTUSDT +15.5% regression)
  - Bug #4: Stage-1 L6 buyer-ignition parity + RS_IGN closing No-Man's-Land (BTW/SKY/IDOL)
  - Bug #5: Expanded 75-slot sub-$1.5M sleeper watch selection within Binance weight budget
  - Bug #6: High-churn spike rejection + live Watchlist 4H EMA50 & failed-breakout invalidation
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from algotrader.api.portal_app import create_app
from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import Settings
from algotrader.domain.auto_watchlist import (
    AutoWatchEpisode,
    AutoWatchStatus,
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
from algotrader.domain.execution_brackets import (
    build_long_bracket,
    extract_overhead_shelves_4h,
)
from algotrader.domain.kinetic_readiness import (
    is_actionable_watchlist_coil,
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
from algotrader.domain.resistance_absorption import compute_resistance_absorption
from algotrader.domain.screener_policy import (
    ALFA_V33_SCREENING_POLICY_VERSION,
    SCREENING_POLICY_VERSION,
    publication_rejection_reasons,
    screening_policy_version_for,
)
from algotrader.domain.scoring import (
    OpportunityGrade,
    classify_opportunity_grade,
    compute_3tier_ev_r,
)
from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

D = Decimal


def _make_inst(symbol: str, tick: str = "0.0001") -> Instrument:
    return Instrument(
        id=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol),
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        price_tick=D(tick),
        quantity_step=D("1"),
        min_quantity=D("1"),
        min_notional=D("5"),
        trading=True,
    )


def _make_1h_series(
    end_at: datetime,
    prices: list[float],
    *,
    qvol: float = 500_000.0,
    taker_buy_share: float = 0.55,
    wick_pct: float = 0.004,
) -> list[Candle]:
    bars: list[Candle] = []
    n = len(prices)
    for i, px in enumerate(prices):
        closed_at = end_at - timedelta(hours=(n - 1 - i))
        opened_at = closed_at - timedelta(hours=1)
        prev_px = prices[i - 1] if i > 0 else px * 0.998
        op = D(f"{prev_px:.6f}")
        cl = D(f"{px:.6f}")
        hi = max(op, cl) * (D("1") + D(f"{wick_pct:.5f}"))
        lo = min(op, cl) * (D("1") - D(f"{wick_pct:.5f}"))
        qv = D(f"{qvol:.2f}")
        vol = (qv / max(cl, D("1e-8"))).quantize(D("0.01"))
        tb_qv = (qv * D(f"{taker_buy_share:.4f}")).quantize(D("0.01"))
        bars.append(
            Candle(
                opened_at=opened_at,
                closed_at=closed_at,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
                quote_volume=qv,
                closed=True,
                taker_buy_volume=(vol * D(f"{taker_buy_share:.4f}")).quantize(D("0.01")),
                taker_buy_quote_volume=tb_qv,
            )
        )
    return bars


def _resample_4h_from_1h(bars_1h: list[Candle]) -> list[Candle]:
    out: list[Candle] = []
    for idx in range(0, len(bars_1h) - 3, 4):
        chunk = bars_1h[idx : idx + 4]
        out.append(
            Candle(
                opened_at=chunk[0].opened_at,
                closed_at=chunk[-1].closed_at,
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=sum((c.volume for c in chunk), D("0")),
                quote_volume=sum((c.quote_volume for c in chunk), D("0")),
                closed=True,
                taker_buy_volume=sum((c.taker_buy_volume for c in chunk), D("0")),
                taker_buy_quote_volume=sum((c.taker_buy_quote_volume for c in chunk), D("0")),
            )
        )
    return out


class _StaticMacro:
    class _Ctx:
        fear_greed_value = 35
        fear_greed_label = "Fear"
        btc_dominance = D("57.5")
        stablecoin_7d_change_pct = D("0.1")

        def narrative(self) -> str:
            return "Static test macro"

        def multiplier_for(self, direction: Direction) -> Decimal:
            return D("1.0")

    async def get_context(self) -> Any:
        return self._Ctx()


# ---------------------------------------------------------------------------
# 1. UNIT & REGRESSION TESTS (BUGS #1 - #6)
# ---------------------------------------------------------------------------


def test_bug1_spaced_t1_ev_r_calibration_preserves_portal_grade_b_and_rejects_weak_setup() -> None:
    """PORTALUSDT (+360%): realistic 1.0R/2.4R/3.8R ladder must achieve Grade B with spaced_t1_calibration=True."""
    ev_legacy = compute_3tier_ev_r(p_win=D("0.563"), r_t1=D("0.95"), r_t2=D("2.40"), r_t3=D("3.80"))
    ev_v36 = compute_3tier_ev_r(
        p_win=D("0.563"),
        r_t1=D("0.95"),
        r_t2=D("2.40"),
        r_t3=D("3.80"),
        spaced_t1_calibration=True,
    )
    assert ev_legacy < D("0.20")  # Reproduces legacy bug that killed PORTALUSDT
    assert ev_v36 >= D("0.25")
    assert (
        classify_opportunity_grade(
            ev_r=ev_v36,
            conviction=D("69.5"),
            asymmetric_rr=D("2.40"),
            checklist_passed=4,
        )
        is OpportunityGrade.GRADE_B
    )
    # Weak setup still rejected cleanly
    ev_weak = compute_3tier_ev_r(
        p_win=D("0.42"),
        r_t1=D("0.90"),
        r_t2=D("1.80"),
        r_t3=D("2.60"),
        spaced_t1_calibration=True,
    )
    assert ev_weak < D("0.20")
    assert (
        classify_opportunity_grade(
            ev_r=ev_weak,
            conviction=D("64.0"),
            asymmetric_rr=D("1.80"),
            checklist_passed=3,
        )
        is OpportunityGrade.BELOW_THRESHOLD
    )


def test_bug2_conviction_floor_decoupled_from_score_floor_for_cfg_style_setup() -> None:
    """CFGUSDT (+37.5%): score=75.5 (>=68) & conviction=67.5 (>=65) must NOT trigger score_floor."""
    inst = _make_inst("CFGUSDT")
    opp = CryptoOpportunity(
        evaluated_at=datetime(2026, 11, 27, 9, 0, tzinfo=UTC),
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.MOMENTUM_EMERGING_LONG,
        score=D("75.5"),
        score_breakdown=CryptoScreenerScoreBreakdown(
            D("75"), D("75"), D("75"), D("75"), D("75"), D("75"), D("1.00"), D("75.5")
        ),
        current_price=D("0.0550"),
        atr14=D("0.0020"),
        suggested_entry=D("0.0540"),
        breakout_trigger=D("0.0560"),
        suggested_stop=D("0.0500"),
        suggested_targets=(D("0.0600"), D("0.0652"), D("0.0700")),
        risk_reward_ratio=D("2.79"),
        risk_reward_target_index=2,
        btc_beta=D("1.10"),
        residual_return_24h=D("0.06"),
        taker_ratio=D("1.24"),
        rationale=("CFGUSDT breakout test",),
        macro_narrative="Neutral",
        conviction=D("67.5"),
        expected_value_r=D("0.27"),
        grade=OpportunityGrade.GRADE_B.value,
        execution_mode="RETEST_ACCUMULATION",
        checklist_verification=ChecklistVerification(True, True, False, True, True),
        taker_ratio_4h=D("1.24"),
        oi_change_7d=D("-0.024"),
        oi_change_1h=D("0.08"),
        funding_rate=D("0.00005"),
    )
    assert publication_rejection_reasons(opp) == ()
    # Below conviction_floor (64.9 < 65.0) is still rejected
    low_conv = replace(opp, conviction=D("64.9"))
    assert "conviction_floor" in publication_rejection_reasons(low_conv)
    # Below score_floor (67.9 < 68.0) is still rejected
    low_score = replace(opp, score=D("67.9"))
    assert "score_floor" in publication_rejection_reasons(low_score)


def test_bug3_resistance_absorption_fresh_4h_oi_inflection_overrides_stale_7d_oi_drain() -> None:
    """SENTUSDT (+15.5%): fresh 4h OI >= +2.5% + buyer taker >= 1.15 overrides stale 7d OI < -8%."""
    vpvr_bins = [(D("0.020"), D("20000")), (D("0.023"), D("4000")), (D("0.025"), D("3000"))]
    score, status, flags = compute_resistance_absorption(
        cvd_zscore_24h=0.65,
        taker_buy_ratio_4h=1.31,
        oi_delta_7d=-0.110,  # Stale 7d washout
        oi_delta_4h=0.039,  # Strong fresh 4h OI inflection!
        funding_bps=0.50,
        low_4h=D("0.0215"),
        low_4h_prev=D("0.0212"),
        low_4h_prev2=D("0.0210"),
        close=D("0.0225"),
        ema20_1h=D("0.0220"),
        atr14_1h=D("0.0006"),
        ret_24h=D("0.085"),
        vpvr_bins=vpvr_bins,
        vah_30d=D("0.0230"),
        high_30d=D("0.0250"),
        poc_bin_volume=D("20000"),
    )
    assert status == "BREAKOUT_CONFIRMED"
    assert score >= 65.0
    assert flags["pillar2_oi_expansion"] is True

    # Without fresh 4h OI inflection, stale 7d OI drain STILL triggers HIGH_REJECTION_RISK
    score_bad, status_bad, _ = compute_resistance_absorption(
        cvd_zscore_24h=0.10,
        taker_buy_ratio_4h=0.92,
        oi_delta_7d=-0.110,
        oi_delta_4h=-0.015,
        funding_bps=1.80,
        low_4h=D("0.0215"),
        low_4h_prev=D("0.0215"),
        low_4h_prev2=D("0.0215"),
        close=D("0.0225"),
        ema20_1h=D("0.0220"),
        atr14_1h=D("0.0006"),
        ret_24h=D("0.02"),
        vpvr_bins=vpvr_bins,
        vah_30d=D("0.0230"),
        high_30d=D("0.0250"),
        poc_bin_volume=D("20000"),
    )
    assert status_bad == "HIGH_REJECTION_RISK"
    assert score_bad <= 34.0


def test_bug4a_stage1_l6_admits_1h_buyer_ignition_before_4h_rvol_expands() -> None:
    """CFGUSDT T-1h: Stage-1 L6 must pass when taker_4h >= 1.12 and rvol_4h >= 0.90 under discovery_v2."""
    inp = LongGateInputs(
        close=D("0.0550"),
        ema50_4h=D("0.0530"),
        ema20_1h=D("0.0535"),
        ema200_4h=D("0.0540"),
        low_24h=D("0.0510"),
        atr14_4h=D("0.0020"),
        vcr_4h=D("1.21"),  # Volume expanding (not drying up)
        ema20_4h=D("0.0532"),
        excursion_72h=D("0.22"),
        ret_24h=D("0.075"),
        rsi_1h=D("64.0"),
        rsi_4h=D("59.8"),
        rsi_1d=D("35.0"),
        rsi_1w=D("30.0"),
        high_30d=D("0.0680"),
        low_30d=D("0.0450"),
        poc_30d=D("0.0525"),
        val_30d=D("0.0490"),
        vah_30d=D("0.0580"),
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=D("0.20"),
        bb_inside_kc_4h_lookback=(False,),
        rvol_4h=D("0.95"),  # 4H bar not yet >= 1.15
        oi_delta_7d=None,  # Stage 1 pre-derivatives
        oi_delta_4h=None,
        oi_delta_1h=None,
        taker_buy_ratio_4h=D("1.24"),
        discovery_v2=True,
    )
    passed, details = evaluate_long_gates(inp, SetupLabel.MOMENTUM_EMERGING_LONG)
    assert details["L6"] is True
    assert passed is True


def test_bug6a_watchlist_coil_rejects_high_volume_distribution_spike() -> None:
    """MAGICUSDT (va6=7.25x): explosive spike that failed ON_RADAR must NOT enter WATCHLIST_COIL."""
    magic_spike = {
        "current_price": "0.07269",
        "breakout_trigger": "0.07550",
        "volume_contraction_ratio": "1.10",
        "taker_buy_sell_ratio_1h": "1.01",
        "kinetic_reason": "retest_wait_routed_to_watchlist(va6=7.25)",
    }
    assert is_actionable_watchlist_coil(magic_spike) is False

    # Normal quiet coil (va6=0.95) remains eligible for Watchlist staging
    clean_coil = {
        "current_price": "0.07269",
        "breakout_trigger": "0.07550",
        "volume_contraction_ratio": "0.85",
        "taker_buy_sell_ratio_1h": "1.04",
        "kinetic_reason": "retest_wait_routed_to_watchlist(va6=0.95)",
    }
    assert is_actionable_watchlist_coil(clean_coil) is True


def test_bug6b_watchlist_live_4h_ema50_loss_and_failed_breakout_rejection_invalidate_episode() -> None:
    """EIGEN/OP/GPS Watchlist episodes breaking 4H EMA50 or rejecting breakout zone must HARD_INVALIDATE."""
    now = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
    pub = now - timedelta(hours=12)
    base_chk = (
        {"title": "Trend Baseline", "passed": True, "status": "passed", "detail": "Above 4H EMA50"},
        {"title": "Anti-Chasing Validated", "passed": True, "status": "passed", "detail": "Tight"},
        {"title": "Value Acceptance", "passed": True, "status": "passed", "detail": "Above POC"},
        {"title": "Volatility Compression", "passed": True, "status": "passed", "detail": "Squeeze"},
        {"title": "Smart Money Absorption", "passed": True, "status": "passed", "detail": "OK"},
    )

    # Case A: Breaks > 1.5% below 4H EMA50 (220 1H bars: steady at 1.00, then dumps to 0.94)
    prices_ema_break = [1.00] * 212 + [0.99, 0.98, 0.97, 0.96, 0.95, 0.945, 0.94, 0.94]
    bars_ema_break = _make_1h_series(now, prices_ema_break)
    ep_ema = AutoWatchEpisode(
        episode_id="ep-eigen",
        symbol="EIGENUSDT",
        direction="long",
        setup_label="MOMENTUM_EMERGING_LONG",
        execution_mode="RETEST_ACCUMULATION",
        score=D("85.0"),
        tick_size=D("0.0001"),
        published_at=pub,
        expires_at=pub + timedelta(hours=48),
        signal_price=D("1.00"),
        entry_price=D("0.98"),
        trigger_price=D("1.04"),
        stop_price=D("0.88"),
        target_1=D("1.15"),
        target_2=D("1.25"),
        target_3=D("1.35"),
        poc_price=D("0.99"),
        vah_price=D("1.05"),
        val_price=D("0.95"),
        status=AutoWatchStatus.WATCHLIST,
        radar_presence="EVICTED_FROM_RADAR",
        radar_status_label="PRE-IGNITION COIL",
        checklist=base_chk,
    )
    res_ema = evaluate_auto_watch_episode(
        ep_ema,
        candles=bars_ema_break,
        live_price=D("0.94"),
        live_observed_at=now,
        now=now,
    )
    assert res_ema.status is AutoWatchStatus.HARD_INVALIDATED
    assert res_ema.resolution_reason == "watchlist_trend_baseline_lost"
    assert res_ema.checklist[0]["passed"] is False
    assert res_ema.checklist[0]["status"] == "failed"

    # Case B: Reaches breakout trigger ($1.04), fails to graduate, then dumps >= 4.5% off peak & >= 3% below signal
    prices_rej = [1.00, 1.01, 1.02, 1.038, 1.01, 0.99, 0.975, 0.965, 0.962, 0.960, 0.960, 0.960]
    bars_rej = _make_1h_series(now, prices_rej, wick_pct=0.003)
    ep_rej = AutoWatchEpisode(
        episode_id="ep-gps",
        symbol="GPSUSDT",
        direction="long",
        setup_label="VCP_COMPRESSION_LONG",
        execution_mode="RETEST_ACCUMULATION",
        score=D("84.0"),
        tick_size=D("0.0001"),
        published_at=pub,
        expires_at=pub + timedelta(hours=48),
        signal_price=D("1.00"),
        entry_price=D("0.97"),
        trigger_price=D("1.04"),
        stop_price=D("0.88"),
        target_1=D("1.15"),
        target_2=D("1.25"),
        target_3=D("1.35"),
        poc_price=D("0.99"),
        vah_price=D("1.05"),
        val_price=D("0.95"),
        status=AutoWatchStatus.WATCHLIST,
        radar_presence="EVICTED_FROM_RADAR",
        radar_status_label="PRE-IGNITION COIL",
        checklist=base_chk,
    )
    res_rej = evaluate_auto_watch_episode(
        ep_rej,
        candles=bars_rej,
        live_price=D("0.960"),
        live_observed_at=now,
        now=now,
    )
    assert res_rej.status is AutoWatchStatus.HARD_INVALIDATED
    assert res_rej.resolution_reason == "failed_breakout_rejection"


# ---------------------------------------------------------------------------
# 2. END-TO-END INTEGRATION TEST (SCREENER -> KINETIC -> SQLITE AUTO-WATCHLIST)
# ---------------------------------------------------------------------------


def test_e2e_full_screener_to_radar_watchlist_and_invalidation_lifecycle(tmp_path: Path) -> None:
    """Full pipeline integration: RS_IGN gainer qualifies ON_RADAR while broken Watchlist coil is evicted."""
    now = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
    btc_inst = _make_inst("BTCUSDT", tick="0.1")
    btw_inst = _make_inst("BTWUSDT", tick="0.0001")
    dead_inst = _make_inst("DEADUSDT", tick="0.0001")

    # BTC: flat/slightly red (-1.5% over last 6h)
    btc_prices = [62000.0] * 354 + [61800.0, 61600.0, 61400.0, 61200.0, 61100.0, 61000.0]
    btc_1h = _make_1h_series(now, btc_prices, qvol=50_000_000.0, taker_buy_share=0.49)
    btc_4h = _resample_4h_from_1h(btc_1h)

    # BTWUSDT: Post-correction leader (earlier high 1.80 -> bottom 0.70 -> reclaiming above 4H EMA50 & 1H EMA20,
    # breaking 6h high with +5.5% 6h move vs red BTC = +7.0pp RS6, strong volume acceleration & buyer flow!)
    btw_prices = (
        [1.70 - 0.005 * i for i in range(180)]  # Prior downtrend so 4H EMA50 < 4H EMA200 (No-Man's-Land!)
        + [0.80 + 0.0012 * i for i in range(170)]  # Base recovery up to ~1.00
        + [0.99, 1.00, 1.005, 1.01, 1.015, 1.02, 1.03, 1.045, 1.06, 1.075]  # Fresh 6h breakout!
    )
    btw_1h_base = _make_1h_series(now, btw_prices, qvol=1_200_000.0, taker_buy_share=0.58)
    # Boost last 6h volume so VA6 >= 1.5x and last bar taker_1h >= 1.40
    btw_1h: list[Candle] = []
    for idx, c in enumerate(btw_1h_base):
        if idx >= len(btw_1h_base) - 6:
            qv = c.quote_volume * D("1.8")
            tb = qv * D("0.60")
            btw_1h.append(
                Candle(
                    opened_at=c.opened_at,
                    closed_at=c.closed_at,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=(qv / c.close).quantize(D("0.01")),
                    quote_volume=qv,
                    closed=True,
                    taker_buy_volume=(tb / c.close).quantize(D("0.01")),
                    taker_buy_quote_volume=tb,
                )
            )
        else:
            btw_1h.append(c)
    btw_4h = _resample_4h_from_1h(btw_1h)

    # DEADUSDT: downtrend losing 4H EMA50
    dead_prices = [1.00] * 340 + [0.99 - 0.005 * i for i in range(20)]
    dead_1h = _make_1h_series(now, dead_prices, qvol=2_000_000.0, taker_buy_share=0.45)
    dead_4h = _resample_4h_from_1h(dead_1h)

    candles_map = {
        ("BTCUSDT", "1h"): btc_1h,
        ("BTCUSDT", "4h"): btc_4h,
        ("BTWUSDT", "1h"): btw_1h,
        ("BTWUSDT", "4h"): btw_4h,
        ("DEADUSDT", "1h"): dead_1h,
        ("DEADUSDT", "4h"): dead_4h,
    }

    class _FakeMarket:
        venue = Venue.BINANCE_USDM

        async def instruments(self) -> list[Instrument]:
            return [btc_inst, btw_inst, dead_inst]

        async def quotes(self, instruments: list[Instrument]) -> list[Quote]:
            out: list[Quote] = []
            for inst in instruments:
                bars = candles_map[(inst.id.symbol, "1h")]
                px = bars[-1].close
                qv24 = sum((b.quote_volume for b in bars[-24:]), D("0"))
                chg24 = ((px - bars[-24].open) / bars[-24].open * D("100")).quantize(D("0.01"))
                out.append(
                    Quote(
                        instrument_id=inst.id,
                        bid=px * D("0.9998"),
                        ask=px * D("1.0002"),
                        last=px,
                        quote_volume_24h=qv24,
                        price_change_pct_24h=chg24,
                        observed_at=now,
                        exchange_observed_at=now,
                    )
                )
            return out

        async def candles(self, instrument: Instrument, interval: str, limit: int) -> list[Candle]:
            return candles_map.get((instrument.id.symbol, interval), [])[-limit:]

    class _FakeDeriv:
        async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
            if instrument.id.symbol == "BTWUSDT":
                return DerivativesSnapshot(
                    funding_rate=D("0.00005"),
                    basis_rate=D("0"),
                    open_interest=D("25000000"),
                    open_interest_change_1h=D("0.018"),
                    taker_buy_sell_ratio=D("1.42"),
                    bid_depth_notional=D("150000"),
                    ask_depth_notional=D("150000"),
                    open_interest_change_4h=D("0.068"),
                    open_interest_change_24h=D("0.15"),
                    open_interest_change_7d=D("0.45"),
                    taker_buy_sell_ratio_4h=D("1.22"),
                    taker_buy_sell_ratio_24h=D("1.12"),
                    observed_at=now,
                    degraded_telemetry=False,
                )
            return DerivativesSnapshot(
                funding_rate=D("0.00010"),
                basis_rate=D("0"),
                open_interest=D("5000000"),
                open_interest_change_1h=D("-0.01"),
                taker_buy_sell_ratio=D("0.80"),
                bid_depth_notional=D("100000"),
                ask_depth_notional=D("100000"),
                open_interest_change_4h=D("-0.03"),
                open_interest_change_24h=D("-0.05"),
                open_interest_change_7d=D("-0.12"),
                taker_buy_sell_ratio_4h=D("0.85"),
                taker_buy_sell_ratio_24h=D("0.88"),
                observed_at=now,
                degraded_telemetry=False,
            )

    settings = Settings(
        early_recovery_enabled=True,
        kinetic_ignition_v33_enabled=True,
        kinetic_scout_15m_enabled=True,
        bearish_continuation_enabled=False,
    )
    screener = CryptoOpportunityScreener(
        settings,
        _StaticMacro(),  # type: ignore[arg-type]
        evaluated_at=now,
        feature_contract="discovery_v2",
    )
    market = _FakeMarket()
    opps = asyncio.run(
        screener.screen(
            market,  # type: ignore[arg-type]
            derivatives_port=_FakeDeriv(),  # type: ignore[arg-type]
            mode=ScreenerMode.ALL,
            target_direction=Direction.LONG,
            max_symbols=10,
            enforce_quality=True,
        )
    )

    btw_opps = [o for o in opps if o.instrument.id.symbol == "BTWUSDT"]
    dead_opps = [o for o in opps if o.instrument.id.symbol == "DEADUSDT"]
    assert len(btw_opps) == 1
    assert len(dead_opps) == 0
    btw = btw_opps[0]
    assert btw.publication_lane == "ON_RADAR"
    assert btw.suggested_stop < btw.suggested_entry < btw.suggested_targets[0] < btw.suggested_targets[1] < btw.suggested_targets[2]
    assert (btw.suggested_entry - btw.suggested_stop) / btw.suggested_entry <= D("0.096")

    # Sync into real SQLite AutoWatchlistService + enroll a broken DEADUSDT Watchlist coil and refresh
    svc = AutoWatchlistService(db_path=tmp_path / "e2e_watch.sqlite3")
    svc.sync_from_scan(
        published_opportunities=[btw.as_dict()],
        radar_events=[],
        now=now,
        scan_id="scan-e2e-1",
    )
    svc.enroll_pre_ignition_coil(
        {
            "symbol": "DEADUSDT",
            "direction": "long",
            "setup_label": "VCP_COMPRESSION_LONG",
            "execution_mode": "RETEST_ACCUMULATION",
            "score": "82.0",
            "tick_size": "0.0001",
            "current_price": "0.99",
            "suggested_entry": "0.97",
            "breakout_trigger": "1.02",
            "suggested_stop": "0.80",
            "suggested_targets": ["1.10", "1.20", "1.30"],
            "poc_price": "0.99",
            "vah_price": "1.03",
            "val_price": "0.95",
            "checklist": [
                {"title": "Trend Baseline", "passed": True, "status": "passed", "detail": "OK"},
                {"title": "Anti-Chasing Validated", "passed": True, "status": "passed", "detail": "OK"},
                {"title": "Value Acceptance", "passed": True, "status": "passed", "detail": "OK"},
                {"title": "Volatility Compression", "passed": True, "status": "passed", "detail": "OK"},
                {"title": "Smart Money Absorption", "passed": True, "status": "passed", "detail": "OK"},
            ],
        },
        published_at=now - timedelta(hours=10),
        scan_id="scan-e2e-0",
    )

    asyncio.run(
        svc.refresh_and_evaluate(
            market,  # type: ignore[arg-type]
            [btc_inst, btw_inst, dead_inst],
            force_refresh=True,
            internal_scheduler=True,
            now=now,
        )
    )
    resp = svc.build_response(days=7, live_radar_symbols=["BTWUSDT"], now=now)
    # Disjointness invariant: BTWUSDT is ON_RADAR and NOT duplicated in Watchlist
    assert [e["symbol"] for e in resp["on_radar"]] == ["BTWUSDT"]
    assert all(e["symbol"] != "BTWUSDT" for e in resp["watchlist"])
    # DEADUSDT lost 4H EMA50 -> auto-invalidated out of Watchlist into resolved ledger!
    assert all(e["symbol"] != "DEADUSDT" for e in resp["watchlist"])
    assert any(
        e["symbol"] == "DEADUSDT" and e["resolution_reason"] == "watchlist_trend_baseline_lost"
        for e in resp["resolved"]
    )


# ---------------------------------------------------------------------------
# 3. SMOKE TESTS (PORTAL API & PURE SCREENER BOUNDARY)
# ---------------------------------------------------------------------------


def test_smoke_portal_readyz_and_policy_flag_defaults() -> None:
    """Smoke check: FastAPI /healthz & /readyz enforce pure screener boundary and flag-off defaults."""
    app = create_app(Settings(screener_cron_enabled=False))
    with TestClient(app) as client:
        h = client.get("/healthz")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        r = client.get("/readyz")
        assert r.status_code in (200, 503)  # 503 fail-closed when local cache is unseeded
        payload = r.json()
        assert payload["mode"] == "screener"
        assert payload["order_execution_enabled"] is False

    default_settings = Settings(
        early_recovery_enabled=False,
        kinetic_ignition_v33_enabled=False,
        kinetic_scout_15m_enabled=False,
    )
    assert screening_policy_version_for(default_settings) == SCREENING_POLICY_VERSION
    v36_settings = Settings(kinetic_ignition_v33_enabled=True)
    assert screening_policy_version_for(v36_settings) == ALFA_V33_SCREENING_POLICY_VERSION


# ---------------------------------------------------------------------------
# 4. PERFORMANCE & BINANCE RATE-LIMIT WEIGHT-BUDGET TESTS
# ---------------------------------------------------------------------------


def test_perf_500_symbol_bracket_gates_and_watchlist_throughput_and_weight_budget() -> None:
    """Performance & rate-limit test: 500 symbols evaluated in <2.5s and 75-slot watch stays within 2400/min."""
    now = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
    bars_1h = _make_1h_series(now, [1.00 + 0.0002 * (i % 15) for i in range(220)])
    bars_4h = _resample_4h_from_1h(bars_1h)
    vpvr_bins = [(D("0.98"), D("20000")), (D("1.02"), D("5000")), (D("1.08"), D("3000"))]

    ep = AutoWatchEpisode(
        episode_id="perf-ep",
        symbol="PERFUSDT",
        direction="long",
        setup_label="VCP_COMPRESSION_LONG",
        execution_mode="RETEST_ACCUMULATION",
        score=D("85.0"),
        tick_size=D("0.0001"),
        published_at=now - timedelta(hours=10),
        expires_at=now + timedelta(hours=38),
        signal_price=D("1.002"),
        entry_price=D("0.990"),
        trigger_price=D("1.030"),
        stop_price=D("0.930"),
        target_1=D("1.100"),
        target_2=D("1.180"),
        target_3=D("1.260"),
        poc_price=D("1.000"),
        vah_price=D("1.025"),
        val_price=D("0.980"),
        status=AutoWatchStatus.WATCHLIST,
        radar_presence="EVICTED_FROM_RADAR",
        radar_status_label="PRE-IGNITION COIL",
        checklist=(),
    )

    t0 = time.perf_counter()
    for _ in range(500):
        shelves = extract_overhead_shelves_4h(bars_4h, D("1.002"))
        brk = build_long_bracket(
            close=D("1.002"),
            ema50_4h=D("0.995"),
            poc_30d=D("1.000"),
            atr14_4h=D("0.015"),
            local_compression_high_4h=D("1.020"),
            swing_low_4h=D("0.960"),
            handle_low_4h=D("0.982"),
            high_30d=D("1.180"),
            vah_30d=D("1.025"),
            val_30d=D("0.980"),
            tick_size=D("0.0001"),
            use_confluence_trigger_v2=True,
            overhead_shelves=shelves,
        )
        assert brk.t2 > brk.t1 > brk.suggested_entry > brk.invalidation_stop
        compute_resistance_absorption(
            cvd_zscore_24h=0.5,
            taker_buy_ratio_4h=1.15,
            oi_delta_7d=0.05,
            oi_delta_4h=0.02,
            funding_bps=0.5,
            low_4h=D("0.99"),
            low_4h_prev=D("0.985"),
            low_4h_prev2=D("0.98"),
            close=D("1.002"),
            ema20_1h=D("1.000"),
            atr14_1h=D("0.008"),
            ret_24h=D("0.03"),
            vpvr_bins=vpvr_bins,
            vah_30d=D("1.025"),
            high_30d=D("1.180"),
            poc_bin_volume=D("20000"),
        )
    elapsed_core = time.perf_counter() - t0
    assert elapsed_core < 2.5, f"500-symbol bracket+absorption benchmark took {elapsed_core:.2f}s (> 2.5s)"

    # Benchmark 50 active Watchlist episodes with full 220-bar 4H EMA50 resampling
    t1 = time.perf_counter()
    for _ in range(50):
        res = evaluate_auto_watch_episode(
            ep,
            candles=bars_1h,
            live_price=D("1.002"),
            live_observed_at=now,
            now=now,
        )
        assert res.status is AutoWatchStatus.WATCHLIST
    elapsed_wl = time.perf_counter() - t1
    assert elapsed_wl < 1.0, f"50-episode Watchlist 4H EMA50 refresh took {elapsed_wl:.2f}s (> 1.0s)"

    # Verify 75-slot sub-$1.5M sleeper expansion stays bounded within Binance 2,400/min weight ceiling
    core_insts = [_make_inst(f"CORE{i}USDT") for i in range(250)]
    watch_insts = [_make_inst(f"SLEEP{i}USDT") for i in range(150)]
    quotes: dict[str, Quote] = {}
    for i, inst in enumerate(core_insts):
        quotes[inst.id.symbol] = Quote(
            instrument_id=inst.id,
            bid=D("1.000"),
            ask=D("1.001"),
            last=D("1.0005"),
            quote_volume_24h=D("5000000"),
            high_24h=D("1.05"),
            low_24h=D("0.95"),
            price_change_pct_24h=D("3.0"),
            observed_at=now,
        )
    for i, inst in enumerate(watch_insts):
        quotes[inst.id.symbol] = Quote(
            instrument_id=inst.id,
            bid=D("1.000"),
            ask=D("1.001"),
            last=D("1.0005"),
            quote_volume_24h=D("900000"),
            high_24h=D("1.04"),
            low_24h=D("0.96"),
            price_change_pct_24h=D("2.0"),
            observed_at=now,
        )
    selected = activity_watch_selection(
        core_insts,
        [*core_insts, *watch_insts],
        quotes,
        budget=350,
        at=now,
        max_watch_slots=75,
    )
    assert len(selected) == 325  # 250 core + 75 sleeper watch slots
    # Binance weight check: stage0 (56) + 325 * 4 (1h+4h klines) + 75 * 4 (stage2 probes) = 1656 <= 2400/min
    estimated_fapi_weight = 56 + len(selected) * 4 + 75 * 4
    assert estimated_fapi_weight <= 2000
