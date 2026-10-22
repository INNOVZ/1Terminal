from __future__ import annotations

from decimal import Decimal
import pytest

from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.models import Instrument, InstrumentId, MarketKind, Venue, Quote
from algotrader.domain.scoring import calculate_continuous_long_factor_scores
from algotrader.domain.screening_gates import (
    LongGateInputs,
    evaluate_long_gates,
    compute_coil_maturity_score,
    is_volume_vacuum_setup,
    is_mature_squeeze_setup,
)


def _base_valid_gate_inputs(**kwargs) -> LongGateInputs:
    defaults = {
        "close": Decimal("100.00"),
        "ema50_4h": Decimal("96.00"),
        "ema20_1h": Decimal("99.00"),
        "ema200_4h": Decimal("90.00"),
        "low_24h": Decimal("97.50"),
        "atr14_4h": Decimal("2.00"),
        "vcr_4h": Decimal("0.85"),
        "ema20_4h": Decimal("98.50"),
        "excursion_72h": Decimal("0.08"),
        "ret_24h": Decimal("0.01"),
        "rsi_1h": Decimal("52.00"),
        "rsi_4h": Decimal("50.00"),
        "high_30d": Decimal("112.00"),
        "low_30d": Decimal("90.00"),
        "poc_30d": Decimal("98.00"),
        "val_30d": Decimal("94.00"),
        "vah_30d": Decimal("108.00"),
        "bb_inside_kc_4h": True,
        "bb_bandwidth_4h": Decimal("0.10"),
        "bb_inside_kc_4h_lookback": (True,) * 12,
        "rvol_4h": Decimal("0.45"),
        "oi_delta_7d": Decimal("0.02"),
        "oi_delta_4h": Decimal("0.01"),
        "oi_delta_1h": Decimal("0.002"),
        "taker_buy_ratio_4h": Decimal("1.02"),
        "has_kinetic_ignition": False,
        "pocket_pivot_1h": False,
        "micro_expansion_1h": False,
        "of_absorption_1h": False,
    }
    defaults.update(kwargs)
    return LongGateInputs(**defaults)


def test_coil_maturity_score_grading() -> None:
    """Test Coil Maturity Score across perfect, mature, and immature compression setups."""
    # 1. Textbook maximum maturity coil
    perfect_inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.07"),  # 30 pts (<= 0.08)
        vcr_4h=Decimal("0.60"),            # 25 pts (<= 0.65)
        bb_inside_kc_4h_lookback=(True,) * 10,  # 20 pts (>= 8 bars)
        close=Decimal("100.0"),
        ema20_4h=Decimal("99.0"),
        poc_30d=Decimal("98.0"),           # 15 pts (holding ema20 & poc)
        rsi_4h=Decimal("48.0"),            # 10 pts (40-60)
    )
    score_perfect = compute_coil_maturity_score(perfect_inputs)
    assert score_perfect == 100

    # 2. Mature coil passing 65 threshold
    mature_inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.11"),   # 25 pts (<= 0.12)
        vcr_4h=Decimal("0.80"),             # 15 pts (<= 0.85)
        bb_inside_kc_4h_lookback=(True,) * 6,   # 12 pts (>= 4 bars)
        close=Decimal("100.0"),
        ema20_4h=Decimal("99.0"),
        poc_30d=Decimal("98.0"),            # 15 pts
        rsi_4h=Decimal("55.0"),             # 10 pts
    )
    score_mature = compute_coil_maturity_score(mature_inputs)
    assert score_mature == 77
    assert score_mature >= 65

    # 3. Immature / loose coil failing 65 threshold
    immature_inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.19"),   # 0 pts (> 0.18)
        vcr_4h=Decimal("0.95"),             # 0 pts (> 0.85)
        bb_inside_kc_4h_lookback=(False, False),  # 0 pts
        bb_inside_kc_4h=False,
        close=Decimal("100.0"),
        ema20_4h=Decimal("99.0"),
        poc_30d=Decimal("98.0"),            # 15 pts
        rsi_4h=Decimal("68.0"),             # 0 pts (> 60)
    )
    score_immature = compute_coil_maturity_score(immature_inputs)
    assert score_immature == 15
    assert score_immature < 65


def test_gate_l6_stealth_coil_passes_without_active_ignition() -> None:
    """Mature compression coil with dead volume (RVOL 0.45) passes Gate L6 via Coil Maturity bypass."""
    inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.09"),  # Extreme coil
        vcr_4h=Decimal("0.62"),
        rvol_4h=Decimal("0.45"),          # Dead volume (< 1.20)
        has_kinetic_ignition=False,
        pocket_pivot_1h=False,
        micro_expansion_1h=False,
        of_absorption_1h=False,
        oi_delta_7d=Decimal("0.03"),      # Healthy non-bleeding OI
        oi_delta_4h=Decimal("0.01"),
    )
    passed, gate_details = evaluate_long_gates(inputs, SetupLabel.COIL_SPRING_LONG)
    assert gate_details["L6"] is True
    assert passed is True


