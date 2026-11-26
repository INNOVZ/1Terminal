"""Tests for ALFA v3.3 kinetic readiness classification and Stage-1 trap fixes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.kinetic_readiness import (
    assess_kinetic_readiness,
    has_recent_bearish_divergence_below_support,
)
from algotrader.domain.models import (
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    Venue,
)
from algotrader.domain.scoring import OpportunityGrade

_INST = Instrument(
    id=InstrumentId(Venue.BINANCE_USDM, "TESTUSDT"),
    base_asset="TEST",
    quote_asset="USDT",
    asset_class=AssetClass.CRYPTO,
    market_kind=MarketKind.FUTURES,
    trading=True,
    price_tick=Decimal("0.0001"),
    quantity_step=Decimal("1"),
    min_quantity=Decimal("1"),
    min_notional=Decimal("5"),
)


def _make_1h_series(*, recent_vol_mult: Decimal = Decimal("1.0"), breakout_last: bool = False) -> list[Candle]:
    base_t = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    bars: list[Candle] = []
    for i in range(30):
        opened = base_t + timedelta(hours=i)
        closed = opened + timedelta(hours=1)
        is_recent = i >= 24
        qvol = Decimal("100000") * (recent_vol_mult if is_recent else Decimal("1.0"))
        vol = qvol / Decimal("1.0")
        if breakout_last and i == 29:
            o, h, low, c = Decimal("1.00"), Decimal("1.05"), Decimal("0.995"), Decimal("1.045")
        else:
            o, h, low, c = Decimal("1.00"), Decimal("1.01"), Decimal("0.99"), Decimal("1.00")
        bars.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=vol,
                quote_volume=qvol,
                closed=True,
            )
        )
    return bars


def _make_opp(*, wait_dip: bool, taker_4h: Decimal = Decimal("1.10")) -> CryptoOpportunity:
    status = "RETEST_ONLY_WAIT_FOR_DIP" if wait_dip else "BREAKOUT_CONFIRMED"
    score = Decimal("82.0")
    return CryptoOpportunity(
        instrument=_INST,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.VCP_COMPRESSION_LONG,
        score=score,
        score_breakdown=CryptoScreenerScoreBreakdown(
            score, score, score, score, score, score, Decimal("1.0"), score
        ),
        current_price=Decimal("1.00"),
        atr14=Decimal("0.02"),
        suggested_entry=Decimal("1.00"),
        suggested_stop=Decimal("0.96"),
        suggested_targets=(Decimal("1.08"), Decimal("1.12"), Decimal("1.16")),
        risk_reward_ratio=Decimal("2.5"),
        risk_reward_target_index=2,
        btc_beta=Decimal("1.0"),
        residual_return_24h=Decimal("0.02"),
        funding_rate=Decimal("0.00005"),
        oi_change_1h=Decimal("0.002"),
        oi_change_7d=Decimal("0.08"),
        taker_ratio=taker_4h,
        taker_ratio_4h=taker_4h,
        taker_ratio_window_hours=4,
        rationale=(
            "Archetype: VCP_COMPRESSION_LONG | Conviction: 76.0 | Grade: Grade B",
            f"Execution Mode: RETEST_ACCUMULATION | Resistance Absorption: 68.0/100 ({status})",
        ),
        macro_narrative="Neutral",
        checklist=(
            ("Trend Baseline", True, "ok"),
            ("Anti-Chasing Validated", True, "ok"),
            ("Value Acceptance", True, "ok"),
            ("Volatility Compression", True, "ok"),
            ("Smart Money Absorption", True, "ok"),
        ),
        checklist_verification=ChecklistVerification(True, True, True, True, True),
        conviction=Decimal("76.0"),
        expected_value_r=Decimal("0.45"),
        grade=OpportunityGrade.GRADE_B,
        execution_mode="RETEST_ACCUMULATION",
    )


def test_retest_wait_dip_without_kinetic_surge_routes_to_watchlist_coil() -> None:
    opp = _make_opp(wait_dip=True, taker_4h=Decimal("1.06"))
    bars = _make_1h_series(recent_vol_mult=Decimal("1.05"), breakout_last=False)
    res = assess_kinetic_readiness(opp, bars, None)
    assert res.is_radar_ignited is False
    assert res.target_lane == "WATCHLIST_COIL"


def test_confirmed_breakout_with_va6_expansion_routes_to_on_radar() -> None:
    opp = _make_opp(wait_dip=False, taker_4h=Decimal("1.12"))
    bars = _make_1h_series(recent_vol_mult=Decimal("1.50"), breakout_last=True)
    deriv = DerivativesSnapshot(
        funding_rate=Decimal("0.00005"),
        basis_rate=Decimal("0.0002"),
        open_interest=Decimal("5000000"),
        open_interest_change_1h=Decimal("0.008"),
        taker_buy_sell_ratio=Decimal("1.18"),
        bid_depth_notional=Decimal("80000"),
        ask_depth_notional=Decimal("80000"),
        open_interest_change_4h=Decimal("0.022"),
        open_interest_change_7d=Decimal("0.10"),
        taker_buy_sell_ratio_4h=Decimal("1.12"),
    )
    res = assess_kinetic_readiness(opp, bars, deriv)
    assert res.is_radar_ignited is True
    assert res.target_lane == "ON_RADAR"


def test_stale_bearish_divergence_16_bars_ago_does_not_block_fresh_reversal() -> None:
    base_t = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    # Build 26 4H bars where a bearish divergence happened around bar 6-10, followed by washout & flat base
    prices = [
        Decimal("1.00"), Decimal("1.05"), Decimal("1.15"), Decimal("1.08"), Decimal("1.18"),  # peak 2 higher price, lower momentum
        Decimal("1.02"), Decimal("0.92"), Decimal("0.85"), Decimal("0.82"), Decimal("0.80"),
        Decimal("0.79"), Decimal("0.79"), Decimal("0.80"), Decimal("0.80"), Decimal("0.81"),
        Decimal("0.81"), Decimal("0.81"), Decimal("0.82"), Decimal("0.82"), Decimal("0.82"),
        Decimal("0.82"), Decimal("0.83"), Decimal("0.83"), Decimal("0.84"), Decimal("0.85"),
        Decimal("0.86"),
    ]
    bars_4h = [
        Candle(
            opened_at=base_t + timedelta(hours=4 * idx),
            closed_at=base_t + timedelta(hours=4 * (idx + 1)),
            open=p * Decimal("0.99"),
            high=p * Decimal("1.01"),
            low=p * Decimal("0.98"),
            close=p,
            volume=Decimal("1000"),
            quote_volume=Decimal("1000") * p,
            closed=True,
        )
        for idx, p in enumerate(prices)
    ]
    assert has_recent_bearish_divergence_below_support(bars_4h, max_bars_ago=3) is False


def test_wait_dip_with_va6_velocity_or_smart_money_absorption_routes_to_on_radar_while_1h_selling_blocks() -> None:
    from dataclasses import replace

    # 1. STABLEUSDT-style: va6=2.82x velocity expansion even when latest bar consolidates
    opp_vel = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.15")),
        taker_ratio_1h=Decimal("1.21"),
        oi_change_7d=Decimal("0.247"),
        resistance_absorption_score=Decimal("68.0"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    bars_vel = _make_1h_series(recent_vol_mult=Decimal("2.82"), breakout_last=False)
    res_vel = assess_kinetic_readiness(opp_vel, bars_vel, None)
    assert res_vel.target_lane == "ON_RADAR"

    # 2. BLURUSDT-style: quiet coil (va6=0.60) with elite Smart-Money OI + 1H/4H taker absorption
    opp_sm = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.12")),
        taker_ratio_1h=Decimal("1.13"),
        oi_change_1h=Decimal("0.0067"),
        oi_change_7d=Decimal("0.537"),
        resistance_absorption_score=Decimal("83.0"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    bars_quiet = _make_1h_series(recent_vol_mult=Decimal("0.60"), breakout_last=False)
    res_sm = assess_kinetic_readiness(opp_sm, bars_quiet, None)
    assert res_sm.target_lane == "ON_RADAR"

    # 3. Nov 23 HMSTR-style dead grinder: CEILING_BREAKOUT but active 1H net selling (taker_1h=0.65) -> WATCHLIST_COIL
    opp_dump = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.10")),
        execution_mode="CEILING_BREAKOUT",
        taker_ratio_1h=Decimal("0.65"),
        oi_change_7d=Decimal("0.25"),
        resistance_absorption_score=Decimal("70.0"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    res_dump = assess_kinetic_readiness(opp_dump, bars_quiet, None)
    assert res_dump.target_lane == "WATCHLIST_COIL"

    # 4. Regression V3 (1000LUNCUSDT): when /futures/data/ 1H taker lags (None at :01) and va6=0.53,
    # closed_1h[-1] kline taker_buy_quote_volume fallback (0.9143) + va6<0.80 routes to WATCHLIST_COIL!
    bars_lunc = _make_1h_series(recent_vol_mult=Decimal("0.53"), breakout_last=False)
    bars_lunc[-1] = replace(
        bars_lunc[-1],
        quote_volume=Decimal("100000"),
        taker_buy_quote_volume=Decimal("47760"),  # 47760 / 52240 = 0.9142 (< 1.02 seller dominance)
    )
    opp_lunc = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.17")),
        execution_mode="CEILING_BREAKOUT",
        taker_ratio_1h=None,
        oi_change_1h=Decimal("0.011"),
        oi_change_7d=Decimal("0.182"),
        resistance_absorption_score=Decimal("68.0"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    res_lunc = assess_kinetic_readiness(opp_lunc, bars_lunc, None)
    assert res_lunc.target_lane == "WATCHLIST_COIL"


def test_heavy_1h_seller_dumping_blocks_on_radar_and_watchlist_even_with_high_va6() -> None:
    """USUALUSDT pre-crash regression: `va6=1.59, taker_4h=1.15, taker_1h=0.79` must NEVER enter ON_RADAR or WATCHLIST."""
    from dataclasses import replace

    from algotrader.domain.kinetic_readiness import is_actionable_watchlist_coil

    bars_usual = _make_1h_series(recent_vol_mult=Decimal("1.59"), breakout_last=False)
    opp_usual = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.155")),
        taker_ratio_1h=Decimal("0.7916"),
        oi_change_1h=Decimal("0.006"),
        oi_change_7d=Decimal("0.185"),
        poc_price=Decimal("0.92"),
        breakout_trigger=Decimal("1.001"),
        volume_contraction_ratio=Decimal("1.34"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    res = assess_kinetic_readiness(opp_usual, bars_usual, None)
    assert res.is_radar_ignited is False
    assert res.target_lane == "WATCHLIST_COIL"
    cand_dict = {
        **opp_usual.as_dict(),
        "publication_lane": res.target_lane,
        "kinetic_reason": res.reason,
    }
    assert is_actionable_watchlist_coil(cand_dict) is False


def test_unreachable_trigger_and_high_vcr_churn_filtered_while_tight_coil_passes() -> None:
    from dataclasses import replace

    from algotrader.domain.kinetic_readiness import is_actionable_watchlist_coil

    # ANIMEUSDT-style +34.1% unreachable breakout trigger must not enter ON_RADAR or WATCHLIST
    bars_surge = _make_1h_series(recent_vol_mult=Decimal("3.0"), breakout_last=True)
    opp_anime = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.12")),
        setup_label=SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
        taker_ratio_1h=Decimal("1.14"),
        funding_rate=Decimal("-0.0011"),
        oi_change_1h=Decimal("0.05"),
        oi_change_7d=Decimal("0.18"),
        current_price=Decimal("1.00"),
        breakout_trigger=Decimal("1.341"),
        breakout_readiness_status="RETEST_ONLY_WAIT_FOR_DIP",
    )
    res_anime = assess_kinetic_readiness(opp_anime, bars_surge, None)
    assert res_anime.target_lane == "WATCHLIST_COIL"
    assert is_actionable_watchlist_coil(opp_anime.as_dict()) is False

    # Tight coil (+4.5% trigger, VCR=0.82, healthy 1H taker) is actionable for WATCHLIST staging
    opp_good = replace(
        _make_opp(wait_dip=True, taker_4h=Decimal("1.07")),
        taker_ratio_1h=Decimal("1.04"),
        current_price=Decimal("1.00"),
        breakout_trigger=Decimal("1.045"),
        volume_contraction_ratio=Decimal("0.82"),
    )
    assert is_actionable_watchlist_coil(opp_good.as_dict()) is True


def test_breakout_long_stage1_and_stage2_parity_when_oi_4h_unknown() -> None:
    """AGENTS.md Invariant #2: Stage-1 (`oi_delta_4h=None`) and Stage-2 (`oi_delta_4h=+2%`) must both classify BREAKOUT_LONG."""
    from dataclasses import replace

    from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype

    f_stage1 = LongArchetypeFeatures(
        close=Decimal("1.05"),
        ema50_4h=Decimal("0.98"),
        poc_30d=Decimal("0.96"),
        val_30d=Decimal("0.90"),
        vah_30d=Decimal("1.04"),
        low_30d=Decimal("0.85"),
        low_24h=Decimal("0.99"),
        low_4h=Decimal("1.02"),
        local_swing_high_4h=Decimal("1.04"),
        atr14_4h=Decimal("0.025"),
        bb_bandwidth_4h=Decimal("0.11"),
        bb_inside_kc_4h=True,
        vcr_4h=Decimal("1.15"),
        rvol_4h=Decimal("1.80"),
        pos_in_range_30d=Decimal("0.88"),
        range_height_30d=Decimal("0.30"),
        dist_to_ceiling=Decimal("0.20"),
        ret_24h=Decimal("0.06"),
        ret_7d=Decimal("0.12"),
        btc_residual_alpha_24h=Decimal("0.04"),
        macd_hist_4h=Decimal("0.005"),
        macd_hist_4h_prev=Decimal("0.002"),
        macd_hist_1h=Decimal("0.004"),
        norm_momentum_1h=Decimal("1.20"),
        cvd_24h=Decimal("500000"),
        oi_delta_7d=None,
        oi_delta_24h=None,
        oi_delta_4h=None,
        funding_rate_8h=Decimal("0.00005"),
        taker_buy_ratio_4h=None,
        taker_buy_ratio_1h=None,
        ema20_4h=Decimal("1.01"),
        ema20_1h=Decimal("1.03"),
        rvol_1h=Decimal("1.90"),
        low_48h=Decimal("0.97"),
    )
    f_stage2 = replace(
        f_stage1,
        oi_delta_7d=Decimal("0.06"),
        oi_delta_24h=Decimal("0.05"),
        oi_delta_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.16"),
    )
    assert classify_long_archetype(f_stage1) is SetupLabel.BREAKOUT_LONG
    assert classify_long_archetype(f_stage2) is SetupLabel.BREAKOUT_LONG




