from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.scheduler import (
    seconds_until_next_fast_cycle,
    seconds_until_next_settlement_cycle,
)
from algotrader.domain.archetypes import (
    LongArchetypeFeatures,
    ShortArchetypeFeatures,
    classify_long_archetype,
    classify_short_archetype,
)
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.execution_brackets import (
    build_long_bracket,
    build_short_bracket,
)
from algotrader.domain.indicators import volume_profile
from algotrader.domain.liquidity import SymbolLiquidityMetrics, compute_ili_ranks, percentile_rank
from algotrader.domain.models import Candle, Direction
from algotrader.domain.precision import normalize_price, normalize_quantity
from algotrader.domain.scoring import (
    LongContinuousFactorScores,
    OpportunityGrade,
    calculate_adx_subscore,
    calculate_continuous_long_factor_scores,
    classify_opportunity_grade,
    compute_3tier_ev_r,
    compute_p_win_and_conviction,
)
from algotrader.domain.screening_gates import (
    LongGateInputs,
    ShortGateInputs,
    evaluate_long_gates,
    evaluate_short_gates,
)


# =====================================================================
# 1. Stage 3 Tick & Quantity Normalization
# =====================================================================


def test_stage3_normalize_price() -> None:
    # Round to nearest tick_size
    tick = Decimal("0.01")
    assert normalize_price(Decimal("100.004"), tick) == Decimal("100.00")
    assert normalize_price(Decimal("100.006"), tick) == Decimal("100.01")

    # Tick size with small decimals (e.g. SHIB 0.000001)
    shib_tick = Decimal("0.000001")
    assert normalize_price(Decimal("0.0000254"), shib_tick) == Decimal("0.000025")
    assert normalize_price(Decimal("0.0000256"), shib_tick) == Decimal("0.000026")


def test_stage3_normalize_quantity() -> None:
    # Floor to step_size
    step = Decimal("0.001")
    assert normalize_quantity(Decimal("1.2349"), step) == Decimal("1.234")
    assert normalize_quantity(Decimal("1.2341"), step) == Decimal("1.234")
    assert normalize_quantity(Decimal("5.0"), Decimal("1")) == Decimal("5")


# =====================================================================
# 2. Stage 5 Institutional Liquidity Index (ILI) Ranking
# =====================================================================


def test_percentile_rank() -> None:
    values = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")]
    ranks = percentile_rank(values)
    # Mid-rank for 30 in 5 items: rank 2 out of 5 -> 0.50
    assert ranks[2] == Decimal("0.50")
    assert ranks[4] == Decimal("0.90")


def test_compute_ili_ranks() -> None:
    symbols_data = [
        SymbolLiquidityMetrics(
            symbol="AAA",
            quote_vol_24h=Decimal("100000000"),
            open_interest_usd=Decimal("50000000"),
            median_quote_vol_30d=Decimal("80000000"),
        ),
        SymbolLiquidityMetrics(
            symbol="BBB",
            quote_vol_24h=Decimal("10000000"),
            open_interest_usd=Decimal("5000000"),
            median_quote_vol_30d=Decimal("8000000"),
        ),
        SymbolLiquidityMetrics(
            symbol="CCC",
            quote_vol_24h=Decimal("50000000"),
            open_interest_usd=Decimal("25000000"),
            median_quote_vol_30d=Decimal("40000000"),
        ),
    ]

    ranks = compute_ili_ranks(symbols_data)
    assert len(ranks) == 3
    # AAA should be top rank (highest metrics across 30d vol, OI, 24h vol)
    assert ranks[0].symbol == "AAA"
    assert ranks[1].symbol == "CCC"
    assert ranks[2].symbol == "BBB"
    assert ranks[0].ili > ranks[1].ili > ranks[2].ili


# =====================================================================
# 3. 1H Triangular Kernel VPVR
# =====================================================================


def test_triangular_vpvr_allocates_volume() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            opened_at=started + timedelta(hours=i),
            closed_at=started + timedelta(hours=i + 1),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            quote_volume=Decimal("100000"),
            closed=True,
        )
        for i in range(50)
    ]

    vp = volume_profile(candles, bins=20)
    # Mode is typical price (110 + 90 + 100) / 3 = 100.0
    assert Decimal("98") <= vp.poc_price <= Decimal("102")
    assert vp.val_price < vp.poc_price < vp.vah_price
    assert vp.val_price >= Decimal("90")
    assert vp.vah_price <= Decimal("110")


# =====================================================================
# 4. Long Archetype Classifiers
# =====================================================================


