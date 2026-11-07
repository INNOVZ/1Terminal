"""Stateful Candidate Radar Lifecycle & Active Trade Retention Engine.

Maintains institutional continuity across 4-hour screener sweeps:
- Newly discovered setups enter as `FRESH_COIL`.
- Previously qualified setups that pull back into their Limit Retest Entry / 30d POC
  shelf transition to `AT_ENTRY_ZONE` instead of vanishing during the pullback.
- Setups that trigger their breakout or bounce into profit transition to `IN_ACTIVE_TRADE`.
- Setups that breach their `suggested_stop` (Invalidation Stop Loss), suffer a structural
  breakdown below Value Area Low + EMA50, or exceed the 72-hour TTL are automatically
  eliminated (`INVALIDATED` / `EXPIRED`).
"""

from __future__ import annotations

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


def _to_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
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


def evaluate_radar_candidate_lifecycle(
    candidate: Mapping[str, Any],
    *,
    current_price: Decimal,
    recent_candles_1h: Sequence[Mapping[str, Any]] = (),
    ema50_4h: Decimal | None = None,
    now: datetime | None = None,
    max_age_hours: float = 72.0,
) -> RadarLifecycleDecision:
    """Evaluate whether a previously discovered radar candidate remains an active trade setup."""
    ref_now = now or datetime.now(UTC)
    direction = str(candidate.get("direction", "long")).lower()
    is_short = direction == "short"

    stop = _to_dec(candidate.get("suggested_stop"))
    entry = _to_dec(candidate.get("suggested_entry")) or _to_dec(candidate.get("pullback_entry"))
    trigger = _to_dec(candidate.get("breakout_trigger"))
    poc = _to_dec(candidate.get("poc_price"))
    val = _to_dec(candidate.get("val_price"))
    vah = _to_dec(candidate.get("vah_price"))

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
        min_low_since_scan = min(
            (_to_dec(c.get("low")) for c in post_scan_candles if _to_dec(c.get("low")) is not None),
            default=current_price,
        )
        if current_price <= stop or (min_low_since_scan is not None and min_low_since_scan <= stop):
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
        max_high_since_scan = max(
            (_to_dec(c.get("high")) for c in post_scan_candles if _to_dec(c.get("high")) is not None),
            default=current_price,
        )
        # For shorts, also enforce a tight 6% squeeze cap above entry if stop is wide
        effective_short_stop = min(stop, entry * Decimal("1.06")) if entry else stop
        if current_price >= effective_short_stop or (
            max_high_since_scan is not None and max_high_since_scan >= effective_short_stop
        ):
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

    # 3. Classify Active Trade State (AT_ENTRY_ZONE vs IN_ACTIVE_TRADE vs ACTIVE_COIL)
    pnl_pct: Decimal | None = None
    if entry is not None and entry > Decimal("0"):
        raw_pnl = ((current_price - entry) / entry * Decimal("100")) if not is_short else ((entry - current_price) / entry * Decimal("100"))
        pnl_pct = raw_pnl.quantize(Decimal("0.1"))

    if not is_short:
        candle_source = recent_candles_1h if recent_candles_1h else (candidate.get("candles") or ())
        low_48h = min(
            (_to_dec(c.get("low")) for c in candle_source[-48:] if isinstance(c, Mapping) and _to_dec(c.get("low")) is not None),
            default=current_price,
        )
        high_48h = max(
            (_to_dec(c.get("high")) for c in candle_source[-48:] if isinstance(c, Mapping) and _to_dec(c.get("high")) is not None),
            default=current_price,
        )
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
            and low_48h is not None
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
            and (current_price >= trigger or (high_48h is not None and high_48h >= trigger and current_price >= (entry or trigger) * Decimal("0.99")))
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
            )

        if at_entry_shelf:
            return RadarLifecycleDecision(
                retained=True,
                state=RadarLifecycleState.AT_ENTRY_ZONE,
                label="🟢 AT ENTRY ZONE",
                reason="testing_limit_entry_shelf",
                current_price=current_price,
                pnl_from_entry_pct=pnl_pct,
            )

    # 4. Expiration Check (Only expire if not in an active triggered trade and older than max_age_hours)
    if evaluated_at is not None:
        age_hours = (ref_now - evaluated_at).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            return RadarLifecycleDecision(
                retained=False,
                state=RadarLifecycleState.EXPIRED,
                label="⌛ EXPIRED",
                reason="max_retention_age_exceeded",
                current_price=current_price,
            )

    return RadarLifecycleDecision(
        retained=True,
        state=RadarLifecycleState.ACTIVE_COIL,
        label="⏳ ACTIVE COIL",
        reason="holding_structure_above_stop",
        current_price=current_price,
        pnl_from_entry_pct=pnl_pct,
    )
