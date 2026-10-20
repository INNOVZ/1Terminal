from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from algotrader.domain.precision import floor_to_increment, normalize_price

EPSILON = Decimal("1e-8")


@dataclass(frozen=True, slots=True)
class ExecutionBracket:
    retest_entry: Decimal
    breakout_trigger: Decimal
    invalidation_stop: Decimal
    effective_entry: Decimal
    suggested_entry: Decimal
    t1: Decimal
    t2: Decimal
    t3: Decimal
    targets: tuple[Decimal, Decimal, Decimal]
    risk_unit: Decimal
    r_t1: Decimal
    r_t2: Decimal
    r_t3: Decimal
    asymmetric_rr: Decimal
    execution_mode: str = "RETEST_ACCUMULATION"
    retest_stop: Decimal = Decimal("0")
    breakout_stop: Decimal = Decimal("0")


def build_long_bracket(
    *,
    close: Decimal,
    ema50_4h: Decimal,
    poc_30d: Decimal,
    atr14_4h: Decimal,
    local_compression_high_4h: Decimal,
    swing_low_4h: Decimal,
    high_30d: Decimal,
    vah_30d: Decimal,
    tick_size: Decimal,
    val_30d: Decimal | None = None,
    resistance_absorption_score: Decimal | None = None,
) -> ExecutionBracket:
    """Build 5-Level Execution Bracket for LONG candidate enforcing Universal Invariants 1, 2, and 3."""
    atr = max(atr14_4h, EPSILON)
    effective_val = val_30d if val_30d is not None else (poc_30d - atr * Decimal("1.5"))

    # Invariant 3: Strict Raw Retest Entry Calculation
    if close < poc_30d * Decimal("0.985"):
        raw_retest_entry = min(
            effective_val + Decimal("0.40") * (poc_30d - effective_val),
            close * Decimal("0.985"),
        )
    else:
        upper_shelf = max(ema50_4h, poc_30d)
        lower_shelf = min(ema50_4h, poc_30d)
        if upper_shelf + Decimal("0.10") * atr <= close * Decimal("0.995"):
            anchor_shelf = upper_shelf
        elif lower_shelf + Decimal("0.10") * atr <= close * Decimal("0.995"):
            anchor_shelf = lower_shelf
        else:
            anchor_shelf = poc_30d
        raw_retest_entry = min(anchor_shelf + Decimal("0.10") * atr, close * Decimal("0.990"))

    # Invariant 3: Post-rounding normalization using floor_to_increment with hard guard
    retest_entry = floor_to_increment(raw_retest_entry, tick_size)
    if retest_entry >= close:
        retest_entry = floor_to_increment(close * Decimal("0.988"), tick_size)
    if retest_entry >= close:
        retest_entry = floor_to_increment(close - tick_size, tick_size)

    # Dynamic Mode Selection & Target Shifting
    dist_to_vah = (vah_30d - close) / max(close, Decimal("1e-8"))
    room_vah_to_ceiling = (high_30d - vah_30d) / max(atr, Decimal("1e-8"))
    swing_range = max(high_30d - swing_low_4h, atr * Decimal("3.0"))

    if (close <= vah_30d * Decimal("1.005")) and (room_vah_to_ceiling >= Decimal("1.5")):
        # Case A — VAH Breakout Into 30d Ceiling
        raw_breakout_trigger = vah_30d + Decimal("0.08") * atr
        raw_t1 = high_30d
        raw_t2 = swing_low_4h + Decimal("1.272") * swing_range
        raw_t3 = swing_low_4h + Decimal("1.618") * swing_range
    else:
        # Case B — 30d Ceiling Breakout
        raw_breakout_trigger = max(local_compression_high_4h, vah_30d) + Decimal("0.05") * atr
        if (high_30d - raw_breakout_trigger) < Decimal("1.5") * atr:
            raw_t1 = max(high_30d + Decimal("1.5") * atr, swing_low_4h + Decimal("1.272") * swing_range)
            raw_t2 = swing_low_4h + Decimal("1.618") * swing_range
            raw_t3 = swing_low_4h + Decimal("2.618") * swing_range
        else:
            raw_t1 = max(high_30d, vah_30d)
            raw_t2 = swing_low_4h + Decimal("1.618") * swing_range
            raw_t3 = swing_low_4h + Decimal("2.618") * swing_range

    breakout_trigger = normalize_price(raw_breakout_trigger, tick_size)
    if breakout_trigger <= close:
        breakout_trigger = normalize_price(close + max(tick_size, atr * Decimal("0.05")), tick_size)

    # Ensure Target 1 always provides >= 1.5 * atr expansion room above breakout trigger
    min_t1 = breakout_trigger + Decimal("1.5") * atr
    raw_t1 = max(raw_t1, min_t1)
    t1 = normalize_price(raw_t1, tick_size)
    if (t1 - breakout_trigger) < Decimal("1.5") * atr:
        t1 = normalize_price(breakout_trigger + Decimal("1.5") * atr + tick_size, tick_size)

    if raw_t2 <= t1:
        raw_t2 = t1 + max(tick_size, atr * Decimal("1.0"))
    t2 = normalize_price(raw_t2, tick_size)

    if raw_t3 <= t2:
        raw_t3 = t2 + max(tick_size, atr * Decimal("1.0"))
    t3 = normalize_price(raw_t3, tick_size)

    # Execution Mode Determination
    abs_score = resistance_absorption_score if resistance_absorption_score is not None else Decimal("50.0")

    if abs_score >= Decimal("65.0") and dist_to_vah <= Decimal("0.05"):
        execution_mode = "CEILING_BREAKOUT"
    elif abs_score < Decimal("50.0"):
        execution_mode = "WAIT_FOR_RETEST_ONLY"
    else:
        execution_mode = "RETEST_ACCUMULATION"

    # Invariant 2: Minimum Structural Stop Distance for Retest Entry
    min_retest_risk = max(Decimal("1.20") * atr, retest_entry * Decimal("0.035"))
    raw_stop = min(
        swing_low_4h - Decimal("0.80") * atr,
        effective_val - Decimal("0.60") * atr,
        retest_entry - min_retest_risk,
    )

    retest_stop = floor_to_increment(raw_stop, tick_size)
    if retest_stop >= retest_entry:
        retest_stop = floor_to_increment(retest_entry - min_retest_risk, tick_size)
    if retest_stop >= retest_entry:
        retest_stop = floor_to_increment(retest_entry - tick_size, tick_size)

    # Invariant 1: Unified Entry/Stop Anchor for R:R and EV_R
    if execution_mode == "CEILING_BREAKOUT":
        effective_entry = breakout_trigger
        min_breakout_risk = max(Decimal("1.0") * atr, effective_entry * Decimal("0.025"))
        max_breakout_risk = max(Decimal("3.0") * atr, effective_entry * Decimal("0.080"))

        # Minervini Pivot Handle Stop: anchor to local consolidation handle swing low
        handle_stop = swing_low_4h - Decimal("0.20") * atr
        raw_breakout_stop = max(
            min(handle_stop, effective_entry - min_breakout_risk),
            effective_entry - max_breakout_risk,
        )
        breakout_stop = floor_to_increment(raw_breakout_stop, tick_size)
        if breakout_stop >= effective_entry:
            breakout_stop = floor_to_increment(effective_entry - min_breakout_risk, tick_size)
        if breakout_stop >= effective_entry:
            breakout_stop = floor_to_increment(effective_entry - tick_size, tick_size)

        effective_stop = breakout_stop
        risk_unit = max(effective_entry - effective_stop, min_breakout_risk)
        risk_unit = max(risk_unit, EPSILON)

        # Scale targets above effective_entry
        t1 = max(t1, normalize_price(effective_entry + Decimal("1.50") * risk_unit, tick_size))
        t2 = max(t2, normalize_price(effective_entry + Decimal("2.50") * risk_unit, tick_size))
        t3 = max(t3, normalize_price(effective_entry + Decimal("3.80") * risk_unit, tick_size))
        if t2 <= t1:
            t2 = normalize_price(t1 + max(tick_size, atr * Decimal("0.5")), tick_size)
        if t3 <= t2:
            t3 = normalize_price(t2 + max(tick_size, atr * Decimal("0.5")), tick_size)

        suggested_entry = min(close, retest_entry)
        invalidation_stop = retest_stop
        if invalidation_stop >= suggested_entry:
            invalidation_stop = floor_to_increment(suggested_entry - min_retest_risk, tick_size)
        if invalidation_stop >= suggested_entry:
            invalidation_stop = floor_to_increment(suggested_entry - tick_size, tick_size)
    else:
        effective_entry = retest_entry
        effective_stop = retest_stop
        invalidation_stop = retest_stop
        breakout_stop = floor_to_increment(breakout_trigger - max(Decimal("1.0") * atr, breakout_trigger * Decimal("0.025")), tick_size)
        risk_unit = max(effective_entry - effective_stop, min_retest_risk)
        risk_unit = max(risk_unit, EPSILON)

        # Scale targets above effective_entry so asymmetric upside is guaranteed
        t1 = max(t1, normalize_price(effective_entry + Decimal("1.50") * risk_unit, tick_size))
        t2 = max(t2, normalize_price(effective_entry + Decimal("2.50") * risk_unit, tick_size))
        t3 = max(t3, normalize_price(effective_entry + Decimal("3.80") * risk_unit, tick_size))
        if t2 <= t1:
            t2 = normalize_price(t1 + max(tick_size, atr * Decimal("0.5")), tick_size)
        if t3 <= t2:
            t3 = normalize_price(t2 + max(tick_size, atr * Decimal("0.5")), tick_size)

        suggested_entry = min(close, retest_entry)
        if invalidation_stop >= suggested_entry:
            invalidation_stop = floor_to_increment(suggested_entry - min_retest_risk, tick_size)
        if invalidation_stop >= suggested_entry:
            invalidation_stop = floor_to_increment(suggested_entry - tick_size, tick_size)

    # Compute r_t1, r_t2, r_t3, asymmetric_rr strictly from unified effective_entry and risk_unit
    r_t1 = (abs(t1 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t2 = (abs(t2 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t3 = (abs(t3 - effective_entry) / risk_unit).quantize(Decimal("0.01"))

    # Invariant 2: Clamp displayed asymmetric_rr = min(r_t2, 8.0)
    asymmetric_rr = min(r_t2, Decimal("8.00")).quantize(Decimal("0.01"))

    return ExecutionBracket(
        retest_entry=retest_entry,
        breakout_trigger=breakout_trigger,
        invalidation_stop=invalidation_stop,
        effective_entry=effective_entry,
        suggested_entry=suggested_entry,
        t1=t1,
        t2=t2,
        t3=t3,
        targets=(t1, t2, t3),
        risk_unit=risk_unit,
        r_t1=r_t1,
        r_t2=r_t2,
        r_t3=r_t3,
        asymmetric_rr=asymmetric_rr,
        execution_mode=execution_mode,
        retest_stop=retest_stop,
        breakout_stop=breakout_stop,
    )


def build_short_bracket(
    *,
    close: Decimal,
    ema50_4h: Decimal,
    poc_30d: Decimal,
    atr14_4h: Decimal,
    local_consolidation_low_4h: Decimal,
    swing_high_4h: Decimal,
    low_30d: Decimal,
    vah_30d: Decimal,
    val_30d: Decimal,
    tick_size: Decimal,
) -> ExecutionBracket:
    """Build 5-Level Execution Bracket for SHORT candidate according to Section 6.2 with Invariant 2."""
    atr = max(atr14_4h, EPSILON)
    primary_ceiling = max(ema50_4h, poc_30d)

    raw_retest_entry = max(primary_ceiling - Decimal("0.15") * atr, close)
    raw_breakdown_trigger = local_consolidation_low_4h - Decimal("0.05") * atr
    if raw_breakdown_trigger >= close:
        raw_breakdown_trigger = close - Decimal("0.10") * atr

    # Invariant 2: Symmetric structural stop distance for SHORT setup
    min_short_risk = max(Decimal("1.20") * atr, raw_retest_entry * Decimal("0.035"))
    raw_invalidation_stop = max(
        swing_high_4h + Decimal("0.80") * atr,
        vah_30d + Decimal("0.60") * atr,
        raw_retest_entry + min_short_risk,
    )
    if raw_invalidation_stop <= raw_retest_entry:
        raw_invalidation_stop = raw_retest_entry + min_short_risk

    raw_effective_entry = (
        raw_retest_entry if (close >= raw_breakdown_trigger) else raw_breakdown_trigger
    )
    risk_unit = max(raw_invalidation_stop - raw_effective_entry, min_short_risk)
    risk_unit = max(risk_unit, EPSILON)

    # Realistic structural short targets
    # T1: Structural shelf (POC/VAL) or initial expansion below breakdown trigger
    raw_t1 = min(poc_30d, val_30d) if close > poc_30d else low_30d
    max_t1 = raw_breakdown_trigger - Decimal("1.20") * atr
    raw_t1 = min(raw_t1, max_t1)

    # T2: Asymmetric R:R target (2.60R) or Lower Structural Shelf
    t2_candidate = raw_effective_entry - Decimal("2.60") * risk_unit
    raw_t2 = min(t2_candidate, raw_t1 - max(tick_size, atr * Decimal("0.80")))

    # T3: Macro Runner (3.80R) or Extended Breakdown
    t3_candidate = raw_effective_entry - Decimal("3.80") * risk_unit
    raw_t3 = min(t3_candidate, raw_t2 - max(tick_size, atr * Decimal("0.80")))

    # Floor protections: strictly prevent sub-penny / near-zero projections
    floor_t = max(low_30d * Decimal("0.60"), close * Decimal("0.20"), tick_size * Decimal("10.0"))
    raw_t1 = max(raw_t1, floor_t + tick_size * Decimal("3.0"))
    raw_t2 = max(raw_t2, floor_t + tick_size * Decimal("2.0"))
    raw_t3 = max(raw_t3, floor_t + tick_size)

    # Normalize with tick_size
    retest_entry = normalize_price(raw_retest_entry, tick_size)
    breakout_trigger = normalize_price(raw_breakdown_trigger, tick_size)
    invalidation_stop = normalize_price(raw_invalidation_stop, tick_size)
    effective_entry = normalize_price(raw_effective_entry, tick_size)
    t1 = normalize_price(raw_t1, tick_size)
    t2 = normalize_price(raw_t2, tick_size)
    t3 = normalize_price(raw_t3, tick_size)

    # Strictly ensure hierarchy holds after rounding
    if retest_entry < close:
        retest_entry = normalize_price(close + tick_size, tick_size)
    if invalidation_stop <= retest_entry:
        invalidation_stop = normalize_price(retest_entry + min_short_risk, tick_size)
    if invalidation_stop <= retest_entry:
        invalidation_stop = normalize_price(retest_entry + tick_size, tick_size)
    if breakout_trigger >= close:
        breakout_trigger = normalize_price(close - tick_size, tick_size)
    if t1 >= breakout_trigger:
        t1 = normalize_price(breakout_trigger - tick_size, tick_size)
    if t2 >= t1:
        t2 = normalize_price(t1 - tick_size, tick_size)
    if t3 >= t2:
        t3 = normalize_price(t2 - tick_size, tick_size)

    is_at_resistance_shelf = close >= (primary_ceiling - (atr * Decimal("0.35")))
    raw_suggested = close if is_at_resistance_shelf else max(close, retest_entry)
    suggested_entry = normalize_price(raw_suggested, tick_size)
    if suggested_entry >= invalidation_stop:
        suggested_entry = normalize_price(invalidation_stop - tick_size, tick_size)
    if suggested_entry < close:
        suggested_entry = normalize_price(close + tick_size, tick_size)
    if invalidation_stop <= suggested_entry:
        invalidation_stop = normalize_price(suggested_entry + min_short_risk, tick_size)
    if invalidation_stop <= suggested_entry:
        invalidation_stop = normalize_price(suggested_entry + tick_size, tick_size)

    risk_unit = max(invalidation_stop - effective_entry, min_short_risk)
    risk_unit = max(risk_unit, EPSILON)

    r_t1 = (abs(t1 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t2 = (abs(t2 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t3 = (abs(t3 - effective_entry) / risk_unit).quantize(Decimal("0.01"))

    # Invariant 2: Clamp displayed asymmetric_rr = min(r_t2, 8.0)
    asymmetric_rr = min(r_t2, Decimal("8.00")).quantize(Decimal("0.01"))

    return ExecutionBracket(
        retest_entry=retest_entry,
        breakout_trigger=breakout_trigger,
        invalidation_stop=invalidation_stop,
        effective_entry=effective_entry,
        suggested_entry=suggested_entry,
        t1=t1,
        t2=t2,
        t3=t3,
        targets=(t1, t2, t3),
        risk_unit=risk_unit,
        r_t1=r_t1,
        r_t2=r_t2,
        r_t3=r_t3,
        asymmetric_rr=asymmetric_rr,
        execution_mode="WAIT_FOR_RETEST_ONLY" if close < primary_ceiling else "RETEST_ACCUMULATION",
        retest_stop=invalidation_stop,
        breakout_stop=normalize_price(breakout_trigger + max(Decimal("1.0") * atr, breakout_trigger * Decimal("0.025")), tick_size),
    )
