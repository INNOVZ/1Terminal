"""Research-only radar lifecycle policy (v2).

Keep immutable discovery references separate from fresh verification inputs. Unknown
telemetry never certifies a gate. Trigger observations do not assert executed fills.
Stops take conservative priority when a candle touches both stop and target. Untriggered
setups expire at 72 hours, including entry shelves; observed triggers persist until
invalidation/completion. Historical OHLC cannot order events within a discovery bar.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    triggered_at: str | None = None
    trigger_basis: str | None = None
    trigger_entry_price: Decimal | None = None
    trigger_stop_price: Decimal | None = None


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        cleaned = (
            str(value).replace("%", "").replace("bps", "").replace("$", "").replace("+", "").strip()
        )
        d = Decimal(cleaned)
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo is not None else None
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


def _fmt_price(val: Decimal) -> str:
    f = float(val)
    if f < 0.01:
        return f"{f:.6f}"
    if f < 0.1:
        return f"{f:.5f}"
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

    candle_source = recent_candles_1h
    closes_1h = _extract_decimals_from_candles(candle_source, "close")

    # Only explicit, freshly computed telemetry is eligible for re-verification.
    # Frozen value-area levels remain the original setup's structural references.
    eff_ema50 = ema50_4h
    poc = _to_dec(candidate.get("poc_price")) or _to_dec(candidate.get("poc_30d"))
    vah = _to_dec(candidate.get("vah_price")) or _to_dec(candidate.get("vah_30d"))
    val = _to_dec(candidate.get("val_price")) or _to_dec(candidate.get("val_30d"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))

    # 1. Gate L1: Trend Baseline (4H EMA50)
    trend_passed = True
    trend_detail = (
        f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)})"
        if eff_ema50
        else "Holding above 4H EMA50"
    )
    if eff_ema50 is not None and eff_ema50 > Decimal("0"):
        diff_ema_pct = (current_price - eff_ema50) / eff_ema50 * Decimal("100")
        if not is_short:
            # Allow 0.8% wick tolerance on EMA50 shelf
            trend_passed = current_price >= eff_ema50 * Decimal("0.992")
            if current_price >= eff_ema50:
                trend_detail = f"Holding above 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
            elif trend_passed:
                trend_detail = f"Testing 4H EMA50 support (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
            else:
                trend_detail = (
                    f"Slipped {float(diff_ema_pct):.1f}% below 4H EMA50 (${_fmt_price(eff_ema50)})"
                )
        else:
            trend_passed = current_price <= eff_ema50 * Decimal("1.008")
            if current_price <= eff_ema50:
                trend_detail = (
                    f"Capped below 4H EMA50 (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
                )
            elif trend_passed:
                trend_detail = f"Testing 4H EMA50 resistance (${_fmt_price(eff_ema50)}, {float(diff_ema_pct):+.1f}%)"
            else:
                trend_detail = f"Reclaimed +{float(diff_ema_pct):.1f}% above 4H EMA50 (${_fmt_price(eff_ema50)})"

    # 2. Gate L2: Anti-Chasing / Excursion Validation (preserve exact 1D/1W RSI telemetry)
    highs_72h = _extract_decimals_from_candles(candle_source[-72:], "high")
    lows_72h = _extract_decimals_from_candles(candle_source[-72:], "low")
    rsi_suffix = ""
    for item in candidate.get("checklist") or ():
        if isinstance(item, Mapping) and "RSI" in str(item.get("detail", "")):
            parts = [p.strip() for p in str(item.get("detail", "")).split("|") if "RSI" in p]
            if parts:
                rsi_suffix = " | " + " | ".join(parts)
            break
    if not rsi_suffix:
        r1d = _to_dec(candidate.get("rsi_1d"))
        r1w = _to_dec(candidate.get("rsi_1w"))
        if r1d is not None and r1w is not None:
            rsi_suffix = f" | 1D RSI {float(r1d):.1f} | 1W RSI {float(r1w):.1f}"
        else:
            rsi_suffix = " | discovery RSI unavailable"

    anti_chase_passed = True
    anti_chase_detail = f"72h range controlled{rsi_suffix}"
    if highs_72h and lows_72h:
        h72 = max(highs_72h)
        l72 = min(lows_72h)
        rng_72_pct = (h72 - l72) / max(l72, Decimal("1e-8")) * Decimal("100")
        in_profit = (
            entry is not None
            and entry > Decimal("0")
            and (
                (not is_short and current_price > entry * Decimal("1.02"))
                or (is_short and current_price < entry * Decimal("0.98"))
            )
        )
        anti_chase_passed = rng_72_pct <= Decimal("28.0") or bool(in_profit)
        if anti_chase_passed:
            state_word = (
                "expanding in profit" if (in_profit and rng_72_pct > Decimal("20.0")) else "tight"
            )
            anti_chase_detail = f"72h range {state_word} ({float(rng_72_pct):.1f}%){rsi_suffix}"
        else:
            anti_chase_detail = f"72h excursion wide ({float(rng_72_pct):.1f}%){rsi_suffix}"

    # 3. Gate L3: Value Acceptance (30d POC & Value Area)
    value_passed = True
    value_detail = "Accepted above 30d POC"
    if poc is not None and poc > Decimal("0"):
        diff_poc = (current_price - poc) / poc * Decimal("100")
        vah_str = f" | VAH: ${_fmt_price(vah)}" if vah is not None else ""
        if not is_short:
            # Passes if holding at/above POC (with 1.0% retest shelf tolerance)
            value_passed = current_price >= poc * Decimal("0.990")
            if current_price >= poc:
                value_detail = f"Accepted above 30d POC (${_fmt_price(poc)}){vah_str}"
            elif value_passed:
                value_detail = (
                    f"Testing 30d POC shelf (${_fmt_price(poc)}, {float(diff_poc):+.1f}%){vah_str}"
                )
            else:
                val_str = f" | Testing VAL (${_fmt_price(val)})" if val is not None else ""
                value_detail = (
                    f"Below 30d POC (${_fmt_price(poc)}, {float(diff_poc):.1f}%){val_str}"
                )
        else:
            value_passed = current_price <= poc * Decimal("1.010")
            if current_price <= poc:
                value_detail = f"Rejected below 30d POC (${_fmt_price(poc)})"
            elif value_passed:
                value_detail = (
                    f"Testing 30d POC resistance (${_fmt_price(poc)}, {float(diff_poc):+.1f}%)"
                )
            else:
                value_detail = (
                    f"Trading above 30d POC (${_fmt_price(poc)}, {float(diff_poc):+.1f}%)"
                )

    # 4. Gate L4: Volatility Compression / Breakout Structure
    vol_passed = True
    vol_detail = "Volatility compression intact"
    metrics = candidate.get("radar_metrics") or {}
    bbw = _to_dec(metrics.get("bb_bandwidth_4h"))
    kc_sq = metrics.get("kc_squeeze_4h")
    if bbw is not None:
        in_favorable_expansion = (not is_short and vah is not None and current_price >= vah) or (
            not is_short and entry is not None and current_price >= entry * Decimal("1.02")
        )
        vol_passed = bbw <= Decimal("0.24") or in_favorable_expansion
        if bbw <= Decimal("0.13") or kc_sq is True and bbw <= Decimal("0.15"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (BB inside KC Squeeze)"
        elif bbw <= Decimal("0.20"):
            vol_detail = f"Bandwidth {float(bbw):.2f} (Controlled 4H Base Coil)"
        elif in_favorable_expansion:
            vol_detail = f"Bandwidth {float(bbw):.2f} (Expanding in Active Trade)"
        else:
            vol_detail = f"Bandwidth loosened ({float(bbw):.2f}) without upside breakout"

    # 5. Gate L5/L6: Smart Money & Derivatives Absorption
    oi_7d = _to_dec(metrics.get("oi_change_7d_pct"))
    funding_bps = _to_dec(metrics.get("funding_rate_bps"))
    taker_r = _to_dec(metrics.get("taker_buy_sell_ratio"))
    vcr = _to_dec(metrics.get("volume_contraction_ratio"))
    sm_passed = True
    if oi_7d is not None and not is_short and oi_7d < Decimal("-8.0"):
        sm_passed = False
    if (
        vcr is not None
        and vcr > Decimal("1.45")
        and not (
            entry is not None
            and (
                (not is_short and current_price > entry * Decimal("1.02"))
                or (is_short and current_price < entry * Decimal("0.98"))
            )
        )
    ):
        sm_passed = False
    oi_str = f"7d OI {float(oi_7d):+.1f}%" if oi_7d is not None else "7d OI unavailable"
    fund_str = f", funding {float(funding_bps):+.2f} bps" if funding_bps is not None else ""
    taker_str = f", taker {float(taker_r):.2f}" if taker_r is not None else ""
    sm_detail = (
        f"{oi_str}{fund_str}{taker_str}"
        if sm_passed
        else f"Orderflow weakened ({oi_str}{fund_str}{taker_str})"
    )

    availability = (
        eff_ema50 is not None and eff_ema50 > 0,
        len(closes_1h) >= 72,
        poc is not None and poc > 0,
        bbw is not None,
        oi_7d is not None and vcr is not None,
    )
    checks = (
        ("Trend Baseline", trend_passed, trend_detail),
        ("Anti-Chasing Validated", anti_chase_passed, anti_chase_detail),
        ("Value Acceptance", value_passed, value_detail + " (discovery reference)"),
        ("Volatility Compression", vol_passed, vol_detail),
        ("Smart Money Absorption", sm_passed, sm_detail),
    )
    updated_checklist: tuple[dict[str, Any], ...] = tuple(
        {
            "title": title,
            "passed": bool(known and passed),
            "status": ("passed" if passed else "failed") if known else "unknown",
            "detail": detail if known else "Unavailable: fresh telemetry required",
        }
        for (title, passed, detail), known in zip(checks, availability, strict=True)
    )
    failed_titles: tuple[str, ...] = tuple(
        str(item["title"]) for item in updated_checklist if item["status"] == "failed"
    )
    passed_count = sum(item["passed"] for item in updated_checklist)
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

    evaluated_at = _parse_iso(candidate.get("first_seen_at")) or _parse_iso(
        candidate.get("evaluated_at")
    )
    prior_trigger = _parse_iso(candidate.get("radar_triggered_at"))
    triggered_at = (
        prior_trigger if prior_trigger and evaluated_at is not None
        and evaluated_at <= prior_trigger <= ref_now else None
    )
    raw_prior_basis = str(candidate.get("radar_trigger_basis", "")).lower()
    locked_entry = _to_dec(candidate.get("radar_trigger_entry"))
    locked_stop = _to_dec(candidate.get("radar_trigger_stop"))
    has_locked_reference = (
        triggered_at is not None and raw_prior_basis in ("retest", "breakout")
        and locked_entry is not None and locked_stop is not None
        and locked_entry > 0 and locked_stop > 0
        and (locked_stop > locked_entry if is_short else locked_stop < locked_entry)
    )
    if has_locked_reference:
        stop = locked_stop

    raw_targets = candidate.get("suggested_targets") or ()
    targets: list[Decimal] = []
    if isinstance(raw_targets, (list, tuple)):
        for t in raw_targets:
            td = _to_dec(t)
            if td is not None and td > Decimal("0"):
                targets.append(td)

    if (
        not current_price.is_finite()
        or current_price <= Decimal("0")
        or stop is None
        or stop <= Decimal("0")
    ):
        return RadarLifecycleDecision(
            retained=False,
            state=RadarLifecycleState.INVALIDATED,
            label="INVALIDATED",
            reason="missing_price_or_stop",
            current_price=current_price,
        )

    eval_epoch = int(evaluated_at.timestamp()) if evaluated_at else 0

    invalid_discovery = evaluated_at is None or evaluated_at > ref_now
    # A candle overlapping discovery cannot establish post-discovery event ordering.
    post_scan_candles = [
        c
        for c in recent_candles_1h
        if not invalid_discovery
        and int(c.get("opened_time", int(c.get("time", 0)) - 3600)) >= eval_epoch
        and int(c.get("time", 0)) <= int(ref_now.timestamp())
        and (_to_dec(c.get("low")) or Decimal("0")) > 0
    ]

    # Locked stops apply after the observed trigger. An overlapping candle cannot
    # establish whether its wick preceded that observation.
    stop_candles = post_scan_candles
    if has_locked_reference and triggered_at is not None:
        stop_candles = [c for c in post_scan_candles
                        if int(c.get("opened_time", int(c["time"]) - 3600)) >= triggered_at.timestamp()]

    # 1. Hard Stop-Loss Invalidation Check (Spot Price + Post-Scan Candle Extremes)
    if not is_short:
        post_lows = _extract_decimals_from_candles(stop_candles, "low")
        min_low_since_scan = min(post_lows) if post_lows else current_price
        if current_price <= stop or min_low_since_scan <= stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="STOP HIT",
                reason="long_stop_loss_breached",
                current_price=current_price,
            )
        # Also invalidate if a Long setup has neither triggered nor held structure and broke >3.5% below VAL & EMA50
        if (
            val is not None
            and val > Decimal("0")
            and current_price < val * Decimal("0.965")
            and (ema50_4h is not None and current_price < ema50_4h * Decimal("0.965"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="VALUE AREA LOST",
                reason="long_value_area_breakdown",
                current_price=current_price,
            )
    else:
        post_highs = _extract_decimals_from_candles(stop_candles, "high")
        max_high_since_scan = max(post_highs) if post_highs else current_price
        # For shorts, also enforce a tight 6% squeeze cap above entry if stop is wide
        stop_entry = locked_entry if has_locked_reference else entry
        effective_short_stop = min(stop, stop_entry * Decimal("1.06")) if stop_entry else stop
        if current_price >= effective_short_stop or max_high_since_scan >= effective_short_stop:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT SQUEEZED",
                reason="short_stop_loss_breached",
                current_price=current_price,
            )
        if (
            vah is not None
            and vah > Decimal("0")
            and current_price > vah * Decimal("1.035")
            and (ema50_4h is not None and current_price > ema50_4h * Decimal("1.035"))
        ):
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT INVALIDATED",
                reason="short_reclaimed_above_value",
                current_price=current_price,
            )

    if evaluated_at is None or evaluated_at > ref_now:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.ACTIVE_COIL,
            label="DATA UNAVAILABLE",
            reason="invalid_discovery_timestamp",
            current_price=current_price,
            gates_passed=0,
        )
    # 3. Re-Verify Live System Gates (5/5 Checklist)
    updated_checklist, gates_passed, gates_total, failed_gates = reverify_candidate_gates(
        candidate,
        current_price=current_price,
        recent_candles_1h=recent_candles_1h,
        ema50_4h=ema50_4h,
    )

    # 4. Classify Active Trade State (AT_ENTRY_ZONE vs IN_ACTIVE_TRADE vs ACTIVE_COIL)
    # Trigger is an observed setup event, never an assertion of an executed fill.
    execution_mode = str(candidate.get("execution_mode", "")).upper()
    allow_breakout_trigger = execution_mode != "WAIT_FOR_RETEST_ONLY"
    allow_retest_trigger = execution_mode != "CEILING_BREAKOUT"

    first_trigger_basis = raw_prior_basis if has_locked_reference else None
    deadline = evaluated_at + timedelta(hours=max_age_hours)
    # Previously triggered legacy episodes with no saved basis stay unknown.
    # New episodes select their basis once and persist it through the service.
    if triggered_at is None:
        for c in sorted(post_scan_candles, key=lambda c: int(c["time"])):
            if int(c["time"]) > int(deadline.timestamp()):
                continue
            high, low, close = (_to_dec(c.get(k)) for k in ("high", "low", "close"))
            breakout = (
                allow_breakout_trigger
                and trigger is not None
                and (
                    (not is_short and high is not None and high >= trigger)
                    or (is_short and low is not None and low <= trigger)
                )
            )
            # A retest requires the entire candle to open after discovery and close
            # favorably after touching entry; this is an observation, not a fill model.
            retest = (
                allow_retest_trigger
                and entry is not None
                and high is not None
                and low is not None
                and close is not None
                and (
                    (not is_short and low <= entry and close >= entry * Decimal("1.018"))
                    or (is_short and high >= entry and close <= entry * Decimal("0.982"))
                )
            )
            if breakout or retest:
                triggered_at = datetime.fromtimestamp(int(c["time"]), UTC)
                first_trigger_basis = "retest" if retest else "breakout"
                break
    if (
        triggered_at is None
        and ref_now < deadline
        and allow_breakout_trigger
        and trigger is not None
        and ((not is_short and current_price >= trigger) or (is_short and current_price <= trigger))
    ):
        triggered_at = ref_now
        first_trigger_basis = "breakout"

    trigger_entry_basis = locked_entry if has_locked_reference else None
    trigger_stop_basis = locked_stop if has_locked_reference else None
    if first_trigger_basis is not None and not has_locked_reference:
        trigger_entry_basis = trigger if first_trigger_basis == "breakout" else entry
        trigger_stop_basis = _to_dec(candidate.get(f"{first_trigger_basis}_stop")) or stop
        if (trigger_entry_basis is None or trigger_stop_basis is None
                or trigger_entry_basis <= 0 or trigger_stop_basis <= 0
                or (trigger_stop_basis <= trigger_entry_basis if is_short
                    else trigger_stop_basis >= trigger_entry_basis)):
            first_trigger_basis = None
            trigger_entry_basis = trigger_stop_basis = None

    # A newly observed trigger must enforce its own stop in this evaluation too.
    if not has_locked_reference and triggered_at is not None and trigger_stop_basis is not None:
        after_trigger = [c for c in post_scan_candles
                         if int(c.get("opened_time", int(c["time"]) - 3600)) >= triggered_at.timestamp()]
        effective_stop = trigger_stop_basis
        if is_short and trigger_entry_basis is not None:
            effective_stop = min(effective_stop, trigger_entry_basis * Decimal("1.06"))
        extremes = _extract_decimals_from_candles(after_trigger, "high" if is_short else "low")
        stopped = (max([current_price, *extremes]) >= effective_stop if is_short
                   else min([current_price, *extremes]) <= effective_stop)
        if stopped:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.INVALIDATED,
                label="SHORT SQUEEZED" if is_short else "STOP HIT",
                reason="short_stop_loss_breached" if is_short else "long_stop_loss_breached",
                current_price=current_price,
                triggered_at=triggered_at.isoformat(),
                trigger_basis=first_trigger_basis,
                trigger_entry_price=trigger_entry_basis,
                trigger_stop_price=trigger_stop_basis,
            )

    pnl_pct: Decimal | None = None
    if trigger_entry_basis is not None and trigger_entry_basis > Decimal("0"):
        raw_pnl = (
            ((current_price - trigger_entry_basis) / trigger_entry_basis * Decimal("100"))
            if not is_short
            else ((trigger_entry_basis - current_price) / trigger_entry_basis * Decimal("100"))
        )
        pnl_pct = raw_pnl.quantize(Decimal("0.1"))
    # Expired, never-triggered setups cannot become retrospective target winners.
    if triggered_at is not None or ref_now < deadline:
        # 2. Target Completion Check (Graduated after reaching Target 2 / Target 3)
        if targets:
            completion_target = targets[-1]
            if (
                not is_short
                and max([current_price] + _extract_decimals_from_candles(post_scan_candles, "high"))
                >= completion_target
            ):
                return RadarLifecycleDecision(
                    retained=False,
                    state=RadarLifecycleState.COMPLETED_TARGET,
                    label="TARGET HIT",
                    reason="long_final_target_reached",
                    current_price=current_price,
                )
            if (
                is_short
                and min([current_price] + _extract_decimals_from_candles(post_scan_candles, "low"))
                <= completion_target
            ):
                return RadarLifecycleDecision(
                    retained=False,
                    state=RadarLifecycleState.COMPLETED_TARGET,
                    label="TARGET HIT",
                    reason="short_final_target_reached",
                    current_price=current_price,
                )

    if triggered_at is not None:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.IN_ACTIVE_TRADE,
            label="TRIGGER OBSERVED",
            reason="setup_trigger_observed",
            current_price=current_price,
            pnl_from_entry_pct=pnl_pct,
            gates_passed=gates_passed,
            gates_total=gates_total,
            failed_gates=failed_gates,
            updated_checklist=updated_checklist,
            triggered_at=triggered_at.isoformat(),
            trigger_basis=first_trigger_basis or "unknown",
            trigger_entry_price=trigger_entry_basis,
            trigger_stop_price=trigger_stop_basis,
        )
    at_entry_shelf = any(
        level is not None and level > 0 and abs(current_price - level) / level <= tolerance
        for level, tolerance in ((entry, Decimal("0.018")), (poc, Decimal("0.012")))
    )
    # 5. Expiration Check (Only expire if not in an active triggered trade and older than max_age_hours)
    if evaluated_at is not None:
        age_hours = (ref_now - evaluated_at).total_seconds() / 3600.0
        if age_hours >= max_age_hours:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.EXPIRED,
                label="EXPIRED",
                reason="max_retention_age_exceeded",
                current_price=current_price,
                gates_passed=gates_passed,
                gates_total=gates_total,
                failed_gates=failed_gates,
                updated_checklist=updated_checklist,
            )

    if at_entry_shelf:
        return RadarLifecycleDecision(
            retained=True,
            state=RadarLifecycleState.AT_ENTRY_ZONE,
            label="AT ENTRY ZONE",
            reason="testing_limit_entry_shelf",
            current_price=current_price,
            pnl_from_entry_pct=pnl_pct,
            gates_passed=gates_passed,
            gates_total=gates_total,
            failed_gates=failed_gates,
            updated_checklist=updated_checklist,
        )

    coil_label = (
        "ACTIVE COIL"
        if gates_passed == gates_total
        else f"COIL ({gates_passed}/{gates_total} GATES)"
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


def same_radar_episode(previous: Mapping[str, Any], fresh: Mapping[str, Any]) -> bool:
    """A continuing thesis retains its original bracket, discovery time and events.

    A changed direction or archetype is a new thesis. An active original thesis must
    be reconciled before it can be replaced. Score/price drift alone is not renewal.
    """
    return all(previous.get(k) == fresh.get(k) for k in ("symbol", "direction", "setup_label"))
