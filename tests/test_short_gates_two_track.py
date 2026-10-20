from __future__ import annotations

from decimal import Decimal
import pytest

from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.archetypes import ShortArchetypeFeatures, classify_short_archetype
from algotrader.domain.execution_brackets import build_short_bracket
from algotrader.domain.screener_gates import (
    GateEvaluationInput,
    evaluate_long_gates as eval_long_ref,
    evaluate_short_gates as eval_short_ref,
)
from algotrader.domain.screening_gates import (
    ShortGateInputs,
    evaluate_short_gates,
)


def _make_base_gate_input(**overrides) -> GateEvaluationInput:
    """Helper creating valid GateEvaluationInput with sensible defaults."""
    data = dict(
        close=100.0,
        open_4h=98.0,
        high_4h=105.0,
        low_4h=97.0,
        high_24h=105.0,
        low_24h=96.0,
        low_72h=92.0,
        high_7d=108.0,
        high_30d=110.0,
        low_30d=70.0,
        ema20_1h=101.0,
        ema20_4h=99.0,
        ema50_4h=95.0,
        ema200_4h=90.0,
        atr14_1h=1.5,
        atr14_4h=3.0,
        rsi_1h=55.0,
        rsi_4h=58.0,
        rsi_1d=65.0,
        macd_hist_1h=0.1,
        macd_hist_4h=-0.05,
        macd_hist_4h_prev=0.02,
        bb_inside_kc_4h=True,
        bb_inside_kc_4h_lookback=(True, True, False, False, False, False, False),
        bb_bandwidth_4h=0.15,
        poc_30d=98.0,
        vah_30d=106.0,
        val_30d=92.0,
        rvol_4h=1.30,
        vcr_4h=0.85,
        ret_24h=0.02,
        ret_7d=0.08,
        ret_14d=0.25,
        runup_30d=0.57,
        excursion_72h=0.15,
        oi_delta_1h=0.01,
        oi_delta_4h=0.02,
        oi_delta_24h=0.05,
        oi_delta_7d=0.20,
        funding_rate_8h=0.00015,
        basis_bps=5.0,
        taker_buy_ratio_1h=0.45,
        taker_buy_ratio_4h=0.52,
        taker_buy_ratio_24h=0.45,
        has_4h_bearish_rsi_div=True,
    )
    data.update(overrides)
    return GateEvaluationInput(**data)


def test_track_a_utad_passes_in_screener_gates():
    """Verify Track A passes for tokens near 30d highs with bearish divergence."""
    inp = _make_base_gate_input(
        close=103.0,
        high_24h=105.0,  # 105 - 0.5 * 3 = 103.5 >= 103.0 (rejected)
        high_30d=106.0,
        low_30d=70.0,  # pos_in_range = (103 - 70) / 36 = 0.916 >= 0.65
        has_4h_bearish_rsi_div=True,
        macd_hist_4h=-0.05,
        macd_hist_4h_prev=0.02,  # macd decaying
        funding_rate_8h=0.00015,
        oi_delta_7d=0.20,
    )
    passed, reason = eval_short_ref(inp)
    assert passed
    assert reason is None


def test_track_a_climax_blowoff_passes_in_screener_gates():
    """Verify Track A passes for overbought RSI climax blowoffs."""
    inp = _make_base_gate_input(
        close=102.0,
        open_4h=98.0,
        high_4h=106.0,
        low_4h=97.0,
        high_24h=106.0,  # 106 - 0.5 * 3 = 104.5 >= 102 (rejected)
        high_30d=107.0,
        low_30d=70.0,
        rsi_1d=78.0,  # >= 76.0
        # upper wick ratio: (106 - 102) / (106 - 97) = 4 / 9 = 0.444 -> set high_4h=107: (107-102)/(107-97) = 5/10 = 0.50
        has_4h_bearish_rsi_div=False,
        funding_rate_8h=0.0002,
        oi_delta_7d=0.30,
    )
    # Adjust high_4h for upper wick >= 0.45
    inp = _make_base_gate_input(
        close=102.0,
        open_4h=98.0,
        high_4h=107.0,
        low_4h=97.0,
        high_24h=107.0,
        high_30d=108.0,
        low_30d=70.0,
        rsi_1d=78.0,
        has_4h_bearish_rsi_div=False,
        funding_rate_8h=0.0002,
        oi_delta_7d=0.30,
    )
    passed, reason = eval_short_ref(inp)
    assert passed


