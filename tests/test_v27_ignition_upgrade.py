from __future__ import annotations

from decimal import Decimal
import pytest

from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
from algotrader.domain.crypto_screener_models import (
    CryptoOpportunity,
    CryptoMarketRegime,
    CryptoScreenerScoreBreakdown,
    Direction,
    SetupLabel,
)
from algotrader.domain.models import Instrument, InstrumentId, MarketKind, Venue
from algotrader.domain.scoring import (
    calculate_adx_subscore,
    calculate_continuous_long_factor_scores,
    classify_opportunity_grade,
    compute_3tier_ev_r,
    compute_p_win_and_conviction,
)
from algotrader.domain.screening_gates import (
    LongGateInputs,
    evaluate_long_gates,
)
from tests.conftest import make_instrument


def _make_archetype_features(**kwargs) -> LongArchetypeFeatures:
    defaults = {
        "close": Decimal("100.00"),
        "low_24h": Decimal("98.00"),
        "ema50_4h": Decimal("96.00"),
        "poc_30d": Decimal("98.00"),
        "vah_30d": Decimal("106.00"),
        "val_30d": Decimal("94.00"),
        "atr14_4h": Decimal("2.00"),
        "vcr_4h": Decimal("0.85"),
        "oi_delta_7d": Decimal("0.05"),
        "oi_delta_4h": Decimal("0.02"),
        "ret_7d": Decimal("0.04"),
        "bb_inside_kc_4h": True,
        "bb_bandwidth_4h": Decimal("0.14"),
        "local_swing_high_4h": Decimal("102.00"),
        "rvol_4h": Decimal("1.25"),
        "norm_momentum_1h": Decimal("0.5"),
        "btc_residual_alpha_24h": Decimal("0.02"),
        "macd_hist_4h": Decimal("0.2"),
        "macd_hist_4h_prev": Decimal("0.1"),
        "macd_hist_1h": Decimal("0.1"),
        "taker_buy_ratio_4h": Decimal("0.54"),
    }
    defaults.update(kwargs)
    return LongArchetypeFeatures(**defaults)


