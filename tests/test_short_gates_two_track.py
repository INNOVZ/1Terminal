from __future__ import annotations

from decimal import Decimal
import pytest

from algotrader.domain.crypto_screener_models import SetupLabel
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
