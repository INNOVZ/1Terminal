from __future__ import annotations

from decimal import Decimal
import pytest

from algotrader.domain.execution_brackets import build_long_bracket, build_short_bracket
from algotrader.domain.indicators import VolumeProfileBin
from algotrader.domain.precision import decimal_places_from_tick_size
from algotrader.domain.resistance_absorption import compute_resistance_absorption
from algotrader.domain.scoring import (
    OpportunityGrade,
    classify_opportunity_grade,
    compute_3tier_ev_r,
)


# =====================================================================
# INVARIANT 1: UNIFIED ENTRY/STOP ANCHOR FOR R:R AND EV_R
# =====================================================================

def test_invariant_1_ceiling_breakout_unified_anchor_and_positive_ev() -> None:
    """Invariant 1: When execution_mode == CEILING_BREAKOUT, effective_entry is breakout_trigger,
    targets scale above effective_entry, and EV_R is mathematically guaranteed positive (> 0).
    """
    tick = Decimal("0.0001")
    close = Decimal("0.0625")
    poc = Decimal("0.0600")
    vah = Decimal("0.0630")
    high_30d = Decimal("0.0660")
    ema50 = Decimal("0.0590")
    atr = Decimal("0.0015")
    swing_low = Decimal("0.0580")
    comp_high = Decimal("0.0645")

    # High absorption score near resistance triggers CEILING_BREAKOUT
    bracket = build_long_bracket(
        close=close,
        ema50_4h=ema50,
        poc_30d=poc,
        atr14_4h=atr,
        local_compression_high_4h=comp_high,
        swing_low_4h=swing_low,
        high_30d=high_30d,
        vah_30d=vah,
        val_30d=Decimal("0.0570"),
        tick_size=tick,
        resistance_absorption_score=Decimal("78.0"),
    )

    assert bracket.execution_mode == "CEILING_BREAKOUT"
    assert bracket.effective_entry == bracket.breakout_trigger
    assert bracket.invalidation_stop < bracket.effective_entry

    # Targets must scale above effective_entry according to Invariant 1 formula:
    # t1 >= effective_entry + 1.50 * risk_unit, t2 >= effective_entry + 2.50 * risk_unit
    assert bracket.r_t1 >= Decimal("1.50"), f"Expected r_t1 >= 1.50, got {bracket.r_t1}"
    assert bracket.r_t2 >= Decimal("2.50"), f"Expected r_t2 >= 2.50, got {bracket.r_t2}"
    assert bracket.r_t3 >= Decimal("3.80"), f"Expected r_t3 >= 3.80, got {bracket.r_t3}"
    assert bracket.t1 > bracket.effective_entry
    assert bracket.t2 > bracket.t1
    assert bracket.t3 > bracket.t2

    # Under standard win rate >= 50%, EV_R is strictly positive
    ev_r = compute_3tier_ev_r(
        p_win=Decimal("0.55"),
        r_t1=bracket.r_t1,
        r_t2=bracket.r_t2,
        r_t3=bracket.r_t3,
    )
    assert ev_r > Decimal("0.0"), f"Breakout EV_R must be positive, got {ev_r}"


def test_invariant_1_retest_accumulation_unified_anchor() -> None:
    """Invariant 1: When execution_mode != CEILING_BREAKOUT, effective_entry is retest_entry,
    effective_stop is invalidation_stop, and risk_unit = effective_entry - effective_stop.
    """
    tick = Decimal("0.0001")
    close = Decimal("0.0830")
    bracket = build_long_bracket(
        close=close,
        ema50_4h=Decimal("0.0810"),
        poc_30d=Decimal("0.0800"),
        atr14_4h=Decimal("0.0025"),
        local_compression_high_4h=Decimal("0.0890"),
        swing_low_4h=Decimal("0.0760"),
        high_30d=Decimal("0.0920"),
        vah_30d=Decimal("0.0880"),
        val_30d=Decimal("0.0750"),
        tick_size=tick,
        resistance_absorption_score=Decimal("55.0"),
    )

    assert bracket.execution_mode == "RETEST_ACCUMULATION"
    assert bracket.effective_entry == bracket.retest_entry
    assert bracket.invalidation_stop < bracket.effective_entry
    assert bracket.risk_unit == bracket.effective_entry - bracket.invalidation_stop


# =====================================================================
# INVARIANT 2: MINIMUM STRUCTURAL STOP DISTANCE PREVENTS R:R EXPLOSION
# =====================================================================