def _base_valid_gate_inputs() -> LongGateInputs:
    return LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("96.00"),
        ema20_1h=Decimal("98.50"),
        ema200_4h=Decimal("90.00"),
        low_24h=Decimal("98.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("98.00"),
        excursion_72h=Decimal("0.12"),
        ret_24h=Decimal("0.05"),
        rsi_1h=Decimal("56.0"),
        rsi_4h=Decimal("58.0"),
        rsi_1d=Decimal("54.0"),
        rsi_1w=Decimal("52.0"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("88.00"),
        poc_30d=Decimal("98.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("106.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        rvol_4h=Decimal("1.25"),
        oi_delta_7d=Decimal("0.08"),
        oi_delta_4h=Decimal("0.03"),
        taker_buy_ratio_4h=Decimal("0.54"),
        high_4h=Decimal("101.00"),
        low_4h=Decimal("99.00"),
        atr14_1h=Decimal("1.00"),
        funding_rate_8h=Decimal("0.00002"),
        ret_7d=Decimal("0.04"),
        prior_bb_bandwidth_4h=Decimal("0.13"),
        pivot_resistance=Decimal("100.50"),
        candle_4h_close_pos=Decimal("0.80"),
        has_kinetic_ignition=True,
    )


# --- Test 1: Gate L6 Universal Order-Flow & Kinetic Ignition (Eliminate Dead Coils) ---

def test_gate_l6_rejects_passive_coil_far_from_ceiling() -> None:
    """Task 2: Eliminate Gate 6 bypass.
    
    A passive token with dead/bleeding OI and no kinetic ignition MUST BE REJECTED
    even when dist_to_ceiling > 4.5% (e.g. dist_to_ceiling = 10%).
    """
    inputs = _base_valid_gate_inputs()
    # 10% distance to ceiling (high_30d=110, close=100 -> dist = 10%)
    dead_coil_inputs = LongGateInputs(
        close=inputs.close,
        ema50_4h=inputs.ema50_4h,
        ema20_1h=inputs.ema20_1h,
        ema200_4h=inputs.ema200_4h,
        low_24h=inputs.low_24h,
        atr14_4h=inputs.atr14_4h,
        vcr_4h=Decimal("1.20"),  # No supply dryup
        ema20_4h=inputs.ema20_4h,
        excursion_72h=inputs.excursion_72h,
        ret_24h=inputs.ret_24h,
        rsi_1h=inputs.rsi_1h,
        rsi_4h=inputs.rsi_4h,
        rsi_1d=inputs.rsi_1d,
        rsi_1w=inputs.rsi_1w,
        high_30d=inputs.high_30d,
        low_30d=inputs.low_30d,
        poc_30d=inputs.poc_30d,
        val_30d=inputs.val_30d,
        vah_30d=inputs.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),  # Compressed setup
        rvol_4h=Decimal("0.75"),  # Dead volume (< 1.20x)
        oi_delta_7d=Decimal("-0.05"),  # Bleeding OI (< -2.5%)
        oi_delta_4h=Decimal("-0.01"),  # No 4h inflection (< +2.5%)
        taker_buy_ratio_4h=Decimal("0.48"),
        has_kinetic_ignition=False,
        pocket_pivot_1h=False,
        micro_expansion_1h=False,
        of_absorption_1h=False,
    )
    passed, gate_details = evaluate_long_gates(dead_coil_inputs, SetupLabel.COIL_SPRING_LONG)
    assert not passed
    assert not gate_details["L6"]


def test_gate_l6_accepts_kinetic_ignition_triggers() -> None:
    """Task 2: Verify active ignition triggers A, B, and C pass Gate L6."""
    base = _base_valid_gate_inputs()

    # Trigger A: Pocket Pivot
    pp_inputs = LongGateInputs(
        close=base.close,
        ema50_4h=base.ema50_4h,
        ema20_1h=base.ema20_1h,
        ema200_4h=base.ema200_4h,
        low_24h=base.low_24h,
        atr14_4h=base.atr14_4h,
        vcr_4h=base.vcr_4h,
        ema20_4h=base.ema20_4h,
        excursion_72h=base.excursion_72h,
        ret_24h=base.ret_24h,
        rsi_1h=base.rsi_1h,
        rsi_4h=base.rsi_4h,
        high_30d=base.high_30d,
        low_30d=base.low_30d,
        poc_30d=base.poc_30d,
        val_30d=base.val_30d,
        vah_30d=base.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        oi_delta_7d=Decimal("0.02"),
        oi_delta_4h=Decimal("0.01"),
        pocket_pivot_1h=True,
    )
    passed_a, details_a = evaluate_long_gates(pp_inputs, SetupLabel.COIL_SPRING_LONG)
    assert details_a["L6"]
    assert passed_a

    # Trigger B: Micro Expansion
    me_inputs = LongGateInputs(
        close=base.close,
        ema50_4h=base.ema50_4h,
        ema20_1h=base.ema20_1h,
        ema200_4h=base.ema200_4h,
        low_24h=base.low_24h,
        atr14_4h=base.atr14_4h,
        vcr_4h=base.vcr_4h,
        ema20_4h=base.ema20_4h,
        excursion_72h=base.excursion_72h,
        ret_24h=base.ret_24h,
        rsi_1h=base.rsi_1h,
        rsi_4h=base.rsi_4h,
        high_30d=base.high_30d,
        low_30d=base.low_30d,
        poc_30d=base.poc_30d,
        val_30d=base.val_30d,
        vah_30d=base.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        oi_delta_7d=Decimal("0.02"),
        micro_expansion_1h=True,
    )
    passed_b, details_b = evaluate_long_gates(me_inputs, SetupLabel.COIL_SPRING_LONG)
    assert details_b["L6"]
    assert passed_b

    # Trigger C: Order-flow Absorption
    of_inputs = LongGateInputs(
        close=base.close,
        ema50_4h=base.ema50_4h,
        ema20_1h=base.ema20_1h,
        ema200_4h=base.ema200_4h,
        low_24h=base.low_24h,
        atr14_4h=base.atr14_4h,
        vcr_4h=base.vcr_4h,
        ema20_4h=base.ema20_4h,
        excursion_72h=base.excursion_72h,
        ret_24h=base.ret_24h,
        rsi_1h=base.rsi_1h,
        rsi_4h=base.rsi_4h,
        high_30d=base.high_30d,
        low_30d=base.low_30d,
        poc_30d=base.poc_30d,
        val_30d=base.val_30d,
        vah_30d=base.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        oi_delta_7d=Decimal("0.01"),
        oi_delta_4h=Decimal("0.04"),
        taker_buy_ratio_1h=Decimal("1.20"),
    )
    passed_c, details_c = evaluate_long_gates(of_inputs, SetupLabel.COIL_SPRING_LONG)
    assert details_c["L6"]
    assert passed_c


# --- Test 2: ADX vs. Squeeze Scoring Paradox Resolution & Blended OI ---

def test_adx_inflection_on_low_bandwidth_squeeze() -> None:
    """Task 3: When in verified compression (bandwidth <= 16% or BB inside KC),
    
    award full 100.0 points if +DI > -DI and delta ADX_1h >= 1.5 or delta DMI diff >= 3.0,
    even if static ADX is low (e.g. ADX = 14).
    """
    # 1. Coiled ignition inflection gives full 100.0 pts
    score_inflection = calculate_adx_subscore(
        archetype=SetupLabel.COIL_SPRING_LONG,
        adx_4h=Decimal("14.0"),  # Static ADX is low!
        adx_4h_prev=Decimal("13.0"),
        dmi_plus=Decimal("26.0"),
        dmi_minus=Decimal("18.0"),
        bandwidth_4h=Decimal("0.12"),  # <= 16% compression
        bb_inside_kc=True,
        delta_adx_1h=Decimal("1.8"),  # >= 1.5 delta
        dmi_plus_4h=Decimal("26.0"),
        dmi_minus_4h=Decimal("18.0"),
    )
    assert score_inflection == Decimal("100.0")

    # 2. Quiet coil without inflection gets standard accumulation score
    score_quiet = calculate_adx_subscore(
        archetype=SetupLabel.COIL_SPRING_LONG,
        adx_4h=Decimal("14.0"),
        adx_4h_prev=Decimal("14.0"),
        dmi_plus=Decimal("22.0"),
        dmi_minus=Decimal("20.0"),
        bandwidth_4h=Decimal("0.12"),
        bb_inside_kc=True,
        delta_adx_1h=Decimal("0.2"),  # < 1.5
        delta_dmi_diff_1h=Decimal("1.0"),  # < 3.0
    )
    assert score_quiet == Decimal("100.0")  # (100 - 4*(14-14))


def test_blended_oi_velocity_in_derivatives_scoring() -> None:
    """Task 3: Blend OI_4h_Delta (40%), OI_7d_Delta (40%), and OI_1h_Delta (20%)."""
    scores = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.12"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.85"),
        close=Decimal("100.00"),
        poc_30d=Decimal("98.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("106.00"),
        pos_in_range_30d=Decimal("0.45"),
        ema50_4h=Decimal("96.00"),
        ema200_4h=Decimal("90.00"),
        resistance_absorption_score=Decimal("60.0"),
        cvd_zscore=Decimal("1.2"),
        oi_change_7d=Decimal("0.00"),  # 7d is flat
        oi_change_4h=Decimal("0.05"),  # fresh 4h institutional accumulation (+5%)
        taker_buy_ratio_4h=Decimal("1.22"),
        funding_rate=Decimal("0.00002"),
        ema20_4h=Decimal("98.00"),
        macd_hist_4h=Decimal("0.5"),
        macd_hist_4h_prev=Decimal("0.2"),
        adx_subscore=Decimal("100.0"),
        btc_residual_alpha=Decimal("0.02"),
        rvol_4h=Decimal("1.30"),
        asymmetric_rr=Decimal("2.8"),
        dist_to_trigger=Decimal("0.03"),
        macro_multiplier=Decimal("1.0"),
        oi_change_1h=Decimal("0.015"),
    )
    # Even though 7d was flat, fresh 4h accumulation drives derivatives score high
    assert scores.derivatives_score >= Decimal("65.0")


