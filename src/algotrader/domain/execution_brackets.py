from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from algotrader.domain.models import Candle, Direction
from algotrader.domain.plan_diagnostics import BracketContext
from algotrader.domain.precision import ceil_to_increment, floor_to_increment, normalize_price

EPSILON = Decimal("1e-8")


def compute_structural_shelf_high_72h(
    closed_1h: Sequence[Candle],
    atr_1h: Decimal,
) -> Decimal:
    """Compute wick-resistant 72h structural resistance shelf from closed 1H bars.

    Caps single-bar stop-hunt upper wicks at `max(P96_high, max_body + 0.35 * atr_1h)` so
    one outlier spike does not push breakout triggers 5-10% above the true consolidation box.
    """
    window = closed_1h[-72:] if len(closed_1h) >= 72 else closed_1h
    if not window:
        return Decimal("0")
    highs = sorted(c.high for c in window)
    max_body = max(max(c.open, c.close) for c in window)
    p96_high = highs[min(len(highs) - 1, int(len(highs) * 0.96))]
    atr_cap = max_body + Decimal("0.35") * max(atr_1h, EPSILON)
    return min(highs[-1], max(p96_high, atr_cap))


def extract_overhead_shelves_4h(
    closed_4h: Sequence[Candle],
    reference_price: Decimal,
) -> tuple[Decimal, ...]:
    """Extract confirmed 5-bar fractal swing highs above `reference_price` from the last 30d of 4H bars."""
    window = closed_4h[-180:] if len(closed_4h) >= 180 else closed_4h
    if len(window) < 5 or reference_price <= Decimal("0"):
        return ()
    pivots: set[Decimal] = set()
    for i in range(2, len(window) - 2):
        h = window[i].high
        if (
            h > reference_price
            and h >= window[i - 1].high
            and h >= window[i - 2].high
            and h >= window[i + 1].high
            and h >= window[i + 2].high
        ):
            pivots.add(h)
    return tuple(sorted(pivots))