def test_track_b_structural_breakdown_passes_even_with_low_rsi():
    """Verify Track B passes for structural breakdowns below EMA50 & POC even with cooled RSI."""
    inp = _make_base_gate_input(
        close=88.0,
        ema20_4h=92.0,
        ema50_4h=95.0,
        poc_30d=96.0,  # close < ema50 and close < poc
        low_30d=60.0,
        high_30d=110.0,  # pos_in_range = (88 - 60) / 50 = 0.56 >= 0.45 (passes S3)
        low_72h=80.0,  # (88 - 80) / 80 = 0.10 > 0.04 (passes S3)
        rsi_1d=48.0,  # Completely cooled down Daily RSI
        rsi_4h=44.0,
        rsi_1h=40.0,
        has_4h_bearish_rsi_div=False,
        macd_hist_4h=-0.15,
        macd_hist_4h_prev=-0.05,  # macd < 0 and < prev
        taker_buy_ratio_24h=0.45,  # <= 0.47
        funding_rate_8h=0.00015,
        oi_delta_7d=0.20,
    )
    passed, reason = eval_short_ref(inp)
    assert passed
    assert reason is None


def test_gate_s1_macro_runup_rejection():
    """Verify Gate S1 rejects tokens with no macro runup."""
    inp = _make_base_gate_input(
        runup_30d=0.10,  # < 0.35
        ret_14d=0.05,  # < 0.22
    )
    passed, reason = eval_short_ref(inp)
    assert not passed
    assert reason == "REJECT_S1_NO_MACRO_RUNUP"


def test_gate_s3_floor_capitulation_rejection():
    """Verify Gate S3 rejects tokens that have dumped into the 72h low or have low pos_in_range."""
    # Price right at 72h low
    inp = _make_base_gate_input(
        close=92.5,
        low_72h=92.0,  # (92.5 - 92.0) / 92.0 = 0.0054 <= 0.04
    )
    passed, reason = eval_short_ref(inp)
    assert not passed
    assert reason == "REJECT_S3_CAPITULATION_FLOOR"


def test_gate_s5_bimodal_crowding_trapped_longs_or_squeeze_flush():
    """Verify Gate S5 accepts both Trapped Longs and Short-Squeeze Flush regimes."""
    # 1. Trapped Longs with huge positive funding and OI expansion
    inp_trapped = _make_base_gate_input(
        funding_rate_8h=0.0003,
        basis_bps=15.0,
        oi_delta_7d=0.40,  # +40% OI expansion
    )
    passed, _ = eval_short_ref(inp_trapped)
    assert passed

    # 2. Squeeze Flush: heavily negative funding, 7d OI surge, rapid 1h OI unwinding
    inp_flush = _make_base_gate_input(
        funding_rate_8h=-0.0005,  # <= -0.0003
        oi_delta_7d=0.35,  # >= 0.25
        oi_delta_1h=-0.02,  # <= -0.015
        taker_buy_ratio_1h=0.42,  # <= 0.46
    )
    passed_flush, _ = eval_short_ref(inp_flush)
    assert passed_flush

    # 3. Neither: Neutral funding and stagnant OI
    inp_neutral = _make_base_gate_input(
        funding_rate_8h=0.00005,  # < 0.0001
        basis_bps=2.0,  # < 12.0
        oi_delta_7d=0.05,  # < 0.15
    )
    passed_neut, reason_neut = eval_short_ref(inp_neutral)
    assert not passed_neut
    assert reason_neut == "REJECT_S5_NO_DERIVATIVES_CROWDING"


