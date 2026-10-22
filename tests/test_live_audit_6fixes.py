from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from algotrader.domain.crypto_screener_models import (
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    Direction,
    SetupLabel,
)
from algotrader.domain.models import Candle, Instrument, MarketKind, Venue
from algotrader.domain.indicators import taker_buy_ratio_from_candles
from algotrader.domain.archetypes import (
    LongArchetypeFeatures,
    ShortArchetypeFeatures,
    classify_long_archetype,
    classify_short_archetype,
)
from algotrader.domain.screening_gates import (
    LongGateInputs,
    evaluate_long_gates,
)
from algotrader.domain.execution_brackets import build_long_bracket
from algotrader.domain.scoring import (
    calculate_continuous_long_factor_scores,
    calculate_continuous_short_factor_scores,
)

UTC = timezone.utc


def test_fix1_taker_ratio_formula_scale_and_precision() -> None:
    """Fix 1: taker_buy_sell_ratio must be buy_vol / sell_vol centered at 1.00, rounded to 4 decimals."""
    now = datetime(2026, 10, 22, tzinfo=UTC)
    # Candle with total quote volume 1,000,000 and taker buy quote volume 520,000
    # Seller volume = 480,000. Ratio = 520,000 / 480,000 = 1.0833 (not 0.5200!)
    c = Candle(
        opened_at=now,
        closed_at=now + timedelta(hours=1),
        open=Decimal("10.0"),
        high=Decimal("10.5"),
        low=Decimal("9.8"),
        close=Decimal("10.2"),
        volume=Decimal("100000"),
        quote_volume=Decimal("1000000"),
        taker_buy_volume=Decimal("52000"),
        taker_buy_quote_volume=Decimal("520000"),
        closed=True,
    )
    ratio = taker_buy_ratio_from_candles([c])
    assert ratio == Decimal("1.0833")
    assert str(ratio) == "1.0833"

    # Fallback when volumes are zero or empty
    assert taker_buy_ratio_from_candles([]) == Decimal("1.0000")