def test_gate_l6_stealth_coil_rejects_bleeding_oi() -> None:
    """Even a mature coil must be rejected if 7d OI is bleeding (distributive bleed)."""
    inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.07"),
        vcr_4h=Decimal("0.60"),
        rvol_4h=Decimal("0.45"),
        oi_delta_7d=Decimal("-0.05"),  # Bleeding OI (< -2.5%)
        oi_delta_4h=Decimal("-0.01"),
    )
    passed, gate_details = evaluate_long_gates(inputs, SetupLabel.COIL_SPRING_LONG)
    assert gate_details["L6"] is False
    assert passed is False


def test_gate_l6_stealth_coil_rejects_acute_liquidation() -> None:
    """Acute liquidation spike (1h OI < -2.5%) vetos Gate L6 even on mature coil."""
    inputs = _base_valid_gate_inputs(
        bb_bandwidth_4h=Decimal("0.07"),
        vcr_4h=Decimal("0.60"),
        rvol_4h=Decimal("0.45"),
        oi_delta_7d=Decimal("0.03"),
        oi_delta_4h=Decimal("0.01"),
        oi_delta_1h=Decimal("-0.035"),  # Acute liquidation crash
    )
    passed, gate_details = evaluate_long_gates(inputs, SetupLabel.COIL_SPRING_LONG)
    assert gate_details["L6"] is False
    assert passed is False