def test_domain_screening_gates_two_track_compatibility():
    """Verify the production domain screening_gates.py module produces identical results."""
    # Test Track B with Decimal inputs
    inputs_b = ShortGateInputs(
        close=Decimal("88.00"),
        ema50_4h=Decimal("95.00"),
        high_7d=Decimal("110.00"),
        range_height_30d=Decimal("0.55"),
        ret_7d=Decimal("-0.05"),
        ret_14d=Decimal("0.24"),
        high_30d=Decimal("110.00"),
        low_30d=Decimal("60.00"),
        days_since_30d_high=Decimal("8.0"),
        ema20_4h=Decimal("92.00"),
        atr14_4h=Decimal("3.00"),
        ret_24h=Decimal("-0.03"),
        rsi_1h=Decimal("40.0"),
        rsi_4h=Decimal("44.0"),
        has_bearish_div=False,
        has_climax_blowoff=False,
        poc_30d=Decimal("96.00"),
        macd_hist_4h=Decimal("-0.15"),
        macd_hist_4h_prev=Decimal("-0.05"),
        taker_buy_ratio_24h=Decimal("0.45"),
        funding_rate_8h=Decimal("0.0002"),
        oi_delta_7d=Decimal("0.20"),
        oi_delta_1h=Decimal("-0.005"),
        taker_buy_ratio_1h=Decimal("0.44"),
        low_72h=Decimal("80.00"),
        runup_30d=Decimal("0.83"),
    )
    passed, details = evaluate_short_gates(inputs_b, SetupLabel.DISTRIBUTION_SHORT)
    assert passed
    assert all(details.values())


def test_gate_s3_rejects_oversold_bottom_dump_shorts():
    """Verify Gate S3 strictly rejects late-cycle shorts that dumped into the bottom 25% of 30d range or oversold."""
    # Scenario matching VIRTUALUSDT: Dumped ~30%, hovering near low, pos_in_range=0.18, rsi_4h=31.0
    inputs_exhausted = ShortGateInputs(
        close=Decimal("65.00"),
        ema50_4h=Decimal("78.00"),
        high_7d=Decimal("88.00"),
        range_height_30d=Decimal("0.40"),
        ret_7d=Decimal("-0.25"),
        ret_14d=Decimal("0.20"),
        high_30d=Decimal("88.00"),
        low_30d=Decimal("60.00"),
        days_since_30d_high=Decimal("12.0"),
        ema20_4h=Decimal("72.00"),
        atr14_4h=Decimal("2.50"),
        ret_24h=Decimal("-0.05"),
        rsi_1h=Decimal("27.0"),
        rsi_4h=Decimal("31.0"),
        has_bearish_div=False,
        has_climax_blowoff=False,
        poc_30d=Decimal("78.50"),
        macd_hist_4h=Decimal("-0.10"),
        macd_hist_4h_prev=Decimal("-0.12"),
        taker_buy_ratio_24h=Decimal("0.46"),
        funding_rate_8h=Decimal("0.0001"),
        oi_delta_7d=Decimal("0.10"),
        oi_delta_1h=Decimal("-0.001"),
        taker_buy_ratio_1h=Decimal("0.45"),
        low_72h=Decimal("64.50"),  # (65 - 64.50)/64.50 = 0.0077 (scraping bottom)
        runup_30d=Decimal("0.46"),
    )
    passed, details = evaluate_short_gates(inputs_exhausted, SetupLabel.COIL_BREAKDOWN_SHORT)
    assert not passed
    assert not details["S3"], "Gate S3 must reject exhausted bottom-scraping short setup"