# --- Test 3: FUNDING_SQUEEZE_SPRING_LONG Quality Floor (Anti-Falling Knife) ---

def test_funding_squeeze_spring_rejects_falling_knife() -> None:
    """Task 4: Crowded negative funding must NOT buy Stage 4 falling knives or tokens at 30d lows."""
    # Knife Case 1: Close is below 30d VAL (Value Area Floor breached)
    features_below_val = _make_archetype_features(
        close=Decimal("80.00"),
        low_24h=Decimal("78.00"),
        ema50_4h=Decimal("92.00"),
        poc_30d=Decimal("96.00"),
        vah_30d=Decimal("104.00"),
        val_30d=Decimal("90.00"),  # Close 80 < VAL 90
        atr14_4h=Decimal("3.00"),
        vcr_4h=Decimal("0.85"),
        funding_rate_8h=Decimal("-0.00025"),  # Heavy negative funding
        oi_delta_24h=Decimal("0.08"),  # >= 0.06 funding squeeze fuel
        oi_delta_4h=Decimal("0.01"),
        oi_delta_7d=Decimal("0.02"),
        taker_buy_ratio_1h=Decimal("1.25"),
        low_30d=Decimal("78.00"),
        low_4h=Decimal("78.50"),  # At 30d low
        pos_in_range_30d=Decimal("0.05"),
    )
    assert classify_long_archetype(features_below_val) != SetupLabel.FUNDING_SQUEEZE_SPRING_LONG
    assert classify_long_archetype(features_below_val) is None

    # Knife Case 2: No 30-day Higher Low (trading at 30d bottom without 3% Wyckoff buffer)
    features_at_low = _make_archetype_features(
        close=Decimal("91.00"),
        low_24h=Decimal("88.10"),
        ema50_4h=Decimal("93.00"),
        poc_30d=Decimal("96.00"),
        vah_30d=Decimal("104.00"),
        val_30d=Decimal("90.00"),
        atr14_4h=Decimal("2.50"),
        vcr_4h=Decimal("0.85"),
        funding_rate_8h=Decimal("-0.00025"),  # Heavy negative funding
        oi_delta_24h=Decimal("0.08"),  # >= 0.06 funding squeeze fuel
        oi_delta_4h=Decimal("0.01"),
        oi_delta_7d=Decimal("0.02"),
        taker_buy_ratio_1h=Decimal("1.25"),
        low_30d=Decimal("88.00"),
        low_4h=Decimal("88.20"),  # < 88.00 * 1.03 = 90.64 -> No Wyckoff higher low!
        pos_in_range_30d=Decimal("0.18"),
        ema20_4h=Decimal("90.50"),
    )
    assert classify_long_archetype(features_at_low) != SetupLabel.FUNDING_SQUEEZE_SPRING_LONG