def _build_spaced_long_targets(
    *,
    effective_entry: Decimal,
    breakout_trigger: Decimal,
    risk_unit: Decimal,
    atr: Decimal,
    poc_30d: Decimal,
    vah_30d: Decimal,
    high_30d: Decimal,
    overhead_shelves: Sequence[Decimal],
    tick_size: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Anchor T1/T2/T3 to real structural overhead shelves with guaranteed inter-tier spacing."""
    min_gap = max(tick_size * Decimal("5"), Decimal("0.70") * risk_unit, Decimal("1.25") * atr)
    cands = sorted(
        {
            lvl
            for lvl in (poc_30d, vah_30d, *overhead_shelves, high_30d)
            if lvl > breakout_trigger
        }
    )

    t1_lo = max(
        effective_entry + Decimal("0.90") * risk_unit,
        breakout_trigger + max(tick_size, Decimal("0.50") * atr),
    )
    t1_hi = max(t1_lo + tick_size, effective_entry + Decimal("1.80") * risk_unit)
    t1_hits = [c for c in cands if t1_lo <= c <= t1_hi]
    t1 = normalize_price(
        t1_hits[0] if t1_hits else max(t1_lo, effective_entry + Decimal("1.35") * risk_unit),
        tick_size,
    )
    if t1 <= effective_entry:
        t1 = normalize_price(effective_entry + max(tick_size, Decimal("1.00") * risk_unit), tick_size)

    t2_lo = max(t1 + min_gap, effective_entry + Decimal("2.05") * risk_unit)
    t2_hi = max(t2_lo + min_gap, effective_entry + Decimal("3.20") * risk_unit)
    t2_hits = [c for c in cands if t2_lo <= c <= t2_hi]
    t2 = normalize_price(
        t2_hits[0] if t2_hits else max(t2_lo, effective_entry + Decimal("2.40") * risk_unit),
        tick_size,
    )
    if t2 < t1 + min_gap:
        t2 = normalize_price(t1 + min_gap, tick_size)
    if t2 <= t1:
        t2 = normalize_price(t1 + max(tick_size, min_gap), tick_size)

    t3_lo = max(t2 + min_gap, effective_entry + Decimal("3.40") * risk_unit)
    t3_hi = max(t3_lo + min_gap, effective_entry + Decimal("5.00") * risk_unit)
    t3_hits = [c for c in cands if t3_lo <= c <= t3_hi]
    t3 = normalize_price(
        t3_hits[-1] if t3_hits else max(t3_lo, effective_entry + Decimal("3.80") * risk_unit),
        tick_size,
    )
    if t3 < t2 + min_gap:
        t3 = normalize_price(t2 + min_gap, tick_size)
    if t3 <= t2:
        t3 = normalize_price(t2 + max(tick_size, min_gap), tick_size)

    return t1, t2, t3


def _compute_structural_long_stop(
    *,
    anchor_entry: Decimal,
    handle_low: Decimal,
    atr: Decimal,
    buffer_atr: Decimal,
    min_atr: Decimal,
    min_pct: Decimal,
    max_atr: Decimal,
    max_pct: Decimal,
    tick_size: Decimal,
) -> tuple[Decimal, Decimal, str]:
    """Compute handle-anchored, volatility- and percentage-bounded long stop loss."""
    min_risk = max(min_atr * atr, anchor_entry * min_pct)
    max_risk = max(max_atr * atr, anchor_entry * max_pct)
    if max_risk < min_risk:
        max_risk = min_risk
    handle_stop = handle_low - buffer_atr * atr
    bounded_upper = min(handle_stop, anchor_entry - min_risk)
    floor_bound = anchor_entry - max_risk
    raw_stop = max(bounded_upper, floor_bound)
    stop = floor_to_increment(raw_stop, tick_size)
    if stop >= anchor_entry:
        stop = floor_to_increment(anchor_entry - min_risk, tick_size)
    if stop >= anchor_entry:
        stop = floor_to_increment(anchor_entry - tick_size, tick_size)
    anchor_label = (
        "distance_bound"
        if floor_bound >= bounded_upper
        else "handle_low_48h"
        if handle_stop <= anchor_entry - min_risk
        else "minimum_distance"
    )
    return stop, min_risk, anchor_label


class InvalidExecutionBracket(ValueError):
    """The structural plan cannot produce positive, ordered execution prices."""


def valid_execution_prices(
    direction: Direction, entry: Decimal, stop: Decimal, targets: Sequence[Decimal],
) -> bool:
    levels = (stop, entry, *targets) if direction is Direction.LONG else (*reversed(targets), entry, stop)
    return bool(targets) and all(p.is_finite() and p > 0 for p in levels) and all(
        left < right for left, right in zip(levels, levels[1:], strict=False)
    )


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
    diagnostic_context: BracketContext | None = None


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
    handle_low_4h: Decimal | None = None,
    use_confluence_trigger_v2: bool = False,
    overhead_shelves: Sequence[Decimal] = (),
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
        # Institutional Retest Offset: anchor_shelf + 0.15 * ATR (Page 2 Spec OCR typo audit: '8.15 * ATR' -> '0.15 * ATR')
        if upper_shelf + Decimal("0.15") * atr <= close * Decimal("0.995"):
            anchor_shelf = upper_shelf
        elif lower_shelf + Decimal("0.15") * atr <= close * Decimal("0.995"):
            anchor_shelf = lower_shelf
        else:
            anchor_shelf = poc_30d
        raw_retest_entry = min(anchor_shelf + Decimal("0.15") * atr, close * Decimal("0.990"))

    if use_confluence_trigger_v2:
        base_low = handle_low_4h if (handle_low_4h is not None and handle_low_4h > Decimal("0")) else swing_low_4h
        max_pullback_floor = max(
            close * Decimal("0.960"),
            close - Decimal("1.25") * atr,
            base_low + Decimal("0.25") * atr,
        )
        pullback_cap = close * Decimal("0.990")
        raw_retest_entry = min(max(raw_retest_entry, min(max_pullback_floor, pullback_cap)), pullback_cap)

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

    if close < poc_30d:
        # Case 0 — Below POC Accumulation (between VAL and POC): Immediate overhead resistance is POC, not VAH!
        if use_confluence_trigger_v2:
            raw_shelf = max(local_compression_high_4h, close)
            if poc_30d > raw_shelf and (poc_30d - raw_shelf) <= min(
                Decimal("1.5") * atr, close * Decimal("0.05")
            ):
                raw_shelf = poc_30d
            raw_breakout_trigger = raw_shelf + Decimal("0.05") * atr
        else:
            raw_breakout_trigger = max(local_compression_high_4h, poc_30d, close) + Decimal("0.05") * atr
        raw_t1 = poc_30d
        raw_t2 = vah_30d
        raw_t3 = max(high_30d, vah_30d + Decimal("1.5") * atr)
    elif (close <= vah_30d * Decimal("1.005")) and (room_vah_to_ceiling >= Decimal("1.5")):
        # Case A — VAH Breakout Into 30d Ceiling (respect immediate 72h compression high)
        if use_confluence_trigger_v2:
            raw_shelf = max(local_compression_high_4h, close)
            if vah_30d > raw_shelf and (vah_30d - raw_shelf) <= min(
                Decimal("1.5") * atr, close * Decimal("0.06")
            ):
                raw_shelf = vah_30d
            raw_breakout_trigger = raw_shelf + Decimal("0.05") * atr
        else:
            raw_breakout_trigger = max(vah_30d, local_compression_high_4h) + Decimal("0.08") * atr
        raw_t1 = high_30d
        raw_t2 = swing_low_4h + Decimal("1.272") * swing_range
        raw_t3 = swing_low_4h + Decimal("1.618") * swing_range
    else:
        # Case B — 30d Ceiling Breakout
        if use_confluence_trigger_v2:
            raw_shelf = max(local_compression_high_4h, close)
            if vah_30d > raw_shelf and (vah_30d - raw_shelf) <= min(
                Decimal("1.5") * atr, close * Decimal("0.06")
            ):
                raw_shelf = vah_30d
            raw_breakout_trigger = raw_shelf + Decimal("0.05") * atr
        else:
            raw_breakout_trigger = max(local_compression_high_4h, vah_30d) + Decimal("0.05") * atr
        if (high_30d - raw_breakout_trigger) < Decimal("1.5") * atr:
            raw_t1 = max(high_30d + Decimal("1.5") * atr, swing_low_4h + Decimal("1.272") * swing_range)
            raw_t2 = swing_low_4h + Decimal("1.618") * swing_range
            raw_t3 = swing_low_4h + Decimal("2.618") * swing_range
        else:
            raw_t1 = max(high_30d, vah_30d)
            raw_t2 = swing_low_4h + Decimal("1.618") * swing_range
            raw_t3 = swing_low_4h + Decimal("2.618") * swing_range

    breakout_trigger = ceil_to_increment(raw_breakout_trigger, tick_size)
    if breakout_trigger <= close:
        breakout_trigger = ceil_to_increment(close + tick_size, tick_size)

    # When close < poc_30d, Target 1 is strictly capped at poc_price unless poc is at/below trigger
    if close < poc_30d:
        raw_t1 = max(raw_t1, breakout_trigger + tick_size)
    else:
        # Ensure Target 1 always provides >= 1.5 * atr expansion room above breakout trigger
        min_t1 = breakout_trigger + Decimal("1.5") * atr
        raw_t1 = max(raw_t1, min_t1)
    t1 = normalize_price(raw_t1, tick_size)

    if raw_t2 <= t1:
        raw_t2 = t1 + max(tick_size, atr * Decimal("1.0"))
    t2 = normalize_price(raw_t2, tick_size)

    if raw_t3 <= t2:
        raw_t3 = t2 + max(tick_size, atr * Decimal("1.0"))
    t3 = normalize_price(raw_t3, tick_size)

    # Save the initial objective for descriptive diagnostics only.
    initial_t1_before_risk_sizing = t1

    # Execution Mode Determination
    abs_score = resistance_absorption_score if resistance_absorption_score is not None else Decimal("50.0")

    if abs_score >= Decimal("65.0") and dist_to_vah <= Decimal("0.05"):
        execution_mode = "CEILING_BREAKOUT"
    elif abs_score < Decimal("50.0"):
        execution_mode = "WAIT_FOR_RETEST_ONLY"
    else:
        execution_mode = "RETEST_ACCUMULATION"

    # Invariant 2: Minimum Structural Stop Distance & Clearance below VAL / Swing Low
    handle_anchor = handle_low_4h if (handle_low_4h is not None and handle_low_4h > Decimal("0")) else swing_low_4h
    if use_confluence_trigger_v2:
        retest_stop, min_retest_risk, retest_stop_anchor = _compute_structural_long_stop(
            anchor_entry=retest_entry,
            handle_low=handle_anchor,
            atr=atr,
            buffer_atr=Decimal("0.35"),
            min_atr=Decimal("1.25"),
            min_pct=Decimal("0.035"),
            max_atr=Decimal("2.75"),
            max_pct=Decimal("0.095"),
            tick_size=tick_size,
        )
        unified_breakout_stop, min_v2_breakout_risk, v2_breakout_anchor = _compute_structural_long_stop(
            anchor_entry=breakout_trigger,
            handle_low=handle_anchor,
            atr=atr,
            buffer_atr=Decimal("0.25"),
            min_atr=Decimal("1.25"),
            min_pct=Decimal("0.035"),
            max_atr=Decimal("2.75"),
            max_pct=Decimal("0.090"),
            tick_size=tick_size,
        )
    else:
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
        retest_stop_anchor = ""
        unified_breakout_stop = Decimal("0")
        min_v2_breakout_risk = Decimal("0")
        v2_breakout_anchor = ""

    # Invariant 1: Unified Entry/Stop Anchor for R:R and EV_R
    if execution_mode == "CEILING_BREAKOUT":
        effective_entry = breakout_trigger
        if use_confluence_trigger_v2:
            breakout_stop = unified_breakout_stop
            min_breakout_risk = min_v2_breakout_risk
            stop_anchor = f"breakout_{v2_breakout_anchor}"
        else:
            min_breakout_risk = max(Decimal("1.0") * atr, effective_entry * Decimal("0.025"))
            max_breakout_risk = max(Decimal("3.0") * atr, effective_entry * Decimal("0.080"))

            # Minervini Pivot Handle Stop: anchor to local consolidation handle swing low
            handle_stop = handle_anchor - Decimal("0.20") * atr
            raw_breakout_stop = max(
                min(handle_stop, effective_entry - min_breakout_risk),
                effective_entry - max_breakout_risk,
            )
            breakout_stop = floor_to_increment(raw_breakout_stop, tick_size)
            if breakout_stop >= effective_entry:
                breakout_stop = floor_to_increment(effective_entry - min_breakout_risk, tick_size)
            if breakout_stop >= effective_entry:
                breakout_stop = floor_to_increment(effective_entry - tick_size, tick_size)

            stop_anchor = (
                "breakout_distance_bound" if effective_entry - max_breakout_risk >= min(handle_stop, effective_entry - min_breakout_risk)
                else "breakout_handle" if handle_stop <= effective_entry - min_breakout_risk
                else "minimum_distance"
            )
        effective_stop = breakout_stop
        risk_unit = max(effective_entry - effective_stop, min_breakout_risk)
        risk_unit = max(risk_unit, EPSILON)

        if use_confluence_trigger_v2:
            t1, t2, t3 = _build_spaced_long_targets(
                effective_entry=effective_entry,
                breakout_trigger=breakout_trigger,
                risk_unit=risk_unit,
                atr=atr,
                poc_30d=poc_30d,
                vah_30d=vah_30d,
                high_30d=high_30d,
                overhead_shelves=overhead_shelves,
                tick_size=tick_size,
            )
        elif close < poc_30d:
            if (poc_30d - effective_entry) < Decimal("1.00") * risk_unit:
                t1 = max(
                    normalize_price(vah_30d, tick_size),
                    normalize_price(effective_entry + Decimal("1.50") * risk_unit, tick_size),
                )
                t2 = max(
                    normalize_price(high_30d, tick_size),
                    normalize_price(effective_entry + Decimal("2.50") * risk_unit, tick_size),
                )
            else:
                min_t1_below_poc = normalize_price(
                    effective_entry + max(tick_size, Decimal("0.50") * risk_unit), tick_size
                )
                t1 = max(normalize_price(poc_30d, tick_size), min_t1_below_poc)
                if local_compression_high_4h <= poc_30d and t1 <= breakout_trigger:
                    t1 = normalize_price(
                        max(
                            vah_30d,
                            breakout_trigger + max(tick_size, Decimal("0.50") * atr),
                        ),
                        tick_size,
                    )
                t2 = max(
                    normalize_price(vah_30d, tick_size),
                    normalize_price(effective_entry + Decimal("2.00") * risk_unit, tick_size),
                )
            if t2 <= t1:
                t2 = normalize_price(t1 + max(tick_size, atr * Decimal("0.5")), tick_size)
            t3 = normalize_price(
                max(high_30d, vah_30d + Decimal("1.5") * atr, effective_entry + Decimal("3.50") * risk_unit),
                tick_size,
            )
            if t3 <= t2:
                t3 = normalize_price(t2 + max(tick_size, atr * Decimal("0.5")), tick_size)
        else:
            # Scale targets above effective_entry
            t1 = max(t1, normalize_price(effective_entry + Decimal("1.50") * risk_unit, tick_size))
            t2 = max(t2, normalize_price(effective_entry + Decimal("2.50") * risk_unit, tick_size))
            t3 = max(t3, normalize_price(effective_entry + Decimal("3.80") * risk_unit, tick_size))
            if t2 <= t1:
                t2 = normalize_price(t1 + max(tick_size, atr * Decimal("0.5")), tick_size)
            if t3 <= t2:
                t3 = normalize_price(t2 + max(tick_size, atr * Decimal("0.5")), tick_size)

        suggested_entry = effective_entry
        invalidation_stop = effective_stop
    else:
        effective_entry = retest_entry
        if use_confluence_trigger_v2:
            stop_anchor = f"retest_{retest_stop_anchor}"
            breakout_stop = unified_breakout_stop
        else:
            stop_options = (
                (swing_low_4h - Decimal("0.80") * atr, "swing_low_7d"),
                (effective_val - Decimal("0.60") * atr, "value_area_low_30d"),
                (retest_entry - min_retest_risk, "minimum_distance"),
            )
            stop_anchor = min(stop_options, key=lambda item: item[0])[1]
            breakout_stop = floor_to_increment(
                breakout_trigger - max(Decimal("1.0") * atr, breakout_trigger * Decimal("0.025")),
                tick_size,
            )
        effective_stop = retest_stop
        invalidation_stop = retest_stop
        risk_unit = max(effective_entry - effective_stop, min_retest_risk)
        risk_unit = max(risk_unit, EPSILON)

        if use_confluence_trigger_v2:
            t1, t2, t3 = _build_spaced_long_targets(
                effective_entry=effective_entry,
                breakout_trigger=breakout_trigger,
                risk_unit=risk_unit,
                atr=atr,
                poc_30d=poc_30d,
                vah_30d=vah_30d,
                high_30d=high_30d,
                overhead_shelves=overhead_shelves,
                tick_size=tick_size,
            )
        elif close < poc_30d:
            if (poc_30d - effective_entry) < Decimal("1.00") * risk_unit:
                t1 = max(
                    normalize_price(vah_30d, tick_size),
                    normalize_price(effective_entry + Decimal("1.50") * risk_unit, tick_size),
                )
                t2 = max(
                    normalize_price(high_30d, tick_size),
                    normalize_price(effective_entry + Decimal("2.50") * risk_unit, tick_size),
                )
            else:
                min_t1_below_poc = normalize_price(
                    effective_entry + max(tick_size, Decimal("0.50") * risk_unit), tick_size
                )
                t1 = max(normalize_price(poc_30d, tick_size), min_t1_below_poc)
                if local_compression_high_4h <= poc_30d and t1 <= breakout_trigger:
                    t1 = normalize_price(
                        max(
                            vah_30d,
                            breakout_trigger + max(tick_size, Decimal("0.50") * atr),
                        ),
                        tick_size,
                    )
                t2 = max(
                    normalize_price(vah_30d, tick_size),
                    normalize_price(effective_entry + Decimal("2.00") * risk_unit, tick_size),
                )
            if t2 <= t1:
                t2 = normalize_price(t1 + max(tick_size, atr * Decimal("0.5")), tick_size)
            t3 = normalize_price(
                max(high_30d, vah_30d + Decimal("1.5") * atr, effective_entry + Decimal("3.50") * risk_unit),
                tick_size,
            )
            if t3 <= t2:
                t3 = normalize_price(t2 + max(tick_size, atr * Decimal("0.5")), tick_size)
        else:
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

    if not valid_execution_prices(Direction.LONG, suggested_entry, invalidation_stop, (t1, t2, t3)):
        raise InvalidExecutionBracket("long bracket requires positive stop < entry < ordered targets")

    # Compute r_t1, r_t2, r_t3, asymmetric_rr strictly from unified effective_entry and risk_unit
    r_t1 = ((t1 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t2 = ((t2 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    r_t3 = ((t3 - effective_entry) / risk_unit).quantize(Decimal("0.01"))
    # Fix 5 (Atlas v3.0 U1): When close < poc_30d, evaluate R:R to full Value-Area rotation (max of r_t1, r_t2) and cap at 5.0
    if close < poc_30d:
        asymmetric_rr = min(max(r_t1, r_t2), Decimal("5.00")).quantize(Decimal("0.01"))
    else:
        asymmetric_rr = min(r_t2, Decimal("5.00")).quantize(Decimal("0.01"))

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
        diagnostic_context=BracketContext(
            atr_value=atr, atr_timeframe="4h", stop_anchor=stop_anchor,
            t1_before_risk_sizing=initial_t1_before_risk_sizing,
            levels=(("30d POC", poc_30d), ("30d VAH", vah_30d), ("30d VAL", effective_val),
                    ("72h compression high", local_compression_high_4h), ("30d high", high_30d)),
        ),
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

    # Realistic actionable limit entry: strictly bounded within 1.0% to 3.5% above market price
    # Prevents anchoring limit orders to a distant POC that sits 60% above a collapsed coin.
    ceiling_cand = primary_ceiling - Decimal("0.15") * atr
    if close <= ceiling_cand <= close * Decimal("1.040"):
        raw_retest_entry = ceiling_cand
    elif close <= swing_high_4h <= close * Decimal("1.040"):
        raw_retest_entry = swing_high_4h
    else:
        raw_retest_entry = min(close * Decimal("1.025"), close + atr * Decimal("0.75"))

    # Breakdown Trigger Offset: -0.05 * ATR (Page 3 Spec OCR typo audit: '0.85 * ATR' -> '0.05 * ATR')
    raw_breakdown_trigger = local_consolidation_low_4h - Decimal("0.05") * atr
    if raw_breakdown_trigger >= close:
        raw_breakdown_trigger = close - Decimal("0.10") * atr

    # Invariant 2: Symmetric structural stop distance for SHORT setup
    min_short_risk = max(Decimal("1.20") * atr, raw_retest_entry * Decimal("0.035"))
    raw_invalidation_stop = raw_retest_entry + min_short_risk
    if swing_high_4h > raw_retest_entry and swing_high_4h <= raw_retest_entry * Decimal("1.06"):
        raw_invalidation_stop = max(raw_invalidation_stop, swing_high_4h + Decimal("0.40") * atr)

    if raw_invalidation_stop <= raw_retest_entry:
        raw_invalidation_stop = raw_retest_entry + min_short_risk

    is_at_resistance_shelf = close >= (primary_ceiling - (atr * Decimal("0.35")))
    raw_suggested = close if is_at_resistance_shelf else max(close, raw_retest_entry)
    raw_effective_entry = raw_suggested
    risk_unit = max(raw_invalidation_stop - raw_effective_entry, min_short_risk)
    risk_unit = max(risk_unit, EPSILON)

    # Realistic structural short targets scaled by volatility and risk
    # Geometric bounding formula from Task 1:
    # Target_1 = max(Structural_Support, Entry_Price - 1.75 * Risk_Dist, Entry_Price * 0.86)
    # This caps Target_1 at a maximum of 1.75R (or a 14% drop) to prevent ancient wick overshoot.
    structural_support = min(poc_30d, val_30d) if close > poc_30d else val_30d
    raw_t1 = max(
        structural_support,
        raw_effective_entry - Decimal("1.75") * risk_unit,
        raw_effective_entry * Decimal("0.86"),
    )
    if raw_t1 >= raw_breakdown_trigger:
        raw_t1 = raw_breakdown_trigger - tick_size

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
    breakout_trigger = floor_to_increment(raw_breakdown_trigger, tick_size)
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
        breakout_trigger = floor_to_increment(close - tick_size, tick_size)
    if t1 >= breakout_trigger:
        t1 = normalize_price(breakout_trigger - tick_size, tick_size)
    if t2 >= t1:
        t2 = normalize_price(t1 - tick_size, tick_size)
    if t3 >= t2:
        t3 = normalize_price(t2 - tick_size, tick_size)

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

    effective_entry = suggested_entry
    risk_unit = max(invalidation_stop - effective_entry, EPSILON)

    if (breakout_trigger <= 0
            or not valid_execution_prices(Direction.SHORT, suggested_entry, invalidation_stop, (t1, t2, t3))):
        raise InvalidExecutionBracket("short bracket requires positive ordered targets < entry < stop")

    r_t1 = ((effective_entry - t1) / risk_unit).quantize(Decimal("0.01"))
    r_t2 = ((effective_entry - t2) / risk_unit).quantize(Decimal("0.01"))
    r_t3 = ((effective_entry - t3) / risk_unit).quantize(Decimal("0.01"))

    # Invariant 2: Clamp displayed asymmetric_rr = min(r_t2, 5.0)
    asymmetric_rr = min(r_t2, Decimal("5.00")).quantize(Decimal("0.01"))

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