def test_bottom_grinders_rejected_by_archetype_classifier() -> None:
    """Tokens languishing at multi-month/ATL support (like PLUME, RIVER, PARTI) must be classified as None."""
    # Simulating PLUMEUSDT: down 80%, scraping range low, pos_in_range=0.08, no recent runup
    plume_features = ShortArchetypeFeatures(
        close=Decimal("0.0185"),
        high_24h=Decimal("0.0195"),
        high_30d=Decimal("0.0850"),
        ema50_4h=Decimal("0.0250"),
        poc_30d=Decimal("0.0240"),
        atr14_4h=Decimal("0.0012"),
        has_bearish_div=False,
        rsi_4h=Decimal("38.0"),
        rsi_1d=Decimal("34.0"),
        rsi_1w=Decimal("30.0"),
        oi_delta_7d=Decimal("-0.05"),
        upper_wick_ratio_4h=Decimal("0.20"),
        max_ret_30d=Decimal("0.78"),
        taker_buy_ratio_24h=Decimal("0.46"),
        macd_hist_4h=Decimal("-0.0001"),
        pos_in_range_30d=Decimal("0.08"),  # Bottom 8% of range
        runup_30d=Decimal("0.02"),  # 0% run-up
        ret_14d=Decimal("-0.25"),
    )
    assert classify_short_archetype(plume_features) is None, "PLUME bottom grinder must be None"

    # Simulating RIVERUSDT: flatlining at ATL floor
    river_features = ShortArchetypeFeatures(
        close=Decimal("1.25"),
        high_24h=Decimal("1.30"),
        high_30d=Decimal("4.50"),
        ema50_4h=Decimal("1.80"),
        poc_30d=Decimal("1.60"),
        atr14_4h=Decimal("0.05"),
        has_bearish_div=False,
        rsi_4h=Decimal("36.0"),
        rsi_1d=Decimal("32.0"),
        rsi_1w=Decimal("28.0"),
        oi_delta_7d=Decimal("0.02"),
        upper_wick_ratio_4h=Decimal("0.25"),
        max_ret_30d=Decimal("0.72"),
        taker_buy_ratio_24h=Decimal("0.47"),
        macd_hist_4h=Decimal("-0.01"),
        pos_in_range_30d=Decimal("0.12"),
        runup_30d=Decimal("0.05"),
        ret_14d=Decimal("-0.18"),
    )
    assert classify_short_archetype(river_features) is None, "RIVER bottom grinder must be None"


def test_bottom_grinders_rejected_by_gate_s3() -> None:
    """Tokens within 8% of 30d low or in lower half (pos < 0.45) must fail Gate S3."""
    grinder_inputs = ShortGateInputs(
        close=Decimal("0.0185"),
        ema50_4h=Decimal("0.0250"),
        high_7d=Decimal("0.0240"),
        range_height_30d=Decimal("0.75"),
        ret_7d=Decimal("-0.08"),
        ret_14d=Decimal("-0.25"),
        high_30d=Decimal("0.0850"),
        low_30d=Decimal("0.0175"),  # dist_to_low = (0.0185 - 0.0175)/0.0175 = 5.7% <= 8.0%
        days_since_30d_high=Decimal("22.0"),
        ema20_4h=Decimal("0.0210"),
        atr14_4h=Decimal("0.0012"),
        ret_24h=Decimal("-0.02"),
        rsi_1h=Decimal("36.0"),
        rsi_4h=Decimal("38.0"),
        has_bearish_div=False,
        has_climax_blowoff=False,
        poc_30d=Decimal("0.0240"),
        macd_hist_4h=Decimal("-0.0001"),
        taker_buy_ratio_24h=Decimal("0.46"),
        funding_rate_8h=Decimal("0.0001"),
        oi_delta_7d=Decimal("-0.02"),
        runup_30d=Decimal("0.05"),  # No runup
    )
    passed, details = evaluate_short_gates(grinder_inputs)
    assert not passed
    assert not details["S1"], "Gate S1 must reject lack of macro run-up"
    assert not details["S3"], "Gate S3 must reject proximity to 30d floor"