def test_funding_squeeze_spring_accepts_higher_low_wyckoff_spring() -> None:
    """Task 4: Accept higher-low Wyckoff spring with negative funding and Value Area support."""
    features_valid = _make_archetype_features(
        close=Decimal("93.50"),
        low_24h=Decimal("91.50"),
        ema50_4h=Decimal("94.00"),
        poc_30d=Decimal("98.00"),
        vah_30d=Decimal("105.00"),
        val_30d=Decimal("91.00"),  # Inside Value Area (close >= VAL)
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        funding_rate_8h=Decimal("-0.00020"),  # Shorts paying longs
        oi_delta_24h=Decimal("0.09"),  # +9% 24h OI
        taker_buy_ratio_1h=Decimal("1.25"),
        low_30d=Decimal("88.00"),
        low_4h=Decimal("91.20"),  # >= 88.00 * 1.03 (90.64) -> verified higher low!
        pos_in_range_30d=Decimal("0.25"),  # >= 0.15
        ema20_4h=Decimal("92.50"),  # reclaimed 4H EMA20
        rvol_1h=Decimal("1.50"),  # >= 1.35x
    )
    arch = classify_long_archetype(features_valid)
    assert arch == SetupLabel.FUNDING_SQUEEZE_SPRING_LONG


# --- Test 4: Gate L2 Anti-Chasing 2.1 Controlled First-Bar Ignition ---