def test_fix2_missing_7d_oi_caps_derivatives_score_at_40() -> None:
    """Fix 2: When oi_change_7d is None ("N/A"), derivatives score must NOT be 78-80; capped at 40.0."""
    long_scores = calculate_continuous_long_factor_scores(
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
        oi_change_7d=None,  # Missing Stage 2 derivatives
        oi_change_4h=None,
        taker_buy_ratio_4h=Decimal("1.20"),
        funding_rate=None,
        ema20_4h=Decimal("98.00"),
        macd_hist_4h=Decimal("0.20"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("80.0"),
        btc_residual_alpha=Decimal("0.00"),
        rvol_4h=Decimal("1.60"),
        asymmetric_rr=Decimal("3.00"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )
    assert long_scores.derivatives_score == Decimal("40.0")

    short_scores = calculate_continuous_short_factor_scores(
        bandwidth_4h=Decimal("0.15"),
        bb_inside_kc=False,
        vol_contraction_ratio=Decimal("0.90"),
        close=Decimal("5.00"),
        poc_30d=Decimal("5.10"),
        val_30d=Decimal("4.60"),
        vah_30d=Decimal("5.30"),
        pos_in_range_30d=Decimal("0.70"),
        ema50_4h=Decimal("5.05"),
        ema200_4h=Decimal("4.50"),
        has_bearish_div=True,
        cvd_zscore=Decimal("-1.2"),
        oi_change_7d=None,  # Missing Stage 2 derivatives
        oi_change_4h=None,
        taker_buy_ratio_4h=Decimal("0.85"),
        funding_rate=None,
        ema20_4h=Decimal("5.02"),
        macd_hist_4h=Decimal("-0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        adx_subscore=Decimal("60.0"),
        btc_residual_alpha=Decimal("0.00"),
        rvol_4h=Decimal("1.10"),
        asymmetric_rr=Decimal("2.50"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )
    assert short_scores.derivatives_score == Decimal("40.0")


def test_fix3_alpha_score_normalization_neutral_at_50() -> None:
    """Fix 3: When residual_return_24h is 0.00%, alpha score must be 50.0 (neutral), NOT 100.0!"""
    long_scores = calculate_continuous_long_factor_scores(
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
        cvd_zscore=Decimal("1.0"),
        oi_change_7d=Decimal("0.05"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.10"),
        funding_rate=Decimal("0.00001"),
        ema20_4h=Decimal("98.00"),
        macd_hist_4h=Decimal("0.10"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("70.0"),
        btc_residual_alpha=Decimal("0.00"),  # 0.00% residual return
        rvol_4h=Decimal("1.20"),
        asymmetric_rr=Decimal("2.50"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )
    assert long_scores.alpha_score == Decimal("50.0")

    short_scores = calculate_continuous_short_factor_scores(
        bandwidth_4h=Decimal("0.15"),
        bb_inside_kc=False,
        vol_contraction_ratio=Decimal("0.90"),
        close=Decimal("5.00"),
        poc_30d=Decimal("5.10"),
        val_30d=Decimal("4.60"),
        vah_30d=Decimal("5.30"),
        pos_in_range_30d=Decimal("0.70"),
        ema50_4h=Decimal("5.05"),
        ema200_4h=Decimal("4.50"),
        has_bearish_div=True,
        cvd_zscore=Decimal("-1.2"),
        oi_change_7d=Decimal("0.05"),
        oi_change_4h=Decimal("0.01"),
        taker_buy_ratio_4h=Decimal("0.85"),
        funding_rate=Decimal("0.0001"),
        ema20_4h=Decimal("5.02"),
        macd_hist_4h=Decimal("-0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        adx_subscore=Decimal("60.0"),
        btc_residual_alpha=Decimal("0.00"),  # 0.00% residual return
        rvol_4h=Decimal("1.10"),
        asymmetric_rr=Decimal("2.50"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
    )
    assert short_scores.alpha_score == Decimal("50.0")


def test_fix4_veto_crashing_1h_oi_and_below_val() -> None:
    """Fix 4: Long with crashing 1h OI (< -2.5%) or price below VAL is vetoed."""
    # Test Gate L6 acute OI liquidation veto
    inputs = LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("95.00"),
        ema20_1h=Decimal("98.00"),
        ema200_4h=Decimal("90.00"),
        low_24h=Decimal("98.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("98.00"),
        excursion_72h=Decimal("0.04"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55.0"),
        rsi_4h=Decimal("58.0"),
        rsi_1d=Decimal("52.0"),
        rsi_1w=Decimal("50.0"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("90.00"),
        poc_30d=Decimal("99.00"),
        val_30d=Decimal("95.00"),
        vah_30d=Decimal("105.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
        rvol_4h=Decimal("1.30"),
        oi_delta_7d=Decimal("0.05"),
        oi_delta_4h=Decimal("0.01"),
        oi_delta_1h=Decimal("-0.035"),  # Acute liquidation (-3.5% < -2.5%)
        has_kinetic_ignition=True,
    )
    passed, details = evaluate_long_gates(inputs, SetupLabel.OI_ACCUMULATION_LONG)
    assert not passed
    assert details["L6"] is False

    # Below VAL veto for RANGE_BOTTOM_ACCUMULATION_LONG and OI_ACCUMULATION_LONG
    inputs_below_val = LongGateInputs(
        close=Decimal("93.00"),  # Below VAL $95.00
        ema50_4h=Decimal("92.00"),
        ema20_1h=Decimal("93.00"),
        ema200_4h=Decimal("90.00"),
        low_24h=Decimal("92.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("92.50"),
        excursion_72h=Decimal("0.04"),
        ret_24h=Decimal("0.01"),
        rsi_1h=Decimal("50.0"),
        rsi_4h=Decimal("50.0"),
        rsi_1d=Decimal("48.0"),
        rsi_1w=Decimal("45.0"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("90.00"),
        poc_30d=Decimal("99.00"),
        val_30d=Decimal("95.00"),
        vah_30d=Decimal("105.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.12"),
        rvol_4h=Decimal("1.25"),
        oi_delta_7d=Decimal("0.05"),
        oi_delta_4h=Decimal("0.01"),
        oi_delta_1h=Decimal("0.005"),
        has_kinetic_ignition=True,
    )
    passed_val, details_val = evaluate_long_gates(inputs_below_val, SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG)
    assert not passed_val
    assert details_val["L4"] is False


def test_fix5_cap_trigger_and_targets_at_poc_when_close_below_poc() -> None:
    """Fix 5: When Close < 30d_POC, immediate overhead resistance is POC (Target 1), trigger is capped, R:R is capped at 5.0."""
    close = Decimal("0.3032")
    poc = Decimal("0.3346")
    vah = Decimal("0.4373")
    val = Decimal("0.2800")
    local_high = Decimal("0.3500")  # Far above close * 1.08 = 0.3274
    atr = Decimal("0.0150")

    bracket = build_long_bracket(
        close=close,
        ema50_4h=Decimal("0.2950"),
        swing_low_4h=Decimal("0.2900"),
        high_30d=Decimal("0.4500"),
        poc_30d=poc,
        vah_30d=vah,
        atr14_4h=atr,
        tick_size=Decimal("0.0001"),
        local_compression_high_4h=local_high,
        val_30d=val,
    )

    # Breakout trigger capped at <= close * 1.08 = 0.3274
    assert bracket.breakout_trigger <= (close * Decimal("1.08")).quantize(Decimal("0.0001"))
    # Target 1 is POC ($0.3346), Target 2 is VAH ($0.4373)
    assert bracket.t1 == poc
    assert bracket.t2 == vah
    # R:R is evaluated to Target 1 and capped at <= 5.0
    assert bracket.asymmetric_rr <= Decimal("5.00")


def test_fix6_short_squeeze_long_requires_strictly_negative_funding() -> None:
    """Fix 6: SHORT_SQUEEZE_ABSORPTION_LONG requires funding_rate_bps <= -0.50 bps."""
    # NEARUSDT had positive funding +0.13 bps (+0.000013) -> MUST be rejected
    features_positive_funding = LongArchetypeFeatures(
        close=Decimal("5.00"),
        low_24h=Decimal("4.80"),
        ema50_4h=Decimal("4.90"),
        poc_30d=Decimal("4.95"),
        vah_30d=Decimal("5.40"),
        atr14_4h=Decimal("0.20"),
        vcr_4h=Decimal("0.85"),
        oi_delta_7d=Decimal("0.15"),
        oi_delta_4h=Decimal("0.03"),
        ret_7d=Decimal("0.05"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        local_swing_high_4h=Decimal("5.10"),
        rvol_4h=Decimal("1.40"),
        norm_momentum_1h=Decimal("0.20"),
        btc_residual_alpha_24h=Decimal("0.03"),
        macd_hist_4h=Decimal("0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        macd_hist_1h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.10"),
        funding_rate_8h=Decimal("0.000013"),  # +0.13 bps (positive!)
    )
    assert classify_long_archetype(features_positive_funding) != SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG

    # Negative funding -1.5 bps (-0.000150) with taker buying >= 1.00 -> Accepted
    features_negative_funding = LongArchetypeFeatures(
        close=Decimal("5.00"),
        low_24h=Decimal("4.80"),
        ema50_4h=Decimal("4.90"),
        poc_30d=Decimal("4.95"),
        vah_30d=Decimal("5.40"),
        atr14_4h=Decimal("0.20"),
        vcr_4h=Decimal("0.85"),
        oi_delta_7d=Decimal("0.15"),
        oi_delta_4h=Decimal("0.03"),
        ret_7d=Decimal("0.05"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.14"),
        local_swing_high_4h=Decimal("5.10"),
        rvol_4h=Decimal("1.40"),
        norm_momentum_1h=Decimal("0.20"),
        btc_residual_alpha_24h=Decimal("0.03"),
        macd_hist_4h=Decimal("0.05"),
        macd_hist_4h_prev=Decimal("0.02"),
        macd_hist_1h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.05"),
        funding_rate_8h=Decimal("-0.000150"),  # -1.5 bps
    )
    assert classify_long_archetype(features_negative_funding) == SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG
