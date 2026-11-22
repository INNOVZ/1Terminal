"""Explicit minute-bar setup simulation; never fills or calibrated probabilities."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.forward_outcomes import evaluate_forward_path, outcome_anchor
from algotrader.domain.models import Candle, Direction

MEASUREMENT_VERSION = "setup-minute-barriers-v1"
TRIGGER_MODES = {"CEILING_BREAKOUT", "BREAKOUT_TRIGGER", "BREAKDOWN_TRIGGER",
                 "BREAKOUT_EXPANSION", "BREAKDOWN_EXPANSION"}
RETEST_MODES = {"RETEST_ACCUMULATION", "WAIT_FOR_RETEST_ONLY"}


def aware(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return result.astimezone(UTC)


def number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("finite number required")
    return result


@dataclass(frozen=True)
class OutcomePolicy:
    horizon_hours: int = 48
    entry_wait_hours: int = 24
    round_trip_cost_bps: Decimal = Decimal("20")
    version: str = MEASUREMENT_VERSION

    def __post_init__(self) -> None:
        if (self.version != MEASUREMENT_VERSION or self.horizon_hours not in (24, 48)
                or not 1 <= self.entry_wait_hours <= self.horizon_hours
                or not self.round_trip_cost_bps.is_finite()
                or not 0 <= self.round_trip_cost_bps <= 1000):
            raise ValueError("invalid outcome policy")

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "round_trip_cost_bps": str(self.round_trip_cost_bps),
                "entry_start": "next_complete_UTC_minute_after_first_visible_audit",
                "fill_assumption": "barrier simulation at planned entry; gaps at entry ambiguous",
                "exit": "original_T1_before_original_stop; unresolved retained separately"}


def freeze_plan(candidate: Mapping[str, Any], visible_at: datetime) -> dict[str, Any]:
    """Only a fresh, positively verified five-gate candidate is eligible for enrollment."""
    visible = aware(visible_at)
    origin = aware(candidate.get("first_seen_at") or candidate["evaluated_at"])
    if origin > visible:
        raise ValueError("discovery after first visibility")
    checklist = candidate.get("checklist")
    if not isinstance(checklist, list) or len(checklist) != 5 or any(
        not isinstance(item, dict) or item.get("passed") is not True
        or item.get("status", "passed") != "passed" for item in checklist
    ):
        raise ValueError("five verified selection gates required")
    direction = Direction(candidate["direction"])
    entry, stop = number(candidate["suggested_entry"]), number(candidate["suggested_stop"])
    targets = tuple(number(t) for t in candidate["suggested_targets"])
    if not targets or min(entry, stop, *targets) <= 0:
        raise ValueError("positive ordered bracket required")
    ordered = (stop, entry, *targets) if direction is Direction.LONG else (*reversed(targets), entry, stop)
    if any(a >= b for a, b in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("positive ordered bracket required")
    mode = str(candidate["execution_mode"])
    if mode not in TRIGGER_MODES | RETEST_MODES:
        raise ValueError("unsupported execution mode")
    symbol, setup = str(candidate["symbol"]), str(candidate["setup_label"])
    if not symbol or not setup or len(symbol) > 64 or len(setup) > 128:
        raise ValueError("invalid symbol/setup")
    deadline = None
    if setup == "BREAKDOWN_RETEST_SHORT":
        evidence = candidate["continuation_evidence"]
        deadline = (aware(evidence["plan"]["rejection_at"]) + timedelta(hours=4)).isoformat()
    return {"symbol": symbol, "direction": direction.value, "setup_label": setup,
            "origin_at": origin.isoformat(), "first_visible_at": visible.isoformat(),
            "score": str(number(candidate["score"])), "execution_mode": mode,
            "entry": str(entry), "stop": str(stop), "targets": [str(t) for t in targets],
            "strategy_entry_deadline": deadline}


def evaluate_setup(plan: Mapping[str, Any], candles: Sequence[Candle], *, now: datetime,
                   policy: OutcomePolicy) -> dict[str, Any]:
    start = outcome_anchor(aware(plan["first_visible_at"]))
    end = start + timedelta(hours=policy.horizon_hours)
    now = aware(now)
    direction = Direction(plan["direction"])
    check = evaluate_forward_path(anchor=start, horizon_hours=policy.horizon_hours,
                                  candles=candles, now=now, direction=direction)
    base: dict[str, Any] = {"measurement_version": policy.version, "anchor": start.isoformat(),
                           "horizon_end": end.isoformat(), "is_executed_trade": False,
                           "probability_calibration_permitted": False}
    if check.status != "complete":
        return {**base, "status": check.status, "reason": check.reason, "net_r": None}
    bars = sorted((c for c in candles if c.closed and start <= c.opened_at < end
                   and c.closed_at <= now), key=lambda c: c.opened_at)
    sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    entry, stop, target = number(plan["entry"]), number(plan["stop"]), number(plan["targets"][0])
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("positive risk required")
    wait_end = start + timedelta(hours=policy.entry_wait_hours)
    if plan.get("strategy_entry_deadline"):
        wait_end = min(wait_end, aware(plan["strategy_entry_deadline"]))
    active = False
    triggered_at = None

    def finish(status: str, reason: str, bar: Candle | None = None,
               price: Decimal | None = None) -> dict[str, Any]:
        net_r = ((price - entry) * sign - entry * policy.round_trip_cost_bps / 10000) / risk if price is not None else None
        return {**base, "status": status, "reason": reason, "triggered_at": triggered_at,
                "event_bar_open": bar.opened_at.isoformat() if bar else None,
                "net_r": str(net_r) if net_r is not None else None,
                "cost_assumption_bps": str(policy.round_trip_cost_bps),
                "intrabar_order_measured": False}

    for bar in bars:
        stopped = bar.low <= stop if sign > 0 else bar.high >= stop
        targeted = bar.high >= target if sign > 0 else bar.low <= target
        if not active:
            if bar.opened_at >= wait_end:
                return finish("untriggered_expired", "entry_wait_elapsed")
            trigger_mode = plan["execution_mode"] in TRIGGER_MODES
            touched = ((bar.high >= entry if sign > 0 else bar.low <= entry) if trigger_mode
                       else (bar.low <= entry if sign > 0 else bar.high >= entry))
            if not touched:
                if stopped:
                    return finish("invalidated_before_entry", "stop_touched_before_entry", bar)
                continue
            if bar.opened_at < wait_end < bar.closed_at:
                return finish("ambiguous", "entry_bar_straddles_strategy_deadline", bar)
            # A marketable opening gap cannot be called a fill at the planned price.
            beyond_entry = ((bar.open > entry if sign > 0 else bar.open < entry) if trigger_mode
                            else (bar.open < entry if sign > 0 else bar.open > entry))
            if beyond_entry:
                return finish("ambiguous", "opening_price_already_beyond_entry", bar)
            if stopped:
                return finish("ambiguous", "entry_and_stop_in_same_bar", bar)
            if targeted and not trigger_mode and bar.open != entry:
                return finish("ambiguous", "retest_entry_and_target_order_unknown", bar)
            active, triggered_at = True, bar.opened_at.isoformat()
        if stopped and targeted:
            return finish("ambiguous", "target_and_stop_in_same_bar", bar)
        if stopped:
            # A stop gap receives the adverse opening price, never a better planned stop.
            exit_price = min(stop, bar.open) if sign > 0 else max(stop, bar.open)
            return finish("stop_before_target", "original_stop_touched", bar, exit_price)
        if targeted:
            return finish("target_before_stop", "original_T1_touched", bar, target)
    return finish("triggered_unresolved" if active else "untriggered_expired",
                  "horizon_finished_without_exit" if active else "entry_not_touched")
