"""Stateful Candidate Radar Lifecycle & Live System Gate Re-Verification Engine.

Maintains institutional continuity across 4-hour screener sweeps while enforcing
strict live gate transparency:
- Newly discovered setups enter as `FRESH_COIL`.
- Previously qualified setups that pull back into their Limit Retest Entry / 30d POC
  shelf transition to `AT_ENTRY_ZONE` instead of vanishing during the pullback.
- Setups that trigger their breakout or bounce into profit transition to `IN_ACTIVE_TRADE`.
- Every retained setup is re-audited against the 5/5 (L1–L6) institutional screening gates
  on live candles. If any gate drifts (e.g. price slips below 4H EMA50 or 30d POC while still
  above Stop Loss), the live checklist and `failed_gates` are updated so the dashboard
  prominently displays the exact gate warning (`⚠️ 4/5 GATES`, `⚠️ 3/5 GATES`).
- Setups that breach their `suggested_stop`, suffer a structural breakdown below Value Area
  Low + EMA50, complete their final target, or exceed the 72-hour TTL are automatically
  eliminated (`INVALIDATED` / `COMPLETED_TARGET` / `EXPIRED`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class RadarLifecycleState(StrEnum):
    FRESH_COIL = "FRESH_COIL"
    AT_ENTRY_ZONE = "AT_ENTRY_ZONE"
    IN_ACTIVE_TRADE = "IN_ACTIVE_TRADE"
    ACTIVE_COIL = "ACTIVE_COIL"
    COMPLETED_TARGET = "COMPLETED_TARGET"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class RadarLifecycleDecision:
    retained: bool
    state: RadarLifecycleState
    label: str
    reason: str
    current_price: Decimal
    pnl_from_entry_pct: Decimal | None = None
    gates_passed: int = 5
    gates_total: int = 5
    failed_gates: tuple[str, ...] = ()
    updated_checklist: tuple[dict[str, Any], ...] = ()


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        cleaned = str(value).replace("%", "").replace("bps", "").replace("$", "").replace("+", "").strip()
        d = Decimal(cleaned)
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _extract_decimals_from_candles(candles: Sequence[Mapping[str, Any]], key: str) -> list[Decimal]:
    out: list[Decimal] = []
    for c in candles:
        if isinstance(c, Mapping):
            val = _to_dec(c.get(key))
            if val is not None and val > Decimal("0"):
                out.append(val)
    return out


def _compute_ema_from_1h(closes_1h: Sequence[Decimal], span_1h: int = 200) -> Decimal | None:
    """Approximates 4H EMA50 (200 hours) from 1H closes when explicit 4H EMA50 is not passed."""
    if not closes_1h:
        return None
    period = min(len(closes_1h), span_1h)
    if period < 10:
        return closes_1h[-1]
    k = Decimal("2") / Decimal(str(period + 1))
    ema_val = sum(closes_1h[:period], Decimal("0")) / Decimal(str(period))
    for price in closes_1h[period:]:
        ema_val = (price - ema_val) * k + ema_val
    return ema_val


def _fmt_price(val: Decimal) -> str:
    f = float(val)
    if f < 0.0001:
        return f"{f:.6f}"
    if f < 1.0:
        return f"{f:.4f}"
    if f < 10.0:
        return f"{f:.3f}"
    return f"{f:.2f}"


def reverify_candidate_gates(
    candidate: Mapping[str, Any],
    *,
    current_price: Decimal,
    recent_candles_1h: Sequence[Mapping[str, Any]] = (),
    ema50_4h: Decimal | None = None,
) -> tuple[tuple[dict[str, Any], ...], int, int, tuple[str, ...]]:
    """Re-evaluates the 5 institutional verification gates (L1–L6) on live market structure.

    Returns (updated_checklist, passed_count, total_count, failed_gate_titles).
    """
    direction = str(candidate.get("direction", "long")).lower()
    is_short = direction == "short"

    candle_source = recent_candles_1h if recent_candles_1h else (candidate.get("candles") or ())
    closes_1h = _extract_decimals_from_candles(candle_source, "close")

    # Resolve live 4H EMA50: explicit argument -> candidate field -> parsed from checklist -> 1H EMA200
    eff_ema50 = ema50_4h or _to_dec(candidate.get("ema50_4h"))
    if eff_ema50 is None:
        for item in candidate.get("checklist") or ():
            if isinstance(item, Mapping) and "EMA50" in str(item.get("title", "")) + str(item.get("detail", "")):
                m = re.search(r"\$([0-9.]+)", str(item.get("detail", "")))
                if m:
                    eff_ema50 = _to_dec(m.group(1))
                    break
    if eff_ema50 is None and len(closes_1h) >= 40:
        eff_ema50 = _compute_ema_from_1h(closes_1h, span_1h=200)

    poc = _to_dec(candidate.get("poc_price")) or _to_dec(candidate.get("poc_30d"))
    vah = _to_dec(candidate.get("vah_price")) or _to_dec(candidate.get("vah_30d"))
    val = _to_dec(candidate.get("val_price")) or _to_dec(candidate.get("val_30d"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))

    # 1. Gate L1: Trend Baseline (4H EMA50)
    trend_passed = True
    trend_detail = f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)})" if eff_ema50 else "Holding above 4H EMA50 baseline"
    if eff_ema50 is not None and eff_ema50 > Decimal("0"):
        if not is_short:
            # Allow 0.8% wick tolerance on EMA50 shelf
            trend_passed = current_price >= eff_ema50 * Decimal("0.992")
            if trend_passed:
                trend_detail = f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)})"
            else:
                diff_pct = (current_price - eff_ema50) / eff_ema50 * Decimal("100")
                trend_detail = f"Slipped {float(diff_pct):.1f}% below 4H EMA50 (${_fmt_price(eff_ema50)})"
        else:
            trend_passed = current_price <= eff_ema50 * Decimal("1.008")
            if trend_passed:
                trend_detail = f"Capped below 4H EMA50 (${_fmt_price(eff_ema50)})"
            else:
                diff_pct = (current_price - eff_ema50) / eff_ema50 * Decimal("100")
                trend_detail = f"Reclaimed +{float(diff_pct):.1f}% above 4H EMA50 (${_fmt_price(eff_ema50)})"

    # 2. Gate L2: Anti-Chasing / Excursion Validation
    highs_72h = _extract_decimals_from_candles(candle_source[-72:], "high")
    lows_72h = _extract_decimals_from_candles(candle_source[-72:], "low")
    anti_chase_passed = True
    anti_chase_detail = "72h range controlled | Multi-timeframe RSI aligned"
    if highs_72h and lows_72h:
        h72 = max(highs_72h)
        l72 = min(lows_72h)
        rng_72_pct = (h72 - l72) / max(l72, Decimal("1e-8")) * Decimal("100")
        # If already entered lower (current_price > entry), larger range expansion is healthy profit expansion
        in_profit = entry is not None and entry > Decimal("0") and (
            (not is_short and current_price > entry * Decimal("1.02"))
            or (is_short and current_price < entry * Decimal("0.98"))
        )
        anti_chase_passed = rng_72_pct <= Decimal("28.0") or bool(in_profit)
        if anti_chase_passed:
            anti_chase_detail = f"72h range {'expanding in profit' if in_profit and rng_72_pct > Decimal('20.0') else 'tight'} ({float(rng_72_pct):.1f}%)"
        else:
            anti_chase_detail = f"72h excursion wide ({float(rng_72_pct):.1f}%) — wait for pullback"

    # 3. Gate L3: Value Acceptance (30d POC & Value Area)
    value_passed = True
    value_detail = "Accepted above 30d POC"
    if poc is not None and poc > Decimal("0"):
        vah_str = f" | VAH: ${_fmt_price(vah)}" if vah is not None else ""
        if not is_short:
            # Passes if holding at/above POC (with 1.0% shelf tolerance)
            value_passed = current_price >= poc * Decimal("0.990")
            if value_passed:
                value_detail = f"Accepted above 30d POC (${_fmt_price(poc)}){vah_str}"
            else:
                diff_poc = (current_price - poc) / poc * Decimal("100")
                val_str = f" | Testing VAL (${_fmt_price(val)})" if val is not None else ""
                value_detail = f"Below 30d POC (${_fmt_price(poc)}, {float(diff_poc):.1f}%){val_str}"
        else:
            value_passed = current_price <= poc * Decimal("1.010")
            if value_passed:
                value_detail = f"Rejected below 30d POC (${_fmt_price(poc)})"
            else:
                value_detail = f"Trading above 30d POC (${_fmt_price(poc)})"

    # 4. Gate L4: Volatility Compression / Breakout Structure
    vol_passed = True
    vol_detail = "Volatility structure intact"
    bbw = _to_dec(candidate.get("bb_bandwidth_4h"))
    if bbw is None:
        for item in candidate.get("checklist") or ():
            if isinstance(item, Mapping) and "Bandwidth" in str(item.get("detail", "")):
                m = re.search(r"Bandwidth\s+([0-9.]+)", str(item.get("detail", "")))
                if m:
                    bbw = _to_dec(m.group(1))
                    break
    if bbw is not None:
        # If bandwidth expanded (>0.24), check if it expanded in the trade's favor
        in_favorable_expansion = (
            (not is_short and vah is not None and current_price >= vah)
            or (not is_short and entry is not None and current_price >= entry * Decimal("1.02"))
        )
        vol_passed = bbw <= Decimal("0.24") or in_favorable_expansion
        if bbw <= Decimal("0.20"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (BB inside KC Squeeze)"
        elif in_favorable_expansion:
            vol_detail = f"Bandwidth {float(bbw):.2f} (Expanding into active breakout)"
        else:
            vol_detail = f"Bandwidth loosened ({float(bbw):.2f}) without upside breakout"

    # 5. Gate L5/L6: Smart Money & Derivatives Absorption
    oi_7d = _to_dec(candidate.get("oi_change_7d_pct"))
    funding_bps = _to_dec(candidate.get("funding_rate_bps"))
    vcr = _to_dec(candidate.get("volume_contraction_ratio")) or _to_dec(candidate.get("vcr_4h"))
    sm_passed = True
    if oi_7d is not None and not is_short and oi_7d < Decimal("-8.0"):
        sm_passed = False
    if vcr is not None and vcr > Decimal("1.45") and not (entry is not None and current_price > entry * Decimal("1.02")):
        sm_passed = False
    oi_str = f"7d OI {float(oi_7d):+.1f}%" if oi_7d is not None else "OI steady"
    fund_str = f", funding {float(funding_bps):+.2f} bps" if funding_bps is not None else ""
    sm_detail = f"{oi_str}{fund_str}" if sm_passed else f"Orderflow weakened ({oi_str}{fund_str})"

    updated_checklist: tuple[dict[str, Any], ...] = (
        {"title": "Trend Baseline", "passed": trend_passed, "detail": trend_detail},
        {"title": "Anti-Chasing Validated", "passed": anti_chase_passed, "detail": anti_chase_detail},
        {"title": "Value Acceptance", "passed": value_passed, "detail": value_detail},
        {"title": "Volatility Compression", "passed": vol_passed, "detail": vol_detail},
        {"title": "Smart Money Absorption", "passed": sm_passed, "detail": sm_detail},
    )
    failed_titles: tuple[str, ...] = tuple(
        str(item["title"]) for item in updated_checklist if not item["passed"]
    )
    passed_count = len(updated_checklist) - len(failed_titles)
    return updated_checklist, passed_count, len(updated_checklist), failed_titles


def evaluate_radar_candidate_lifecycle(
    candidate: Mapping[str, Any],
    *,
    current_price: Decimal,
    recent_candles_1h: Sequence[Mapping[str, Any]] = (),
    ema50_4h: Decimal | None = None,
    now: datetime | None = None,
    max_age_hours: float = 72.0,
) -> RadarLifecycleDecision:
    """Evaluate whether a previously discovered radar candidate remains an active trade setup
    and re-verify its 5/5 (L1–L6) institutional screening gates against live price action.
    """
    ref_now = now or datetime.now(UTC)
    direction = str(candidate.get("direction", "long")).lower()
    is_short = direction == "short"

    stop = _to_dec(candidate.get("suggested_stop"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))
    trigger = _to_dec(candidate.get("breakout_trigger"))
    poc = _to_dec(candidate.get("poc_price")) or _to_dec(candidate.get("poc_30d"))
    val = _to_dec(candidate.get("val_price")) or _to_dec(candidate.get("val_30d"))
    vah = _to_dec(candidate.get("vah_price")) or _to_dec(candidate.get("vah_30d"))

    raw_targets = candidate.get("suggested_targets") or ()
    targets: list[Decimal] = []
    if isinstance(raw_targets, (list, tuple)):
        for t in raw_targets:
            td = _to_dec(t)
            if td is not None and td > Decimal("0"):
                targets.append(td)

    if current_price <= Decimal("0") or stop is None or stop <= Decimal("0"):
        return RadarLifecycleDecision(
            retained=False,
            state=RadarLifecycleState.INVALIDATED,
            label="❌ INVALIDATED",
            reason="missing_price_or_stop",
            current_price=current_price,
        )

    evaluated_at = _parse_iso(candidate.get("evaluated_at")) or _parse_iso(candidate.get("first_seen_at"))
    eval_epoch = int(evaluated_at.timestamp()) if evaluated_at else 0

    # Filter candles that occurred AFTER the setup was evaluated
    post_scan_candles = [
        c for c in recent_candles_1h
        if int(c.get("time", 0)) >= eval_epoch and float(c.get("low", 0)) > 0
    ]

    # 1. Hard Stop-Loss Invalidation Check (Spot Price + Post-Scan Candle Extremes)
    if not is_short:
        post_lows = _extract_decimals_from_candles(post_scan_candles, "low")
        min_low_since_scan = min(post_lows) if post_lows else current_price
        if current_price <= stop or min_low_since_scan <= stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="❌ STOP HIT",
                reason="long_stop_loss_breached",
                current_price=current_price,
            )
        # Also invalidate if a Long setup has neither triggered nor held structure and broke >3.5% below VAL & EMA50
        if (
            val is not None
            and val > Decimal("0")
            and current_price < val * Decimal("0.965")
            and (ema50_4h is None or current_price < ema50_4h * Decimal("0.965"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="❌ VALUE AREA LOST",
                reason="long_value_area_breakdown",
                current_price=current_price,
            )
    else:
        post_highs = _extract_decimals_from_candles(post_scan_candles, "high")
        max_high_since_scan = max(post_highs) if post_highs else current_price
        # For shorts, also enforce a tight 6% squeeze cap above entry if stop is wide
        effective_short_stop = min(stop, entry * Decimal("1.06")) if entry else stop
        if current_price >= effective_short_stop or max_high_since_scan >= effective_short_stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="❌ SHORT SQUEEZED",
                reason="short_stop_loss_breached",
                current_price=current_price,
            )
        if (
            vah is not None
            and vah > Decimal("0")
            and current_price > vah * Decimal("1.035")
            and (ema50_4h is None or current_price > ema50_4h * Decimal("1.035"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="❌ SHORT INVALIDATED",
                reason="short_reclaimed_above_value",
                current_price=current_price,
            )

    # 2. Target Completion Check (Graduated after reaching Target 2 / Target 3)
    if len(targets) >= 2:
        completion_target = targets[-1]
        if not is_short and current_price >= completion_target:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.COMPLETED_TARGET,
                label="🎯 TARGET HIT",
                reason="long_final_target_reached",
                current_price=current_price,
            )
        if is_short and current_price <= completion_target:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.COMPLETED_TARGET,
                label="🎯 TARGET HIT",
                reason="short_final_target_reached",
                current_price=current_price,
            )

    # 3. Re-Verify Live System Gates (5/5 Checklist)
    updated_checklist, gates_passed, gates_total, failed_gates = reverify_candidate_gates(
        candidate,
        current_price=current_price,
        recent_candles_1h=recent_candles_1h,
        ema50_4h=ema50_4h,
    )

    # 4. Classify Active Trade State (AT_ENTRY_ZONE vs IN_ACTIVE_TRADE vs ACTIVE_COIL)
    pnl_pct: Decimal | None = None
    if entry is not None and entry > Decimal("0"):
        raw_pnl = (
            ((current_price - entry) / entry * Decimal("100"))
            if not is_short
            else ((entry - current_price) / entry * Decimal("100"))
        )
        pnl_pct = raw_pnl.quantize(Decimal("0.1"))

    if not is_short:
        candle_source = recent_candles_1h if recent_candles_1h else (candidate.get("candles") or ())
        lows_48h = _extract_decimals_from_candles(candle_source[-48:], "low")
        highs_48h = _extract_decimals_from_candles(candle_source[-48:], "high")
        low_48h = min(lows_48h) if lows_48h else current_price
        high_48h = max(highs_48h) if highs_48h else current_price

        at_entry_shelf = False
        if entry is not None and entry > Decimal("0"):
            dist_to_entry = abs(current_price - entry) / entry
            if dist_to_entry <= Decimal("0.018"):
                at_entry_shelf = True
        if not at_entry_shelf and poc is not None and poc > Decimal("0"):
            if abs(current_price - poc) / poc <= Decimal("0.012"):
                at_entry_shelf = True

        filled_retest = (
            entry is not None
            and low_48h <= entry * Decimal("1.015")
            and current_price >= entry * Decimal("1.018")
        )
        expanding_above_value = (
            pnl_pct is not None
            and pnl_pct >= Decimal("3.0")
            and (vah is None or current_price >= vah)
        )
        triggered_breakout = (
            trigger is not None
            and trigger > Decimal("0")
            and (
                current_price >= trigger
                or (high_48h >= trigger and current_price >= (entry or trigger) * Decimal("0.99"))
            )
        )

        if triggered_breakout or filled_retest or expanding_above_value:
            gain_str = f" ({pnl_pct:+.1f}%)" if pnl_pct is not None and pnl_pct > Decimal("0") else ""
            return RadarLifecycleDecision(
                retained=True,
                state=RadarLifecycleState.IN_ACTIVE_TRADE,
                label=f"🚀 IN TRADE{gain_str}",
                reason="active_trade_running",
                current_price=current_price,
                pnl_from_entry_pct=pnl_pct,
                gates_passed=gates_passed,
                gates_total=gates_total,
                failed_gates=failed_gates,
                updated_checklist=updated_checklist,
            )

        if at_entry_shelf:
            return RadarLifecycleDecision(
                retained=True,
                state=RadarLifecycleState.AT_ENTRY_ZONE,
                label="🟢 AT ENTRY ZONE",
                reason="testing_limit_entry_shelf",
                current_price=current_price,
                pnl_from_entry_pct=pnl_pct,
                gates_passed=gates_passed,
                gates_total=gates_total,
                failed_gates=failed_gates,
                updated_checklist=updated_checklist,
            )

    # 5. Expiration Check (Only expire if not in an active triggered trade and older than max_age_hours)
    if evaluated_at is not None:
        age_hours = (ref_now - evaluated_at).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.EXPIRED,
                label="⌛ EXPIRED",
                reason="max_retention_age_exceeded",
                current_price=current_price,
                gates_passed=gates_passed,
                gates_total=gates_total,
                failed_gates=failed_gates,
                updated_checklist=updated_checklist,
            )

    coil_label = (
        "⏳ ACTIVE COIL"
        if gates_passed == gates_total
        else f"⚠️ COIL ({gates_passed}/{gates_total} GATES)"
    )
    return RadarLifecycleDecision(
        retained=True,
        state=RadarLifecycleState.ACTIVE_COIL,
        label=coil_label,
        reason="holding_structure_above_stop",
        current_price=current_price,
        pnl_from_entry_pct=pnl_pct,
        gates_passed=gates_passed,
        gates_total=gates_total,
        failed_gates=failed_gates,
        updated_checklist=updated_checklist,
    )