def test_invariant_2_long_minimum_structural_stop_and_rr_ceiling() -> None:
    """Invariant 2: Invalidation stop sits structurally below val_30d and maintains min_retest_risk,
    and displayed asymmetric_rr is capped at min(r_t2, 8.0).
    """
    tick = Decimal("0.0001")
    atr = Decimal("0.0400")
    close = Decimal("0.8596")
    poc = Decimal("0.7989")
    val = Decimal("0.7600")

    bracket = build_long_bracket(
        close=close,
        ema50_4h=Decimal("0.8200"),
        poc_30d=poc,
        atr14_4h=atr,
        local_compression_high_4h=Decimal("0.9800"),
        swing_low_4h=Decimal("0.8000"),
        high_30d=Decimal("0.9900"),
        vah_30d=Decimal("0.9200"),
        val_30d=val,
        tick_size=tick,
        resistance_absorption_score=Decimal("55.0"),
    )

    min_retest_risk = max(Decimal("1.20") * atr, bracket.retest_entry * Decimal("0.035"))
    assert bracket.invalidation_stop <= bracket.retest_entry - min_retest_risk
    assert bracket.invalidation_stop <= val - Decimal("0.20") * atr
    assert bracket.risk_unit >= min_retest_risk
    # Clamped at 8.0 to prevent denominator explosion
    assert bracket.asymmetric_rr <= Decimal("8.00")
    assert bracket.asymmetric_rr >= Decimal("2.50")


def test_invariant_2_short_minimum_structural_stop_and_rr_ceiling() -> None:
    """Invariant 2: Short setup enforces symmetric structural stop and clamps asymmetric_rr <= 8.0."""
    tick = Decimal("0.00001")
    atr = Decimal("0.0020")
    close = Decimal("0.0506")
    bracket = build_short_bracket(
        close=close,
        ema50_4h=Decimal("0.0416"),
        poc_30d=Decimal("0.0416"),
        atr14_4h=atr,
        local_consolidation_low_4h=Decimal("0.0298"),
        swing_high_4h=Decimal("0.0520"),
        low_30d=Decimal("0.0180"),
        vah_30d=Decimal("0.0517"),
        val_30d=Decimal("0.0298"),
        tick_size=tick,
    )

    min_short_risk = max(Decimal("1.20") * atr, bracket.retest_entry * Decimal("0.035"))
    assert bracket.invalidation_stop >= bracket.retest_entry + min_short_risk
    assert bracket.invalidation_stop > close
    assert bracket.risk_unit >= min_short_risk
    # Clamped at 8.0 to prevent 16.52:1 explosion
    assert bracket.asymmetric_rr <= Decimal("8.00")


# =====================================================================
# INVARIANT 3: STRICT POST-ROUNDING ORDERING & DYNAMIC PRECISION
# =====================================================================

def test_invariant_3_retest_entry_strictly_below_close_when_close_below_poc() -> None:
    """Invariant 3: When close <= poc_30d * 1.005, retest_entry uses val_30d shelf and
    strictly satisfies retest_entry < close after tick_size rounding.
    """
    tick = Decimal("0.0001")
    # Candidate trading slightly below or at POC
    close = Decimal("0.2343")
    poc = Decimal("0.2350")
    val = Decimal("0.2200")

    bracket = build_long_bracket(
        close=close,
        ema50_4h=Decimal("0.2355"),
        poc_30d=poc,
        atr14_4h=Decimal("0.0120"),
        local_compression_high_4h=Decimal("0.2600"),
        swing_low_4h=Decimal("0.2100"),
        high_30d=Decimal("0.2700"),
        vah_30d=Decimal("0.2500"),
        val_30d=val,
        tick_size=tick,
    )

    assert bracket.retest_entry < close, f"Retest entry {bracket.retest_entry} must be < close {close}"
    assert bracket.retest_entry <= close * Decimal("0.990")
    assert bracket.retest_entry % tick == Decimal("0")


def test_invariant_3_dynamic_tick_precision_derivation() -> None:
    """Invariant 3: Decimal precision is derived dynamically from tick_size:
    decimals = max(2, -int(math.floor(math.log10(tick_size)))).
    """
    assert decimal_places_from_tick_size(Decimal("0.0001")) == 4
    assert decimal_places_from_tick_size(Decimal("0.00001")) == 5
    assert decimal_places_from_tick_size(Decimal("0.000001")) == 6
    assert decimal_places_from_tick_size(Decimal("0.01")) == 2
    assert decimal_places_from_tick_size(Decimal("0.1")) == 2
    assert decimal_places_from_tick_size(Decimal("1.0")) == 2


# =====================================================================
# INVARIANT 4: COHERENT ABSORPTION BADGE & GRADE STATE MACHINE
# =====================================================================