def test_classify_coil_spring_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("100.50"),
        low_24h=Decimal("98.00"),  # Swept below ema50 (99.00) - 0.10*atr
        ema50_4h=Decimal("99.00"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("102.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),  # Contraction <= 0.90
        oi_delta_7d=Decimal("0.05"),
        oi_delta_4h=Decimal("0.02"),
        ret_7d=Decimal("0.01"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
        local_swing_high_4h=Decimal("101.00"),
        rvol_4h=Decimal("1.10"),
        norm_momentum_1h=Decimal("0.2"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        macd_hist_1h=Decimal("0.01"),
        taker_buy_ratio_4h=Decimal("0.52"),
    )
    assert classify_long_archetype(features) == SetupLabel.COIL_SPRING_LONG


def test_classify_oi_accumulation_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("100.00"),
        low_24h=Decimal("99.50"),  # No spring sweep
        ema50_4h=Decimal("99.00"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("102.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.95"),
        oi_delta_7d=Decimal("0.25"),  # 0.10 <= oi_7d <= 0.60
        oi_delta_4h=Decimal("0.02"),
        ret_7d=Decimal("0.03"),  # Price flat: |ret_7d| <= 0.12
        bb_inside_kc_4h=True,  # Compressed
        bb_bandwidth_4h=Decimal("0.15"),
        local_swing_high_4h=Decimal("101.00"),
        rvol_4h=Decimal("1.10"),
        norm_momentum_1h=Decimal("0.1"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("0.02"),
        macd_hist_4h_prev=Decimal("0.01"),
        macd_hist_1h=Decimal("0.01"),
        taker_buy_ratio_4h=Decimal("0.52"),
    )
    assert classify_long_archetype(features) == SetupLabel.OI_ACCUMULATION_LONG


def test_classify_breakout_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("103.00"),  # At or above local_swing_high (102.50) / vah_30d (102.00)
        low_24h=Decimal("99.50"),
        ema50_4h=Decimal("99.00"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("102.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("1.10"),
        oi_delta_7d=Decimal("0.05"),
        oi_delta_4h=Decimal("0.03"),  # Expanding >= 0.01
        ret_7d=Decimal("0.15"),  # Expanding > 0.12 so not OI accumulation
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=Decimal("0.28"),
        local_swing_high_4h=Decimal("102.50"),
        rvol_4h=Decimal("1.45"),  # rvol >= 1.25
        norm_momentum_1h=Decimal("0.35"),  # > 0
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("0.10"),
        macd_hist_4h_prev=Decimal("0.05"),
        macd_hist_1h=Decimal("0.05"),
        taker_buy_ratio_4h=Decimal("0.55"),
    )
    assert classify_long_archetype(features) == SetupLabel.BREAKOUT_LONG


def test_classify_momentum_emerging_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("100.50"),
        low_24h=Decimal("99.50"),
        ema50_4h=Decimal("99.00"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("104.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("1.05"),
        oi_delta_7d=Decimal("0.04"),  # Below 0.10
        oi_delta_4h=Decimal("0.005"),
        ret_7d=Decimal("0.05"),
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=Decimal("0.25"),
        local_swing_high_4h=Decimal("103.00"),
        rvol_4h=Decimal("1.10"),
        norm_momentum_1h=Decimal("0.15"),
        btc_residual_alpha_24h=Decimal("0.045"),  # >= 0.03
        macd_hist_4h=Decimal("0.15"),  # > prev
        macd_hist_4h_prev=Decimal("0.08"),
        macd_hist_1h=Decimal("0.05"),  # > 0
        taker_buy_ratio_4h=Decimal("0.54"),  # >= 0.535
    )
    assert classify_long_archetype(features) == SetupLabel.MOMENTUM_EMERGING_LONG


def test_classify_vcp_compression_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("100.00"),
        low_24h=Decimal("99.50"),
        ema50_4h=Decimal("99.00"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("102.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),  # Supply dry-up <= 0.92
        oi_delta_7d=Decimal("0.02"),
        oi_delta_4h=Decimal("0.01"),
        ret_7d=Decimal("0.02"),
        bb_inside_kc_4h=True,  # Compressed Carter Squeeze
        bb_bandwidth_4h=Decimal("0.12"),
        local_swing_high_4h=Decimal("101.50"),
        rvol_4h=Decimal("0.95"),
        norm_momentum_1h=Decimal("0.1"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("0.08"),  # Mom up
        macd_hist_4h_prev=Decimal("0.03"),
        macd_hist_1h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.52"),
        range_height_30d=Decimal("0.22"),  # Tight VCP base <= 0.35
        pos_in_range_30d=Decimal("0.75"),  # Launchpad upper half >= 0.50
        dist_to_ceiling=Decimal("0.05"),  # Within 5% of ceiling <= 0.15
    )
    assert classify_long_archetype(features) == SetupLabel.VCP_COMPRESSION_LONG


def test_classify_range_bottom_accumulation_long() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("100.00"),
        low_24h=Decimal("99.00"),
        ema50_4h=Decimal("99.50"),
        poc_30d=Decimal("99.00"),
        vah_30d=Decimal("105.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.92"),  # Supply dry-up <= 0.95
        oi_delta_7d=Decimal("-0.01"),
        oi_delta_4h=Decimal("0.005"),
        ret_7d=Decimal("0.01"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.16"),
        local_swing_high_4h=Decimal("103.00"),
        rvol_4h=Decimal("0.90"),
        norm_momentum_1h=Decimal("0.1"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        macd_hist_1h=Decimal("0.01"),
        taker_buy_ratio_4h=Decimal("0.51"),
        range_height_30d=Decimal("0.38"),  # <= 0.42
        pos_in_range_30d=Decimal("0.60"),
        dist_to_ceiling=Decimal("0.12"),  # <= 0.16
    )
    assert classify_long_archetype(features) == SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG


# =====================================================================
# 5. Short Archetype Classifiers
# =====================================================================


def test_classify_utad_divergence_short() -> None:
    features = ShortArchetypeFeatures(
        close=Decimal("106.00"),
        high_24h=Decimal("109.50"),  # Swept near high_30d (110.00 * 0.985 = 108.35)
        high_30d=Decimal("110.00"),
        ema50_4h=Decimal("95.00"),
        poc_30d=Decimal("100.00"),
        atr14_4h=Decimal("3.00"),
        has_bearish_div=True,  # Confirmed divergence
        rsi_4h=Decimal("68.00"),
        rsi_1d=Decimal("70.00"),
        rsi_1w=Decimal("65.00"),
        oi_delta_7d=Decimal("0.20"),
        upper_wick_ratio_4h=Decimal("0.45"),
        max_ret_30d=Decimal("0.60"),
        taker_buy_ratio_24h=Decimal("0.47"),
        macd_hist_4h=Decimal("-0.05"),
    )
    # close (106) < high_24h (109.5) - 0.5*atr (1.5) = 108
    assert classify_short_archetype(features) == SetupLabel.UTAD_DIVERGENCE_SHORT


def test_classify_exhaustion_reversal_short() -> None:
    features = ShortArchetypeFeatures(
        close=Decimal("104.00"),
        high_24h=Decimal("108.00"),  # Swept high_24h, rejected back down
        high_30d=Decimal("120.00"),
        ema50_4h=Decimal("90.00"),
        poc_30d=Decimal("95.00"),
        atr14_4h=Decimal("3.00"),
        has_bearish_div=False,
        rsi_4h=Decimal("74.00"),  # >= 72.0
        rsi_1d=Decimal("78.00"),  # >= 76.0
        rsi_1w=Decimal("70.00"),
        oi_delta_7d=Decimal("0.65"),  # >= 0.25
        upper_wick_ratio_4h=Decimal("0.45"),  # >= 0.45
        max_ret_30d=Decimal("0.90"),
        taker_buy_ratio_24h=Decimal("0.49"),
        macd_hist_4h=Decimal("0.05"),
        pos_in_range_30d=Decimal("0.70"),
    )
    assert classify_short_archetype(features) == SetupLabel.EXHAUSTION_REVERSAL_SHORT


def test_classify_distribution_short() -> None:
    features = ShortArchetypeFeatures(
        close=Decimal("88.00"),  # Below ema50 (92) and poc_30d (90)
        high_24h=Decimal("91.00"),
        high_30d=Decimal("130.00"),
        ema50_4h=Decimal("92.00"),
        poc_30d=Decimal("90.00"),
        atr14_4h=Decimal("3.00"),
        has_bearish_div=False,
        rsi_4h=Decimal("42.00"),
        rsi_1d=Decimal("52.00"),
        rsi_1w=Decimal("55.00"),
        oi_delta_7d=Decimal("0.10"),
        upper_wick_ratio_4h=Decimal("0.25"),
        max_ret_30d=Decimal("0.75"),  # >= 0.50
        taker_buy_ratio_24h=Decimal("0.46"),  # <= 0.48
        macd_hist_4h=Decimal("-0.15"),  # < 0
    )
    assert classify_short_archetype(features) == SetupLabel.DISTRIBUTION_SHORT


# =====================================================================
# 6. Screening Gates (L1–L6 and S1–S5)
# =====================================================================


def test_long_gates_pass_and_fail() -> None:
    valid_inputs = LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("98.00"),
        ema20_1h=Decimal("99.00"),
        ema200_4h=Decimal("92.00"),
        low_24h=Decimal("97.50"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("99.00"),
        excursion_72h=Decimal("0.15"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55.0"),
        rsi_4h=Decimal("58.0"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("90.00"),
        poc_30d=Decimal("98.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("106.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
        rvol_4h=Decimal("1.25"),
        oi_delta_7d=Decimal("0.05"),
        oi_delta_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.53"),
    )

    passed, details = evaluate_long_gates(valid_inputs, SetupLabel.COIL_SPRING_LONG)
    assert passed
    assert all(details.values())

    # Fail Gate L2: Excursion too high (chasing)
    chasing_inputs = LongGateInputs(
        close=valid_inputs.close,
        ema50_4h=valid_inputs.ema50_4h,
        ema20_1h=valid_inputs.ema20_1h,
        ema200_4h=valid_inputs.ema200_4h,
        low_24h=valid_inputs.low_24h,
        atr14_4h=valid_inputs.atr14_4h,
        vcr_4h=valid_inputs.vcr_4h,
        ema20_4h=valid_inputs.ema20_4h,
        excursion_72h=Decimal("0.45"),  # Exceeds 0.36
        ret_24h=valid_inputs.ret_24h,
        rsi_1h=valid_inputs.rsi_1h,
        rsi_4h=valid_inputs.rsi_4h,
        high_30d=valid_inputs.high_30d,
        low_30d=valid_inputs.low_30d,
        poc_30d=valid_inputs.poc_30d,
        val_30d=valid_inputs.val_30d,
        vah_30d=valid_inputs.vah_30d,
        bb_inside_kc_4h=valid_inputs.bb_inside_kc_4h,
        bb_bandwidth_4h=valid_inputs.bb_bandwidth_4h,
    )
    passed_chasing, details_chasing = evaluate_long_gates(chasing_inputs, SetupLabel.COIL_SPRING_LONG)
    assert not passed_chasing
    assert not details_chasing["L2"]

    # Fail Gate L2: Daily RSI >= 70.0 (Anti-Chasing HTF Elimination)
    daily_overbought_inputs = LongGateInputs(
        close=valid_inputs.close,
        ema50_4h=valid_inputs.ema50_4h,
        ema20_1h=valid_inputs.ema20_1h,
        ema200_4h=valid_inputs.ema200_4h,
        low_24h=valid_inputs.low_24h,
        atr14_4h=valid_inputs.atr14_4h,
        vcr_4h=valid_inputs.vcr_4h,
        ema20_4h=valid_inputs.ema20_4h,
        excursion_72h=valid_inputs.excursion_72h,
        ret_24h=valid_inputs.ret_24h,
        rsi_1h=valid_inputs.rsi_1h,
        rsi_4h=valid_inputs.rsi_4h,
        rsi_1d=Decimal("71.5"),  # >= 70.0 -> Eliminated
        high_30d=valid_inputs.high_30d,
        low_30d=valid_inputs.low_30d,
        poc_30d=valid_inputs.poc_30d,
        val_30d=valid_inputs.val_30d,
        vah_30d=valid_inputs.vah_30d,
        bb_inside_kc_4h=valid_inputs.bb_inside_kc_4h,
        bb_bandwidth_4h=valid_inputs.bb_bandwidth_4h,
    )
    passed_1d, details_1d = evaluate_long_gates(daily_overbought_inputs, SetupLabel.COIL_SPRING_LONG)
    assert not passed_1d
    assert not details_1d["L2"]

    # Fail Gate L2: Weekly RSI >= 70.0 (Anti-Chasing HTF Elimination)
    weekly_overbought_inputs = LongGateInputs(
        close=valid_inputs.close,
        ema50_4h=valid_inputs.ema50_4h,
        ema20_1h=valid_inputs.ema20_1h,
        ema200_4h=valid_inputs.ema200_4h,
        low_24h=valid_inputs.low_24h,
        atr14_4h=valid_inputs.atr14_4h,
        vcr_4h=valid_inputs.vcr_4h,
        ema20_4h=valid_inputs.ema20_4h,
        excursion_72h=valid_inputs.excursion_72h,
        ret_24h=valid_inputs.ret_24h,
        rsi_1h=valid_inputs.rsi_1h,
        rsi_4h=valid_inputs.rsi_4h,
        rsi_1d=Decimal("62.0"),
        rsi_1w=Decimal("74.2"),  # >= 70.0 -> Eliminated
        high_30d=valid_inputs.high_30d,
        low_30d=valid_inputs.low_30d,
        poc_30d=valid_inputs.poc_30d,
        val_30d=valid_inputs.val_30d,
        vah_30d=valid_inputs.vah_30d,
        bb_inside_kc_4h=valid_inputs.bb_inside_kc_4h,
        bb_bandwidth_4h=valid_inputs.bb_bandwidth_4h,
    )
    passed_1w, details_1w = evaluate_long_gates(weekly_overbought_inputs, SetupLabel.COIL_SPRING_LONG)
    assert not passed_1w
    assert not details_1w["L2"]

    # Pass Gate L2: Daily RSI and Weekly RSI < 70.0
    safe_htf_inputs = LongGateInputs(
        close=valid_inputs.close,
        ema50_4h=valid_inputs.ema50_4h,
        ema20_1h=valid_inputs.ema20_1h,
        ema200_4h=valid_inputs.ema200_4h,
        low_24h=valid_inputs.low_24h,
        atr14_4h=valid_inputs.atr14_4h,
        vcr_4h=valid_inputs.vcr_4h,
        ema20_4h=valid_inputs.ema20_4h,
        excursion_72h=valid_inputs.excursion_72h,
        ret_24h=valid_inputs.ret_24h,
        rsi_1h=valid_inputs.rsi_1h,
        rsi_4h=valid_inputs.rsi_4h,
        rsi_1d=Decimal("58.0"),
        rsi_1w=Decimal("52.0"),
        high_30d=valid_inputs.high_30d,
        low_30d=valid_inputs.low_30d,
        poc_30d=valid_inputs.poc_30d,
        val_30d=valid_inputs.val_30d,
        vah_30d=valid_inputs.vah_30d,
        bb_inside_kc_4h=valid_inputs.bb_inside_kc_4h,
        bb_bandwidth_4h=valid_inputs.bb_bandwidth_4h,
    )
    passed_safe, details_safe = evaluate_long_gates(safe_htf_inputs, SetupLabel.COIL_SPRING_LONG)
    assert passed_safe
    assert details_safe["L2"]


def test_oi_accumulation_passes_gate_l4_in_value_area() -> None:
    # Test that OI_ACCUMULATION_LONG and VCP_COMPRESSION_LONG pass Gate L4 when above VAL
    inputs = LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("98.00"),
        ema20_1h=Decimal("99.00"),
        ema200_4h=Decimal("95.00"),
        low_24h=Decimal("98.50"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.80"),
        ema20_4h=Decimal("98.50"),
        excursion_72h=Decimal("0.10"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55.0"),
        rsi_4h=Decimal("52.0"),
        rsi_1d=Decimal("50.0"),
        rsi_1w=Decimal("48.0"),
        high_30d=Decimal("115.00"),
        low_30d=Decimal("92.00"),
        poc_30d=Decimal("108.00"),  # Close (100) is below POC (108), but above VAL (96)
        val_30d=Decimal("96.00"),
        vah_30d=Decimal("112.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
    )
    passed_oi, details_oi = evaluate_long_gates(inputs, SetupLabel.OI_ACCUMULATION_LONG)
    assert details_oi["L4"]
    assert passed_oi

    passed_vcp, details_vcp = evaluate_long_gates(inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert details_vcp["L4"]


def test_breakout_extension_gate_l2_with_healthy_rsi() -> None:
    # Test that high-momentum breakout coil ignition (up to 4.0 ATRs) passes Gate L2 when RSI is non-overbought
    inputs = LongGateInputs(
        close=Decimal("107.50"),  # (107.50 - 100) / 2.0 = 3.75 ATRs above EMA20
        ema50_4h=Decimal("96.00"),
        ema20_1h=Decimal("105.00"),
        ema200_4h=Decimal("92.00"),
        low_24h=Decimal("101.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("100.00"),
        excursion_72h=Decimal("0.18"),
        ret_24h=Decimal("0.07"),
        rsi_1h=Decimal("70.0"),  # <= 75.0
        rsi_4h=Decimal("60.0"),  # <= 65.0
        rsi_1d=Decimal("55.0"),  # <= 65.0
        rsi_1w=Decimal("64.0"),  # < 70.0
        high_30d=Decimal("112.00"),
        low_30d=Decimal("90.00"),
        poc_30d=Decimal("98.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("106.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.15"),
    )
    passed, details = evaluate_long_gates(inputs, SetupLabel.COIL_SPRING_LONG)
    assert details["L2"]
    assert passed



def test_short_gates_pass_and_fail() -> None:
    # Track A: UTAD divergence short at upper range
    valid_inputs = ShortGateInputs(
        close=Decimal("104.00"),
        ema50_4h=Decimal("95.00"),
        high_7d=Decimal("110.00"),
        range_height_30d=Decimal("0.40"),
        ret_7d=Decimal("0.12"),
        ret_14d=Decimal("0.20"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("80.00"),
        days_since_30d_high=Decimal("4.0"),
        ema20_4h=Decimal("105.00"),
        atr14_4h=Decimal("3.00"),
        ret_24h=Decimal("-0.03"),
        rsi_1h=Decimal("45.0"),
        rsi_4h=Decimal("52.0"),
        has_bearish_div=True,
        has_climax_blowoff=False,
        poc_30d=Decimal("99.00"),
        macd_hist_4h=Decimal("-0.10"),
        macd_hist_4h_prev=Decimal("0.05"),
        taker_buy_ratio_24h=Decimal("0.46"),
        funding_rate_8h=Decimal("0.0002"),
        oi_delta_7d=Decimal("0.25"),
        oi_delta_1h=Decimal("0.01"),
        taker_buy_ratio_1h=Decimal("0.45"),
        high_24h=Decimal("109.00"),
    )

    passed, details = evaluate_short_gates(valid_inputs, SetupLabel.UTAD_DIVERGENCE_SHORT)
    assert passed
    assert all(details.values())

    # Track B: Structural distribution breakdown below EMA50 and POC
    track_b_inputs = ShortGateInputs(
        close=Decimal("89.00"),
        ema50_4h=Decimal("95.00"),
        high_7d=Decimal("110.00"),
        range_height_30d=Decimal("0.45"),
        ret_7d=Decimal("-0.05"),
        ret_14d=Decimal("0.24"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("70.00"),
        days_since_30d_high=Decimal("8.0"),
        ema20_4h=Decimal("92.00"),
        atr14_4h=Decimal("3.00"),
        ret_24h=Decimal("-0.03"),
        rsi_1h=Decimal("42.0"),
        rsi_4h=Decimal("48.0"),
        has_bearish_div=False,
        has_climax_blowoff=False,
        poc_30d=Decimal("96.00"),
        macd_hist_4h=Decimal("-0.15"),
        macd_hist_4h_prev=Decimal("-0.05"),
        taker_buy_ratio_24h=Decimal("0.45"),
        funding_rate_8h=Decimal("0.00015"),
        oi_delta_7d=Decimal("0.20"),
        oi_delta_1h=Decimal("-0.005"),
        taker_buy_ratio_1h=Decimal("0.44"),
        high_24h=Decimal("91.00"),
        runup_30d=Decimal("0.57"),
    )
    passed_b, details_b = evaluate_short_gates(track_b_inputs, SetupLabel.DISTRIBUTION_SHORT)
    assert passed_b
    assert all(details_b.values())

    # Fail Gate S1: No macro run-up (range height too narrow)
    flat_inputs = ShortGateInputs(
        close=valid_inputs.close,
        ema50_4h=valid_inputs.ema50_4h,
        high_7d=Decimal("99.00"),
        range_height_30d=Decimal("0.08"),  # < 0.18
        ret_7d=Decimal("0.02"),
        ret_14d=Decimal("0.03"),
        high_30d=Decimal("100.00"),
        low_30d=Decimal("95.00"),
        days_since_30d_high=valid_inputs.days_since_30d_high,
        ema20_4h=valid_inputs.ema20_4h,
        atr14_4h=valid_inputs.atr14_4h,
        ret_24h=valid_inputs.ret_24h,
        rsi_1h=valid_inputs.rsi_1h,
        rsi_4h=valid_inputs.rsi_4h,
        has_bearish_div=valid_inputs.has_bearish_div,
        has_climax_blowoff=valid_inputs.has_climax_blowoff,
        poc_30d=valid_inputs.poc_30d,
        macd_hist_4h=valid_inputs.macd_hist_4h,
        taker_buy_ratio_24h=valid_inputs.taker_buy_ratio_24h,
        runup_30d=Decimal("0.05"),
    )
    passed_flat, details_flat = evaluate_short_gates(flat_inputs, SetupLabel.UTAD_DIVERGENCE_SHORT)
    assert not passed_flat
    assert not details_flat["S1"]


# =====================================================================
# 7. 5-Level Execution Brackets
# =====================================================================


def test_build_long_bracket() -> None:
    tick = Decimal("0.01")
    bracket = build_long_bracket(
        close=Decimal("100.00"),
        ema50_4h=Decimal("98.00"),
        poc_30d=Decimal("99.00"),
        atr14_4h=Decimal("2.00"),
        local_compression_high_4h=Decimal("101.50"),
        swing_low_4h=Decimal("96.50"),
        high_30d=Decimal("108.00"),
        vah_30d=Decimal("106.00"),
        tick_size=tick,
    )

    # Long hierarchy: invalidation_stop < retest_entry <= close < breakout_trigger < t1 < t2 < t3
    assert bracket.invalidation_stop < bracket.retest_entry
    assert bracket.breakout_trigger > Decimal("100.00")
    assert bracket.t1 > bracket.breakout_trigger
    assert bracket.t2 > bracket.t1
    assert bracket.t3 > bracket.t2
    assert bracket.asymmetric_rr >= Decimal("2.50")

    # Verify tick normalization
    for p in (bracket.invalidation_stop, bracket.retest_entry, bracket.breakout_trigger, bracket.t1, bracket.t2, bracket.t3):
        assert p % tick == Decimal("0")


def test_build_short_bracket() -> None:
    tick = Decimal("0.01")
    bracket = build_short_bracket(
        close=Decimal("100.00"),
        ema50_4h=Decimal("102.00"),
        poc_30d=Decimal("103.00"),
        atr14_4h=Decimal("2.00"),
        local_consolidation_low_4h=Decimal("98.50"),
        swing_high_4h=Decimal("106.00"),
        low_30d=Decimal("90.00"),
        vah_30d=Decimal("105.00"),
        val_30d=Decimal("94.00"),
        tick_size=tick,
    )

    # Short hierarchy: invalidation_stop > retest_entry >= close > breakout_trigger > t1 > t2 > t3
    assert bracket.invalidation_stop > bracket.retest_entry
    assert bracket.retest_entry >= Decimal("100.00")
    assert bracket.breakout_trigger < Decimal("100.00")
    assert bracket.t1 < bracket.breakout_trigger
    assert bracket.t2 < bracket.t1
    assert bracket.t3 < bracket.t2
    assert bracket.asymmetric_rr >= Decimal("2.50")

    # Verify tick normalization
    for p in (bracket.invalidation_stop, bracket.retest_entry, bracket.breakout_trigger, bracket.t1, bracket.t2, bracket.t3):
        assert p % tick == Decimal("0")


# =====================================================================
# 8. Regime-Adaptive Scoring, MOEA Conviction, and 3-Tier EV_R
# =====================================================================


def test_calculate_adx_subscore() -> None:
    # Pre-breakout accumulation: lower ADX rewarded
    sub_accum = calculate_adx_subscore(
        SetupLabel.COIL_SPRING_LONG,
        adx_4h=Decimal("12.0"),
        adx_4h_prev=Decimal("13.0"),
        dmi_plus=Decimal("20.0"),
        dmi_minus=Decimal("18.0"),
    )
    assert sub_accum == Decimal("100.0")

    # Trend expansion: higher ADX rewarded
    sub_trend = calculate_adx_subscore(
        SetupLabel.BREAKOUT_LONG,
        adx_4h=Decimal("30.0"),
        adx_4h_prev=Decimal("28.0"),
        dmi_plus=Decimal("25.0"),
        dmi_minus=Decimal("15.0"),
    )
    # 4.0 * (30 - 15) + 20 bonus = 80.0
    assert sub_trend == Decimal("80.0")


def test_compute_p_win_and_conviction() -> None:
    p_win_long, conv_long = compute_p_win_and_conviction(
        direction=Direction.LONG,
        archetype=SetupLabel.COIL_SPRING_LONG,
        quantitative_score=Decimal("80.0"),
        coi=Decimal("50.0"),
    )
    # W_BASE for COIL_SPRING_LONG is 0.58. score_delta = (80-70)/30 = 0.333.
    # p_win = 0.58 + 0.08 * 0.333 = 0.607
    assert p_win_long > Decimal("0.58")
    assert conv_long >= Decimal("70.0")


def test_compute_3tier_ev_r_and_grade() -> None:
    ev_r = compute_3tier_ev_r(
        p_win=Decimal("0.60"),
        r_t1=Decimal("1.80"),
        r_t2=Decimal("3.20"),
        r_t3=Decimal("5.50"),
    )
    # Expectancy should be positive and attractive
    assert ev_r >= Decimal("0.60")

    # Grade A verification
    grade_a = classify_opportunity_grade(
        ev_r=Decimal("1.65"),
        conviction=Decimal("78.0"),
        asymmetric_rr=Decimal("3.00"),
    )
    assert grade_a == OpportunityGrade.GRADE_A

    # Grade B verification
    grade_b = classify_opportunity_grade(
        ev_r=Decimal("1.25"),
        conviction=Decimal("72.0"),
        asymmetric_rr=Decimal("2.60"),
    )
    assert grade_b == OpportunityGrade.GRADE_B


# =====================================================================
# 9. Dual-Loop Scheduler Cycle Offsets
# =====================================================================


def test_dual_loop_scheduler_timing() -> None:
    # 15-min cycle at 12:04:10 should target 12:15:03 -> ~653 seconds
    now_fast = datetime(2026, 10, 14, 12, 4, 10, tzinfo=UTC)
    wait_fast = seconds_until_next_fast_cycle(now_fast, grace_seconds=3)
    target_fast = now_fast + timedelta(seconds=wait_fast)
    assert target_fast.minute == 15
    assert target_fast.second == 3

    # Settlement cycle at 07:45:00 should target 08:00:30 UTC -> ~930 seconds
    now_macro = datetime(2026, 10, 14, 7, 45, 0, tzinfo=UTC)
    wait_macro = seconds_until_next_settlement_cycle(now_macro, grace_seconds=30)
    target_macro = now_macro + timedelta(seconds=wait_macro)
    assert target_macro.hour == 8
    assert target_macro.minute == 0
    assert target_macro.second == 30


# =====================================================================
# 10. Continuous Multi-Factor Quant Scoring Engine
# =====================================================================


def test_continuous_long_factor_scores_tight_compression() -> None:
    factors = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.09"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.85"),
        close=Decimal("100.00"),
        poc_30d=Decimal("99.50"),
        val_30d=Decimal("95.00"),
        vah_30d=Decimal("103.00"),
        pos_in_range_30d=Decimal("0.70"),
        ema50_4h=Decimal("98.00"),
        ema200_4h=Decimal("92.00"),
        resistance_absorption_score=Decimal("80.0"),
        cvd_zscore=Decimal("1.2"),
        oi_change_7d=Decimal("0.15"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.54"),
        funding_rate=Decimal("0.00002"),
        ema20_4h=Decimal("99.00"),
        macd_hist_4h=Decimal("0.10"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("80.0"),
        btc_residual_alpha=Decimal("0.02"),
        rvol_4h=Decimal("1.10"),
        asymmetric_rr=Decimal("3.20"),
        dist_to_trigger=Decimal("0.03"),
        macro_multiplier=Decimal("1.0"),
    )
    assert factors.volatility_score == Decimal("100.0")
    assert factors.value_score >= Decimal("80.0")
    assert factors.absorption_score >= Decimal("80.0")
    assert factors.trend_momentum_score >= Decimal("75.0")
    assert factors.asymmetry_score >= Decimal("90.0")
    assert factors.final_composite_score >= Decimal("80.0")


def test_continuous_long_factor_scores_breakout_expansion() -> None:
    factors = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.25"),
        bb_inside_kc=False,
        vol_contraction_ratio=Decimal("1.20"),
        close=Decimal("105.00"),
        poc_30d=Decimal("98.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("104.00"),
        pos_in_range_30d=Decimal("0.95"),
        ema50_4h=Decimal("97.00"),
        ema200_4h=Decimal("90.00"),
        resistance_absorption_score=Decimal("85.0"),
        cvd_zscore=Decimal("2.1"),
        oi_change_7d=Decimal("0.05"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.56"),
        funding_rate=Decimal("0.00003"),
        ema20_4h=Decimal("101.00"),
        macd_hist_4h=Decimal("0.25"),
        macd_hist_4h_prev=Decimal("0.12"),
        adx_subscore=Decimal("85.0"),
        btc_residual_alpha=Decimal("0.04"),
        rvol_4h=Decimal("2.50"),
        asymmetric_rr=Decimal("2.80"),
        dist_to_trigger=Decimal("0.01"),
        macro_multiplier=Decimal("1.0"),
    )
    assert factors.trend_momentum_score >= Decimal("85.0")
    assert factors.value_score >= Decimal("85.0")
    assert factors.asymmetry_score >= Decimal("85.0")
    assert factors.final_composite_score >= Decimal("75.0")


def test_qusdt_anomaly_prevention_and_short_squeeze_long() -> None:
    """Verify that an asset like QUSDT with negative funding, surging OI, and positive taker flow

    is strictly VETOED as a Short and correctly classified as a SHORT_SQUEEZE_ABSORPTION_LONG.
    """
    # 1. Verify Short Gate Hard Veto
    short_inputs = ShortGateInputs(
        close=Decimal("0.0502"),
        ema50_4h=Decimal("0.0485"),
        high_7d=Decimal("0.0560"),
        range_height_30d=Decimal("0.85"),
        ret_7d=Decimal("0.25"),
        ret_14d=Decimal("0.45"),
        high_30d=Decimal("0.0560"),
        low_30d=Decimal("0.0210"),
        days_since_30d_high=Decimal("1.0"),
        ema20_4h=Decimal("0.0495"),
        atr14_4h=Decimal("0.0025"),
        ret_24h=Decimal("0.08"),
        rsi_1h=Decimal("72.0"),
        rsi_4h=Decimal("87.0"),
        has_bearish_div=False,
        has_climax_blowoff=True,
        poc_30d=Decimal("0.0509"),
        macd_hist_4h=Decimal("0.001"),
        taker_buy_ratio_24h=Decimal("0.517"),
        funding_rate_8h=Decimal("-0.000093"),  # -0.93 bps (shorts paying longs)
        oi_delta_7d=Decimal("3.71"),  # +371%
        oi_delta_1h=Decimal("0.0546"),  # +5.46%
        taker_buy_ratio_1h=Decimal("0.52"),
        high_24h=Decimal("0.0560"),
        rsi_1d=Decimal("93.5"),
    )
    passed_short, gate_details = evaluate_short_gates(short_inputs, SetupLabel.EXHAUSTION_REVERSAL_SHORT)
    # MUST BE VETOED!
    assert not passed_short

    # 2. Verify Long Archetype Classification
    long_features = LongArchetypeFeatures(
        close=Decimal("0.0502"),
        low_24h=Decimal("0.0480"),
        ema50_4h=Decimal("0.0485"),
        poc_30d=Decimal("0.0509"),
        vah_30d=Decimal("0.0540"),
        atr14_4h=Decimal("0.0025"),
        vcr_4h=Decimal("0.85"),
        oi_delta_7d=Decimal("3.71"),
        oi_delta_4h=Decimal("0.12"),
        ret_7d=Decimal("0.25"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.18"),
        local_swing_high_4h=Decimal("0.0530"),
        rvol_4h=Decimal("1.85"),
        norm_momentum_1h=Decimal("0.65"),
        btc_residual_alpha_24h=Decimal("0.05"),
        macd_hist_4h=Decimal("0.001"),
        macd_hist_4h_prev=Decimal("0.0005"),
        macd_hist_1h=Decimal("0.0008"),
        taker_buy_ratio_4h=Decimal("0.517"),
        funding_rate_8h=Decimal("-0.000093"),
        cvd_24h=Decimal("519412462"),
    )
    arch = classify_long_archetype(long_features)
    assert arch == SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG

    # 3. Verify Long Gate Evaluation
    long_inputs = LongGateInputs(
        close=Decimal("0.0502"),
        ema50_4h=Decimal("0.0485"),
        ema20_1h=Decimal("0.0498"),
        ema200_4h=Decimal("0.0420"),
        low_24h=Decimal("0.0480"),
        atr14_4h=Decimal("0.0025"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("0.0495"),
        excursion_72h=Decimal("0.20"),
        ret_24h=Decimal("0.05"),
        rsi_1h=Decimal("65.0"),
        rsi_4h=Decimal("68.0"),
        high_30d=Decimal("0.0560"),
        low_30d=Decimal("0.0210"),
        poc_30d=Decimal("0.0509"),
        val_30d=Decimal("0.0350"),
        vah_30d=Decimal("0.0540"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.18"),
        rvol_4h=Decimal("1.85"),
        oi_delta_7d=Decimal("3.71"),
        oi_delta_4h=Decimal("0.12"),
        taker_buy_ratio_4h=Decimal("0.517"),
        funding_rate_8h=Decimal("-0.000093"),
    )
    passed_long, details = evaluate_long_gates(long_inputs, SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG)
    assert passed_long
    assert all(details.values())


def test_multi_system_confluence_scoring_and_penalization() -> None:
    """Verify that candidate scoring rewards broad multi-system harmony and penalizes 1-2 system wonders."""
    # Candidate A: Balanced 5-system institutional confluence
    balanced = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.10"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.85"),
        close=Decimal("100.0"),
        poc_30d=Decimal("99.0"),
        val_30d=Decimal("94.0"),
        vah_30d=Decimal("103.0"),
        pos_in_range_30d=Decimal("0.75"),
        ema50_4h=Decimal("96.0"),
        ema200_4h=Decimal("90.0"),
        resistance_absorption_score=Decimal("80.0"),
        cvd_zscore=Decimal("1.5"),
        oi_change_7d=Decimal("0.10"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.55"),
        funding_rate=Decimal("0.00002"),
        ema20_4h=Decimal("98.0"),
        macd_hist_4h=Decimal("0.15"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("75.0"),
        btc_residual_alpha=Decimal("0.03"),
        rvol_4h=Decimal("1.20"),
        asymmetric_rr=Decimal("3.0"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )

    # Candidate B: Lopsided 2-system wonder (Extreme squeeze & R:R, but failing structure & negative CVD)
    lopsided = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.08"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.70"),
        close=Decimal("92.0"),  # Below POC and VAL
        poc_30d=Decimal("98.0"),
        val_30d=Decimal("95.0"),
        vah_30d=Decimal("103.0"),
        pos_in_range_30d=Decimal("0.15"),
        ema50_4h=Decimal("97.0"),  # Below EMA50
        ema200_4h=Decimal("105.0"),
        resistance_absorption_score=Decimal("20.0"),  # Hostile resistance
        cvd_zscore=Decimal("-2.5"),  # Aggressive selling
        oi_change_7d=Decimal("-0.20"),
        oi_change_4h=Decimal("-0.05"),
        taker_buy_ratio_4h=Decimal("0.38"),
        funding_rate=Decimal("0.00030"),
        ema20_4h=Decimal("93.0"),
        macd_hist_4h=Decimal("-0.40"),
        macd_hist_4h_prev=Decimal("-0.20"),
        adx_subscore=Decimal("25.0"),
        btc_residual_alpha=Decimal("-0.08"),
        rvol_4h=Decimal("0.40"),
        asymmetric_rr=Decimal("3.5"),  # Huge theoretical R:R
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )

    # Balanced candidate must significantly outscore lopsided 2-system candidate
    assert balanced.final_composite_score >= Decimal("80.0")
    assert lopsided.final_composite_score < Decimal("55.0")
    assert balanced.final_composite_score > lopsided.final_composite_score + Decimal("25.0")

    # Checklist institutional consensus weighting
    _, conv_5sys = compute_p_win_and_conviction(
        direction=Direction.LONG,
        archetype=SetupLabel.COIL_SPRING_LONG,
        quantitative_score=Decimal("80.0"),
        checklist_passed=5,
    )
    _, conv_1sys = compute_p_win_and_conviction(
        direction=Direction.LONG,
        archetype=SetupLabel.COIL_SPRING_LONG,
        quantitative_score=Decimal("80.0"),
        checklist_passed=1,
    )
    assert conv_5sys > conv_1sys + Decimal("7.0")


def test_continuous_volume_momentum_and_oi_confirmation_scoring() -> None:
    """Verify that confirmed volume dry-up/surge, accelerating MACD/alpha, and expanding OI drive scores."""
    # Confirmed setup: Strong OI growth (+12%), heavy taker buying (55%), low funding (0.00001),
    # volume dry-up (0.80) + breakout volume surge (rvol 1.6), accelerating positive MACD, positive alpha
    confirmed = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.10"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.80"),
        close=Decimal("100.00"),
        poc_30d=Decimal("99.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("103.00"),
        pos_in_range_30d=Decimal("0.75"),
        ema50_4h=Decimal("96.00"),
        ema200_4h=Decimal("90.00"),
        resistance_absorption_score=Decimal("80.0"),
        cvd_zscore=Decimal("1.8"),
        oi_change_7d=Decimal("0.12"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("0.55"),
        funding_rate=Decimal("0.00001"),
        ema20_4h=Decimal("98.00"),
        macd_hist_4h=Decimal("0.20"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("80.0"),
        btc_residual_alpha=Decimal("0.03"),
        rvol_4h=Decimal("1.60"),
        asymmetric_rr=Decimal("3.00"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )

    assert confirmed.volume_score >= Decimal("80.0")
    assert confirmed.momentum_score >= Decimal("75.0")
    assert confirmed.derivatives_score >= Decimal("75.0")
    assert confirmed.absorption_score >= Decimal("85.0")

    # Unconfirmed setup: OI capital flight (-8%), seller taker dominance (44%), overheated funding (0.00025),
    # weak volume (rvol 0.50, vol contraction 1.35), decelerating negative MACD, negative alpha
    unconfirmed = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.10"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("1.35"),
        close=Decimal("100.00"),
        poc_30d=Decimal("99.00"),
        val_30d=Decimal("94.00"),
        vah_30d=Decimal("103.00"),
        pos_in_range_30d=Decimal("0.75"),
        ema50_4h=Decimal("96.00"),
        ema200_4h=Decimal("90.00"),
        resistance_absorption_score=Decimal("60.0"),
        cvd_zscore=Decimal("-1.5"),
        oi_change_7d=Decimal("-0.08"),
        oi_change_4h=Decimal("-0.04"),
        taker_buy_ratio_4h=Decimal("0.44"),
        funding_rate=Decimal("0.00025"),
        ema20_4h=Decimal("98.00"),
        macd_hist_4h=Decimal("-0.20"),
        macd_hist_4h_prev=Decimal("-0.05"),
        adx_subscore=Decimal("40.0"),
        btc_residual_alpha=Decimal("-0.04"),
        rvol_4h=Decimal("0.50"),
        asymmetric_rr=Decimal("3.00"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )

    assert unconfirmed.volume_score <= Decimal("40.0")
    assert unconfirmed.momentum_score <= Decimal("40.0")
    assert unconfirmed.derivatives_score <= Decimal("40.0")
    assert confirmed.final_composite_score > unconfirmed.final_composite_score + Decimal("20.0")


def test_amt_value_area_acceptance_gate_l4() -> None:
    from algotrader.domain.screening_gates import evaluate_long_gates, LongGateInputs

    # Standard inputs where candidate is comfortably accepted inside Value Area (between VAL and POC)
    inputs_in_va = LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("98.00"),
        ema20_1h=Decimal("99.00"),
        ema200_4h=Decimal("95.00"),
        low_24h=Decimal("98.50"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.80"),
        ema20_4h=Decimal("98.50"),
        excursion_72h=Decimal("0.10"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55.0"),
        rsi_4h=Decimal("52.0"),
        rsi_1d=Decimal("50.0"),
        rsi_1w=Decimal("48.0"),
        high_30d=Decimal("115.00"),
        low_30d=Decimal("92.00"),
        poc_30d=Decimal("108.00"),  # Close (100) is below POC (108), but above VAL (96)
        val_30d=Decimal("96.00"),
        vah_30d=Decimal("112.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
    )

    # Any accumulation or emerging momentum archetype must pass Gate L4 when accepted in value
    for arch in ("OI_ACCUMULATION_LONG", "VCP_COMPRESSION_LONG", "COIL_SPRING_LONG", "MOMENTUM_EMERGING_LONG", "RANGE_BOTTOM_ACCUMULATION_LONG"):
        passed, details = evaluate_long_gates(inputs_in_va, archetype=arch)
        assert details["L4"] is True, f"Archetype {arch} must pass Gate L4 when accepted in Value Area"

    # Failed acceptance: price collapsed below Value Area Low and below 30d swing low
    inputs_breakdown = LongGateInputs(
        close=Decimal("85.00"),  # Far below val (96.0) and low (92.00)
        ema50_4h=Decimal("98.00"),
        ema20_1h=Decimal("90.00"),
        ema200_4h=Decimal("95.00"),
        low_24h=Decimal("84.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.80"),
        ema20_4h=Decimal("92.00"),
        excursion_72h=Decimal("0.10"),
        ret_24h=Decimal("-0.10"),
        rsi_1h=Decimal("35.0"),
        rsi_4h=Decimal("32.0"),
        rsi_1d=Decimal("30.0"),
        rsi_1w=Decimal("38.0"),
        high_30d=Decimal("115.00"),
        low_30d=Decimal("92.00"),
        poc_30d=Decimal("108.00"),
        val_30d=Decimal("96.00"),
        vah_30d=Decimal("112.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
    )
    passed, details = evaluate_long_gates(inputs_breakdown, archetype="OI_ACCUMULATION_LONG")
    assert details["L4"] is False, "Collapsing below VAL and 30d low must fail Gate L4"