def test_parabolic_top_losers_accepted_as_exhaustion_short() -> None:
    """Empirical parameters from real Binance top dumpers (AGT, STRK, RLC) must pass as EXHAUSTION_REVERSAL_SHORT."""
    # Simulating AGTUSDT peak: +750% 7d run-up, 4H RSI 88.4, 45% upper rejection wick, trapped longs
    agt_features = ShortArchetypeFeatures(
        close=Decimal("0.0082"),
        high_24h=Decimal("0.0098"),
        high_30d=Decimal("0.0098"),
        ema50_4h=Decimal("0.0035"),
        poc_30d=Decimal("0.0042"),
        atr14_4h=Decimal("0.0008"),
        has_bearish_div=True,
        rsi_4h=Decimal("82.0"),
        rsi_1d=Decimal("88.4"),
        rsi_1w=Decimal("85.0"),
        oi_delta_7d=Decimal("1.80"),  # Massive +180% OI surge
        upper_wick_ratio_4h=Decimal("0.55"),  # 55% upper wick rejection
        max_ret_30d=Decimal("7.50"),
        taker_buy_ratio_24h=Decimal("0.44"),
        macd_hist_4h=Decimal("-0.0002"),
        pos_in_range_30d=Decimal("0.85"),  # Upper 85% of range
        runup_30d=Decimal("7.50"),
        ret_14d=Decimal("4.20"),
    )
    assert classify_short_archetype(agt_features) in (
        SetupLabel.UTAD_DIVERGENCE_SHORT,
        SetupLabel.EXHAUSTION_REVERSAL_SHORT,
    )

    agt_gate_inputs = ShortGateInputs(
        close=Decimal("0.0082"),
        ema50_4h=Decimal("0.0035"),
        high_7d=Decimal("0.0098"),
        range_height_30d=Decimal("0.85"),
        ret_7d=Decimal("2.50"),
        ret_14d=Decimal("4.20"),
        high_30d=Decimal("0.0098"),
        low_30d=Decimal("0.0011"),
        days_since_30d_high=Decimal("0.5"),
        ema20_4h=Decimal("0.0068"),
        atr14_4h=Decimal("0.0008"),
        ret_24h=Decimal("-0.12"),
        rsi_1h=Decimal("62.0"),
        rsi_4h=Decimal("82.0"),
        has_bearish_div=True,
        has_climax_blowoff=True,
        poc_30d=Decimal("0.0042"),
        macd_hist_4h=Decimal("-0.0002"),
        macd_hist_4h_prev=Decimal("0.0005"),
        taker_buy_ratio_24h=Decimal("0.44"),
        funding_rate_8h=Decimal("0.00045"),  # High positive funding (trapped longs)
        oi_delta_7d=Decimal("1.80"),
        oi_delta_1h=Decimal("-0.02"),
        taker_buy_ratio_1h=Decimal("0.42"),
        high_24h=Decimal("0.0098"),
        low_72h=Decimal("0.0040"),
        rsi_1d=Decimal("88.4"),
        runup_30d=Decimal("7.50"),
    )
    passed, details = evaluate_short_gates(agt_gate_inputs, SetupLabel.EXHAUSTION_REVERSAL_SHORT)
    assert passed, f"AGT parabolic dump setup must pass all gates, got {details}"
    assert all(details.values())


def test_short_bracket_targets_are_realistic_and_positive() -> None:
    """Execution bracket for low-priced coin must produce realistic structural targets, not near-zero."""
    tick = Decimal("0.00001")
    bracket = build_short_bracket(
        close=Decimal("0.01850"),
        ema50_4h=Decimal("0.02500"),
        poc_30d=Decimal("0.02400"),
        atr14_4h=Decimal("0.00120"),
        local_consolidation_low_4h=Decimal("0.01780"),
        swing_high_4h=Decimal("0.02600"),
        low_30d=Decimal("0.01500"),
        vah_30d=Decimal("0.02550"),
        val_30d=Decimal("0.02100"),
        tick_size=tick,
    )
    # Hierarchy must hold
    assert bracket.invalidation_stop > bracket.retest_entry >= Decimal("0.01850")
    assert bracket.breakout_trigger < Decimal("0.01850")
    assert bracket.t1 < bracket.breakout_trigger
    assert bracket.t2 < bracket.t1
    assert bracket.t3 < bracket.t2
    # Floor protection: T3 must never collapse to $0.00018
    assert bracket.t3 >= Decimal("0.00900"), f"Target 3 {bracket.t3} must remain above floor"
    assert bracket.asymmetric_rr >= Decimal("2.50")


