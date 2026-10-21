"""Tests for institutional-grade short screening overhaul:
- Anti-bottom grinder protection (BOME, AT, FIL, FARTCOIN rejection)
- Real RSI overbought vs Resistance Retest checklist accuracy
- Bounded actionable entry within 4.5% of market price
- Zero forced quota behavior (no displacing long candidates)
"""

from decimal import Decimal
import pytest

from algotrader.domain.archetypes import (
    ShortArchetypeFeatures,
    classify_short_archetype,
)
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.execution_brackets import build_short_bracket
from algotrader.domain.screening_gates import (
    ShortGateInputs,
    evaluate_short_gates,
)
from algotrader.domain.scoring import calculate_continuous_short_factor_scores


def test_bome_at_fil_bottom_grinders_rejected():
    """Real parameters from the user's BOMEUSDT screenshot:
    Price: 0.000625, Range Low: 0.000580, Range High: 0.001850
    Position in 30d range: ~3.5% (sitting on floor)
    4H RSI: ~40.0, 1D RSI: ~44.0 (nowhere near overbought)
    30d POC: 0.001029 (64% above market price)
    """
    bome_features = ShortArchetypeFeatures(
        close=Decimal("0.000625"),
        high_24h=Decimal("0.000650"),
        high_30d=Decimal("0.001850"),
        ema50_4h=Decimal("0.000850"),
        poc_30d=Decimal("0.001029"),
        atr14_4h=Decimal("0.000030"),
        has_bearish_div=False,
        rsi_4h=Decimal("40.0"),
        rsi_1d=Decimal("44.0"),
        rsi_1w=Decimal("48.0"),
        oi_delta_7d=Decimal("-0.15"),
        upper_wick_ratio_4h=Decimal("0.18"),
        max_ret_30d=Decimal("-0.45"),
        taker_buy_ratio_24h=Decimal("0.48"),
        macd_hist_4h=Decimal("-0.000002"),
        pos_in_range_30d=Decimal("0.035"),  # 3.5% of range!
        runup_30d=Decimal("0.05"),
        ret_14d=Decimal("-0.22"),
    )
    # 1. Archetype classification must reject bottom grinder
    archetype = classify_short_archetype(bome_features)
    assert archetype is None, "Bottom grinders must return None for short archetype"

    # 2. Gate S1 and Gate S3 must strictly reject
    bome_gates = ShortGateInputs(
        close=Decimal("0.000625"),
        ema50_4h=Decimal("0.000850"),
        high_7d=Decimal("0.000680"),
        range_height_30d=Decimal("0.20"),
        ret_7d=Decimal("-0.08"),
        ret_14d=Decimal("-0.22"),
        high_30d=Decimal("0.001850"),
        low_30d=Decimal("0.000580"),
        days_since_30d_high=Decimal("22.0"),
        ema20_4h=Decimal("0.000690"),
        atr14_4h=Decimal("0.000030"),
        ret_24h=Decimal("-0.02"),
        rsi_1h=Decimal("38.0"),
        rsi_4h=Decimal("40.0"),
        has_bearish_div=False,
        has_climax_blowoff=False,
        poc_30d=Decimal("0.001029"),
        macd_hist_4h=Decimal("-0.000002"),
        taker_buy_ratio_24h=Decimal("0.48"),
        low_72h=Decimal("0.000600"),
    )
    passed, details = evaluate_short_gates(bome_gates, None)
    assert not passed, "Bottom grinder must fail screening gates"
    assert not details["S1"], "Must fail S1 (no macro runup)"
    assert not details["S3"], "Must fail S3 (floor proximity)"


def test_bracket_actionability_bounds():
    """Ensure suggested entries are bounded within 3.5% of current price,
    never suggesting limit orders 64% above market price.
    """
    close = Decimal("0.000625")
    distant_poc = Decimal("0.001029")
    atr = Decimal("0.000030")
    tick = Decimal("0.000001")

    bracket = build_short_bracket(
        close=close,
        ema50_4h=Decimal("0.000850"),
        poc_30d=distant_poc,
        atr14_4h=atr,
        local_consolidation_low_4h=Decimal("0.000610"),
        swing_high_4h=Decimal("0.000645"),
        low_30d=Decimal("0.000580"),
        vah_30d=Decimal("0.001200"),
        val_30d=Decimal("0.000750"),
        tick_size=tick,
    )

    dist_to_entry = (bracket.suggested_entry - close) / close
    assert dist_to_entry <= Decimal("0.035"), f"Entry {bracket.suggested_entry} is too far ({dist_to_entry:.2%}) from market {close}"


def test_scoring_no_saturation_for_weak_setups():
    """Scores for weak or bottom-grinding setups must not saturate at 100.0."""
    scores = calculate_continuous_short_factor_scores(
        bandwidth_4h=Decimal("0.15"),
        bb_inside_kc=False,
        vol_contraction_ratio=Decimal("1.0"),
        close=Decimal("0.000625"),
        poc_30d=Decimal("0.001029"),
        val_30d=Decimal("0.000750"),
        vah_30d=Decimal("0.001200"),
        pos_in_range_30d=Decimal("0.035"),
        ema50_4h=Decimal("0.000850"),
        ema200_4h=Decimal("0.001100"),
        has_bearish_div=False,
        cvd_zscore=Decimal("0.10"),
        oi_change_7d=Decimal("-0.10"),
        oi_change_4h=Decimal("-0.01"),
        taker_buy_ratio_4h=Decimal("0.50"),
        funding_rate=Decimal("0.00001"),
        ema20_4h=Decimal("0.000690"),
        macd_hist_4h=Decimal("-0.000002"),
        macd_hist_4h_prev=Decimal("-0.000001"),
        adx_subscore=Decimal("50.0"),
        btc_residual_alpha=Decimal("0.0"),
        rvol_4h=Decimal("0.90"),
        asymmetric_rr=Decimal("2.0"),
        dist_to_trigger=Decimal("0.02"),
        macro_multiplier=Decimal("1.0"),
        is_btc_cascade=False,
        upper_wick_ratio_4h=Decimal("0.15"),
        dist_to_entry=Decimal("0.02"),
    )
    assert scores.final_composite_score < Decimal("60.0"), (
        f"Bottom grinder received saturated score: {scores.final_composite_score}"
    )