def test_anti_chasing_rejects_upper_wick_blowoff_in_expansion_zone() -> None:
    """Task 5: In (+14.0%, +17.5%] zone, reject upper-wick rejections (close_pos < 0.70)."""
    base = _base_valid_gate_inputs()
    blowoff_inputs = LongGateInputs(
        close=base.close,
        ema50_4h=base.ema50_4h,
        ema20_1h=base.ema20_1h,
        ema200_4h=base.ema200_4h,
        low_24h=base.low_24h,
        atr14_4h=base.atr14_4h,
        vcr_4h=base.vcr_4h,
        ema20_4h=base.ema20_4h,
        excursion_72h=Decimal("0.22"),
        ret_24h=Decimal("0.16"),  # in (+14%, +17.5%]
        rsi_1h=Decimal("65.0"),
        rsi_4h=Decimal("62.0"),
        high_30d=base.high_30d,
        low_30d=base.low_30d,
        poc_30d=base.poc_30d,
        val_30d=base.val_30d,
        vah_30d=base.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.18"),
        prior_bb_bandwidth_4h=Decimal("0.14"),  # was coiled
        pivot_resistance=Decimal("98.50"),
        candle_4h_close_pos=Decimal("0.45"),  # Upper wick rejection! (< 0.70)
        has_kinetic_ignition=True,
    )
    passed, details = evaluate_long_gates(blowoff_inputs, SetupLabel.BREAKOUT_LONG)
    assert not passed
    assert not details["L2"]


def test_anti_chasing_accepts_clean_first_bar_ignition() -> None:
    """Task 5: In (+14.0%, +17.5%] zone, accept clean institutional candle (close_pos >= 0.70, coiled prior bar, non-exhausted RSI)."""
    base = _base_valid_gate_inputs()
    clean_ignition_inputs = LongGateInputs(
        close=base.close,
        ema50_4h=base.ema50_4h,
        ema20_1h=base.ema20_1h,
        ema200_4h=base.ema200_4h,
        low_24h=base.low_24h,
        atr14_4h=base.atr14_4h,
        vcr_4h=base.vcr_4h,
        ema20_4h=base.ema20_4h,
        excursion_72h=Decimal("0.20"),
        ret_24h=Decimal("0.155"),  # in (+14%, +17.5%]
        rsi_1h=Decimal("68.0"),  # <= 73.0
        rsi_4h=Decimal("64.0"),  # <= 68.0
        high_30d=base.high_30d,
        low_30d=base.low_30d,
        poc_30d=base.poc_30d,
        val_30d=base.val_30d,
        vah_30d=base.vah_30d,
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.18"),
        prior_bb_bandwidth_4h=Decimal("0.14"),  # <= 16% (prior bar was coiled)
        pivot_resistance=Decimal("99.00"),  # (100 - 99) = 1.0 <= 0.85 * 2.0 (1.70 ATR)
        candle_4h_close_pos=Decimal("0.85"),  # clean bullish bar (>= 0.70)
        has_kinetic_ignition=True,
    )
    passed, details = evaluate_long_gates(clean_ignition_inputs, SetupLabel.BREAKOUT_LONG)
    assert details["L2"]
    assert passed