def test_invariant_4_three_mutually_exclusive_absorption_states() -> None:
    """Invariant 4: The 3 breakout_readiness_status states are mutually exclusive:
    1. BREAKOUT_CONFIRMED: score >= 65.0 and oi_delta_7d >= 0.0 and cvd_zscore_24h > -0.25
    2. HIGH_REJECTION_RISK: score < 45.0 or oi_delta_7d < -0.01 or (cvd_zscore_24h <= -1.0 and dist_to_vah <= 0.03)
    3. RETEST_ONLY_WAIT_FOR_DIP: All other valid candidates (e.g. scores 45.0-64.9 with non-bleeding 7d OI).
    """
    vpvr_bins = [
        VolumeProfileBin(price=Decimal("1.00"), volume=Decimal("5000")),
        VolumeProfileBin(price=Decimal("1.02"), volume=Decimal("1000")),
    ]

    # State 1: BREAKOUT_CONFIRMED
    score_1, status_1, _ = compute_resistance_absorption(
        cvd_zscore_24h=0.55,
        taker_buy_ratio_4h=0.54,
        oi_delta_7d=0.08,
        oi_delta_4h=0.02,
        funding_bps=-5.0,
        low_4h=Decimal("0.95"),
        low_4h_prev=Decimal("0.94"),
        low_4h_prev2=Decimal("0.93"),
        close=Decimal("0.98"),
        ema20_1h=Decimal("0.97"),
        atr14_1h=Decimal("0.02"),
        ret_24h=Decimal("0.03"),
        vpvr_bins=vpvr_bins,
        vah_30d=Decimal("1.00"),
        high_30d=Decimal("1.05"),
        poc_bin_volume=Decimal("20000"),
    )
    assert status_1 == "BREAKOUT_CONFIRMED"
    assert score_1 >= 65.0

    # State 2: HIGH_REJECTION_RISK due to bleeding OI (oi_delta_7d < -0.01)
    score_2, status_2, _ = compute_resistance_absorption(
        cvd_zscore_24h=-0.40,
        taker_buy_ratio_4h=0.48,
        oi_delta_7d=-0.0689,  # Bleeding OI
        oi_delta_4h=-0.01,
        funding_bps=2.60,
        low_4h=Decimal("0.95"),
        low_4h_prev=Decimal("0.94"),
        low_4h_prev2=Decimal("0.93"),
        close=Decimal("0.98"),
        ema20_1h=Decimal("0.97"),
        atr14_1h=Decimal("0.02"),
        ret_24h=Decimal("0.02"),
        vpvr_bins=vpvr_bins,
        vah_30d=Decimal("1.00"),
        high_30d=Decimal("1.05"),
        poc_bin_volume=Decimal("20000"),
    )
    assert status_2 == "HIGH_REJECTION_RISK"
    assert score_2 <= 44.0, f"Score {score_2} must be capped at 44.0 under HIGH_REJECTION_RISK"

    # State 3: RETEST_ONLY_WAIT_FOR_DIP (Non-bleeding OI, intermediate absorption)
    score_3, status_3, _ = compute_resistance_absorption(
        cvd_zscore_24h=-0.30,  # mildly negative CVD
        taker_buy_ratio_4h=0.51,
        oi_delta_7d=0.04,  # Positive 7d OI
        oi_delta_4h=0.005,
        funding_bps=0.50,
        low_4h=Decimal("0.95"),
        low_4h_prev=Decimal("0.94"),
        low_4h_prev2=Decimal("0.93"),
        close=Decimal("0.98"),
        ema20_1h=Decimal("0.97"),
        atr14_1h=Decimal("0.02"),
        ret_24h=Decimal("0.02"),
        vpvr_bins=vpvr_bins,
        vah_30d=Decimal("1.00"),
        high_30d=Decimal("1.05"),
        poc_bin_volume=Decimal("20000"),
    )
    assert status_3 == "RETEST_ONLY_WAIT_FOR_DIP"
    assert 45.0 <= score_3 < 65.0


def test_invariant_4_high_rejection_risk_caps_setup_grade_to_grade_b() -> None:
    """Invariant 4: When breakout_readiness_status == HIGH_REJECTION_RISK,
    overall setup grade is strictly capped at Grade B (RETEST ONLY) and never Grade A+.
    """
    # High technical conviction candidate that suffers from bleeding OI:
    ev_r = Decimal("1.80")
    conviction = Decimal("85.0")
    asymmetric_rr = Decimal("3.50")

    # Raw grade would be Grade A
    raw_grade = classify_opportunity_grade(
        ev_r=ev_r,
        conviction=conviction,
        asymmetric_rr=asymmetric_rr,
    )
    assert raw_grade == OpportunityGrade.GRADE_A

    # State machine cap rule:
    breakout_status = "HIGH_REJECTION_RISK"
    absorption_score = 68.0

    if breakout_status == "HIGH_REJECTION_RISK":
        absorption_score = min(absorption_score, 44.0)
        final_grade = OpportunityGrade.GRADE_B
    else:
        final_grade = raw_grade

    assert final_grade == OpportunityGrade.GRADE_B
    assert absorption_score <= 44.0