def test_volume_vacuum_detection_and_gate_l6_pass() -> None:
    """Volume Vacuum setup (supply exhaustion with flat price and holding EMA) passes L6."""
    vac_inputs = _base_valid_gate_inputs(
        rvol_4h=Decimal("0.42"),          # <= 0.55
        vcr_4h=Decimal("0.75"),           # <= 0.85
        ret_24h=Decimal("0.015"),         # abs <= 0.04
        bb_bandwidth_4h=Decimal("0.12"),  # <= 0.15
        close=Decimal("100.00"),
        ema20_4h=Decimal("99.50"),        # holding EMA20
        has_kinetic_ignition=False,
    )
    assert is_volume_vacuum_setup(vac_inputs) is True
    passed, gate_details = evaluate_long_gates(vac_inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert gate_details["L6"] is True
    assert passed is True


def test_mature_squeeze_duration_passes_gate_l6() -> None:
    """Extended squeeze duration (8+ 4H bars inside KC) passes L6 without active ignition."""
    sqz_inputs = _base_valid_gate_inputs(
        bb_inside_kc_4h=True,
        bb_inside_kc_4h_lookback=(True,) * 8,  # 8+ bars in squeeze
        bb_bandwidth_4h=Decimal("0.14"),       # <= 0.15
        close=Decimal("100.00"),
        ema20_4h=Decimal("99.00"),
        rvol_4h=Decimal("0.50"),
        has_kinetic_ignition=False,
    )
    assert is_mature_squeeze_setup(sqz_inputs) is True
    passed, gate_details = evaluate_long_gates(sqz_inputs, SetupLabel.COIL_SPRING_LONG)
    assert gate_details["L6"] is True
    assert passed is True


def test_immature_passive_coil_rejected_by_gate_l6() -> None:
    """Loose compression with no squeeze duration and dead volume is strictly rejected."""
    loose_inputs = _base_valid_gate_inputs(
        bb_inside_kc_4h=False,
        bb_inside_kc_4h_lookback=(False,),
        bb_bandwidth_4h=Decimal("0.19"),  # Loose (> 0.15)
        vcr_4h=Decimal("0.95"),           # No contraction (> 0.85)
        rvol_4h=Decimal("0.45"),          # Dead volume, no ignition
        close=Decimal("100.00"),
        ema20_4h=Decimal("99.00"),
        has_kinetic_ignition=False,
        pocket_pivot_1h=False,
        micro_expansion_1h=False,
        of_absorption_1h=False,
    )
    assert compute_coil_maturity_score(loose_inputs) < 65
    assert is_volume_vacuum_setup(loose_inputs) is False
    assert is_mature_squeeze_setup(loose_inputs) is False
    passed, gate_details = evaluate_long_gates(loose_inputs, SetupLabel.COIL_SPRING_LONG)
    assert gate_details["L6"] is False
    assert passed is False


def test_scoring_engine_volume_vacuum_reward() -> None:
    """Volume score in quant scoring awards +15 dryup points to confirmed volume vacuum coils."""
    # Candidate in tight coil with dry-up
    scores_vacuum = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.11"),
        bb_inside_kc=True,
        vol_contraction_ratio=Decimal("0.75"),
        close=Decimal("100.0"),
        poc_30d=Decimal("98.0"),
        val_30d=Decimal("95.0"),
        vah_30d=Decimal("105.0"),
        pos_in_range_30d=Decimal("0.50"),
        ema50_4h=Decimal("96.0"),
        ema200_4h=Decimal("90.0"),
        resistance_absorption_score=Decimal("60.0"),
        cvd_zscore=Decimal("0.5"),
        oi_change_7d=Decimal("0.04"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.05"),
        funding_rate=Decimal("0.00005"),
        ema20_4h=Decimal("99.0"),
        macd_hist_4h=Decimal("0.1"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("50.0"),
        btc_residual_alpha=Decimal("0.01"),
        rvol_4h=Decimal("0.50"),  # Low volume inside tight compression
        asymmetric_rr=Decimal("2.5"),
        dist_to_trigger=Decimal("0.05"),
        macro_multiplier=Decimal("1.0"),
    )
    # base 50 + dryup 25 + volume vacuum 15 = 90.0
    assert scores_vacuum.volume_score == Decimal("90.0")

    # Candidate with loose bandwidth (0.25) and low volume (0.50) suffers penalty (-15)
    scores_loose = calculate_continuous_long_factor_scores(
        bandwidth_4h=Decimal("0.25"),  # Not compressed
        bb_inside_kc=False,
        vol_contraction_ratio=Decimal("1.00"),
        close=Decimal("100.0"),
        poc_30d=Decimal("98.0"),
        val_30d=Decimal("95.0"),
        vah_30d=Decimal("105.0"),
        pos_in_range_30d=Decimal("0.50"),
        ema50_4h=Decimal("96.0"),
        ema200_4h=Decimal("90.0"),
        resistance_absorption_score=Decimal("60.0"),
        cvd_zscore=Decimal("0.5"),
        oi_change_7d=Decimal("0.04"),
        oi_change_4h=Decimal("0.02"),
        taker_buy_ratio_4h=Decimal("1.05"),
        funding_rate=Decimal("0.00005"),
        ema20_4h=Decimal("99.0"),
        macd_hist_4h=Decimal("0.1"),
        macd_hist_4h_prev=Decimal("0.05"),
        adx_subscore=Decimal("50.0"),
        btc_residual_alpha=Decimal("0.01"),
        rvol_4h=Decimal("0.50"),  # Low volume without compression = dead token
        asymmetric_rr=Decimal("2.5"),
        dist_to_trigger=Decimal("0.05"),
        macro_multiplier=Decimal("1.0"),
    )
    # base 50 + dryup 5 - rvol penalty 15 = 40.0
    assert scores_loose.volume_score == Decimal("40.0")


def test_gate_l2_htf_overbought_eliminates_weekly_and_daily_rsi_above_70() -> None:
    """Gate L2 strictly eliminates candidates with weekly or daily RSI >= 70.0 (e.g. BEAMXUSDT)."""
    # 1. Weekly RSI >= 70.0 (like BEAMXUSDT with rsi_1w = 73.6) must fail Gate L2
    beamx_like_inputs = _base_valid_gate_inputs(
        rsi_1w=Decimal("73.6"),
        rsi_1d=Decimal("65.0"),
        rsi_4h=Decimal("58.0"),
    )
    passed, gates = evaluate_long_gates(beamx_like_inputs, SetupLabel.BREAKOUT_LONG)
    assert not passed
    assert not gates["L2"]

    # 2. Daily RSI >= 70.0 must fail Gate L2
    daily_overbought_inputs = _base_valid_gate_inputs(
        rsi_1w=Decimal("55.0"),
        rsi_1d=Decimal("72.5"),
        rsi_4h=Decimal("58.0"),
    )
    passed_daily, gates_daily = evaluate_long_gates(daily_overbought_inputs, SetupLabel.BREAKOUT_LONG)
    assert not passed_daily
    assert not gates_daily["L2"]

    # 3. Non-overbought HTF RSI passes Gate L2
    healthy_inputs = _base_valid_gate_inputs(
        rsi_1w=Decimal("62.0"),
        rsi_1d=Decimal("59.0"),
        rsi_4h=Decimal("52.0"),
    )
    passed_healthy, gates_healthy = evaluate_long_gates(healthy_inputs, SetupLabel.BREAKOUT_LONG)
    assert gates_healthy["L2"]