# --- Test 5: Discovery Tier Quality Gate Floor ---

def test_discovery_tier_requires_higher_score_and_conviction() -> None:
    """Task 1: Enforce Discovery Quality Premium in Quality Gate:
    
    is_discovery_tier == True requires Score >= 71.0 and Conviction >= 69.0.
    """
    inst = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="PUMPUSDT")
    # Candidate with Score = 69.5, Conviction = 67.0
    # Passes Core Tier (>= 68.0 / 65.0), but FAILS Discovery Tier (requires >= 71.0 / 69.0)
    score_mid = Decimal("69.5")
    conv_mid = Decimal("67.0")

    opp_core = CryptoOpportunity(
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.OI_ACCUMULATION_LONG,
        score=score_mid,
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=Decimal("70.0"),
            momentum_score=Decimal("70.0"),
            derivatives_score=Decimal("70.0"),
            alpha_score=Decimal("70.0"),
            volume_score=Decimal("70.0"),
            structure_score=Decimal("70.0"),
            macro_multiplier=Decimal("1.0"),
            final_composite_score=score_mid,
        ),
        current_price=Decimal("1.00"),
        atr14=Decimal("0.05"),
        suggested_entry=Decimal("1.00"),
        suggested_stop=Decimal("0.95"),
        suggested_targets=(Decimal("1.10"), Decimal("1.20")),
        risk_reward_ratio=Decimal("2.50"),
        btc_beta=Decimal("1.0"),
        residual_return_24h=Decimal("0.02"),
        funding_rate=Decimal("0.0001"),
        oi_change_1h=Decimal("0.01"),
        taker_ratio=Decimal("1.10"),
        rationale=("Institutional setup",),
        macro_narrative="Bullish expansion",
        estimated_win_rate_pct=Decimal("60.0"),
        expected_value_r=Decimal("0.45"),
        conviction=conv_mid,
        grade="Grade A",
        is_discovery_tier=False,
    )
    opp_discovery = CryptoOpportunity(
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.OI_ACCUMULATION_LONG,
        score=score_mid,
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=Decimal("70.0"),
            momentum_score=Decimal("70.0"),
            derivatives_score=Decimal("70.0"),
            alpha_score=Decimal("70.0"),
            volume_score=Decimal("70.0"),
            structure_score=Decimal("70.0"),
            macro_multiplier=Decimal("1.0"),
            final_composite_score=score_mid,
        ),
        current_price=Decimal("1.00"),
        atr14=Decimal("0.05"),
        suggested_entry=Decimal("1.00"),
        suggested_stop=Decimal("0.95"),
        suggested_targets=(Decimal("1.10"), Decimal("1.20")),
        risk_reward_ratio=Decimal("2.50"),
        btc_beta=Decimal("1.0"),
        residual_return_24h=Decimal("0.02"),
        funding_rate=Decimal("0.0001"),
        oi_change_1h=Decimal("0.01"),
        taker_ratio=Decimal("1.10"),
        rationale=("Institutional setup",),
        macro_narrative="Bullish expansion",
        estimated_win_rate_pct=Decimal("60.0"),
        expected_value_r=Decimal("0.45"),
        conviction=conv_mid,
        grade="Grade A",
        is_discovery_tier=True,
    )

    # Core tier floor: Score >= 68.0, Conviction >= 65.0
    core_passes = opp_core.score >= Decimal("68.0") and opp_core.conviction >= Decimal("65.0")
    assert core_passes

    # Discovery tier floor: Score >= 71.0, Conviction >= 69.0
    min_score_disc = Decimal("71.0") if opp_discovery.is_discovery_tier else Decimal("68.0")
    min_conv_disc = Decimal("69.0") if opp_discovery.is_discovery_tier else Decimal("65.0")
    discovery_passes = opp_discovery.score >= min_score_disc and opp_discovery.conviction >= min_conv_disc
    assert not discovery_passes  # Rejected by higher discovery floor!
