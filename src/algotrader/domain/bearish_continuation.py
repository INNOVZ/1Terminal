"""Point-in-time, opt-in failed-support-retest research policy. No execution."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.continuation_inputs import reproducible_inputs
from algotrader.domain.execution_brackets import valid_execution_prices
from algotrader.domain.indicators import atr, ema
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction
from algotrader.domain.precision import ceil_to_increment, floor_to_increment

D = Decimal
POLICY_VERSION = "bearish-continuation-v3-temporal-ranked-research"
ZERO = D("0")
TITLES = (
    "Bearish Structure",
    "Failed Support Reclaim",
    "Downside Room / Anti-Chasing",
    "Selling Confirmation",
    "Derivatives / Squeeze Safety",
)


@dataclass(frozen=True)
class ContinuationPolicy:
    """Unvalidated research defaults; change requires a new version and fresh replay."""

    version: str = POLICY_VERSION
    pivot_width: int = 2
    break_lookback: int = 24
    retest_window: int = 12
    trigger_wait_hours: int = 4
    shelf_atr: Decimal = D("0.25")
    break_atr: Decimal = D("0.10")
    stop_atr: Decimal = D("0.20")
    max_entry_distance_atr: Decimal = D("0.50")
    min_net_rr: Decimal = D("1.50")
    friction_fraction: Decimal = D("0.002")
    seller_ratio: Decimal = D("0.95")


DEFAULT_POLICY = ContinuationPolicy()


@dataclass(frozen=True)
class ContinuationPlan:
    support: Decimal
    support_known_at: datetime
    break_at: datetime
    rejection_at: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    atr: Decimal
    net_rr: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            k: v.isoformat() if isinstance(v, datetime) else str(v) for k, v in vars(self).items()
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContinuationPlan:
        return cls(
            support=D(str(value["support"])),
            support_known_at=datetime.fromisoformat(str(value["support_known_at"])),
            break_at=datetime.fromisoformat(str(value["break_at"])),
            rejection_at=datetime.fromisoformat(str(value["rejection_at"])),
            entry=D(str(value["entry"])),
            stop=D(str(value["stop"])),
            target=D(str(value["target"])),
            atr=D(str(value["atr"])),
            net_rr=D(str(value["net_rr"])),
        )


@dataclass(frozen=True)
class ContinuationEvaluation:
    plan: ContinuationPlan | None
    gates: tuple[bool | None, ...]
    reason: str
    search_rejections: tuple[tuple[str, int], ...] = ()

    @property
    def structurally_ready(self) -> bool:
        return self.plan is not None and all(g is True for g in self.gates[:4])

    @property
    def confirmed(self) -> bool:
        return self.structurally_ready and self.gates[4] is True


def _bars(raw: Sequence[Candle], now: datetime, seconds: int) -> list[Candle]:
    bars = sorted((c for c in raw if c.closed and c.closed_at <= now), key=lambda c: c.closed_at)
    if not bars or not timedelta(0) <= now - bars[-1].closed_at <= timedelta(seconds=seconds + 300):
        return []
    for c in bars:
        if (
            c.opened_at.tzinfo is None
            or c.closed_at.tzinfo is None
            or not all(x.is_finite() for x in (c.open, c.high, c.low, c.close, c.volume))
            or not 0 < c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
            or c.volume < 0
            or abs((c.closed_at - c.opened_at).total_seconds() - seconds) > 2
        ):
            return []
    if any(
        abs((b.opened_at - a.opened_at).total_seconds() - seconds) > 2
        for a, b in zip(bars, bars[1:], strict=False)
    ):
        return []
    return bars


def _pivots(bars: Sequence[Candle], attr: str, width: int) -> list[tuple[Decimal, datetime]]:
    out = []
    for i in range(width, len(bars) - width):
        value = getattr(bars[i], attr)
        others = [getattr(bars[j], attr) for j in range(i - width, i + width + 1) if j != i]
        if value < min(others) if attr == "low" else value > max(others):
            out.append((value, bars[i + width].closed_at))
    return out


def _plan_checks(
    plan: ContinuationPlan,
    bars: Sequence[Candle],
    *,
    now: datetime,
    price: Decimal,
    spread: Decimal,
    policy: ContinuationPolicy,
    is_triggered: bool,
) -> tuple[bool, bool, bool, str]:
    """Identical acceptance rules during search and frozen-plan revalidation."""
    if (
        not all(
            v.is_finite() and v > 0
            for v in (
                plan.support,
                plan.entry,
                plan.stop,
                plan.target,
                plan.atr,
            )
        )
        or not plan.net_rr.is_finite()
    ):
        return False, False, False, "invalid_plan_prices"
    if (
        any(t.tzinfo is None for t in (plan.support_known_at, plan.break_at, plan.rejection_at))
        or not plan.support_known_at < plan.break_at < plan.rejection_at <= now
        or (
            not is_triggered
            and now - plan.rejection_at > timedelta(hours=policy.trigger_wait_hours)
        )
        or not valid_execution_prices(Direction.SHORT, plan.entry, plan.stop, (plan.target,))
    ):
        return False, False, False, "invalid_or_expired_plan"
    after = [c for c in bars if c.opened_at >= plan.rejection_at]
    sequence = (
        price < plan.stop
        and all(c.high < plan.stop for c in after)
        and all(c.close <= plan.support + policy.shelf_atr * plan.atr for c in after)
    )
    friction = plan.entry * (policy.friction_fraction + spread)
    net_rr = (
        plan.net_rr
        if is_triggered
        else ((plan.entry - plan.target - friction) / (plan.stop - plan.entry + friction))
    )
    room = (
        plan.target < price < plan.stop
        and net_rr >= policy.min_net_rr
        and plan.stop <= plan.entry * D("1.06")
        and (is_triggered or abs(price - plan.entry) <= policy.max_entry_distance_atr * plan.atr)
    )
    rejection = next((c for c in bars if c.closed_at == plan.rejection_at), None)
    prior = [c for c in bars if plan.break_at < c.closed_at < plan.rejection_at]
    # Reference only the original break/retest interval, never a moving latest bar.
    break_bar = next((c for c in bars if c.closed_at == plan.break_at), None)
    ref_volume = (
        sum((c.volume for c in prior), ZERO) / len(prior)
        if prior
        else break_bar.volume
        if break_bar is not None
        else ZERO
    )
    selling = (
        rejection is not None
        and rejection.close < rejection.open
        and ref_volume > 0
        and rejection.volume >= ref_volume
        and bars[-1].close <= rejection.close
    )
    reason = next(
        (f"C{i}_failed" for i, g in ((2, sequence), (3, room), (4, selling)) if not g), "confirmed"
    )
    return sequence, room, selling, reason


def evaluate_continuation(
    candles_1h: Sequence[Candle],
    candles_4h: Sequence[Candle],
    *,
    now: datetime,
    price: Decimal,
    tick: Decimal,
    derivatives: DerivativesSnapshot | None = None,
    spread_fraction: Decimal = ZERO,
    policy: ContinuationPolicy = DEFAULT_POLICY,
    existing_plan: ContinuationPlan | None = None,
    is_triggered: bool = False,
) -> ContinuationEvaluation:
    """Unknown Stage-2 telemetry never becomes a positive publication gate."""

    def fail(reason: str) -> ContinuationEvaluation:
        return ContinuationEvaluation(None, (False, False, False, False, None), reason)

    if is_triggered and existing_plan is None:
        return fail("trigger_requires_frozen_plan")
    if now.tzinfo is None:
        return fail("naive_evaluation_time")
    if (
        not all(v.is_finite() for v in (price, tick, spread_fraction))
        or price <= 0
        or tick <= 0
        or spread_fraction < 0
        or spread_fraction > D("0.005")
    ):
        return fail("invalid_price_inputs")
    # Validate timestamps before comparing them with the aware evaluation anchor.
    if any(
        c.opened_at.tzinfo is None or c.closed_at.tzinfo is None for c in (*candles_1h, *candles_4h)
    ):
        return fail("naive_candle_time")
    h = _bars(candles_1h, now, 3600)
    q = _bars(candles_4h, now, 14400)
    if len(h) < 72 or len(q) < 60:
        return fail("closed_history_unavailable")
    a = atr(q, 14)[-1]
    if not a.is_finite() or a <= 0:
        return fail("atr_unavailable")
    width = policy.pivot_width
    lows, highs = _pivots(q, "low", width), _pivots(q, "high", width)
    means = ema([c.close for c in q], 50)
    trend = (
        len(lows) >= 2
        and len(highs) >= 2
        and lows[-1][0] < lows[-2][0]
        and highs[-1][0] < highs[-2][0]
        and means[-1] < means[-4]
        and price < means[-1]
    )
    if not trend:
        return fail("bearish_structure_absent")

    plan = existing_plan
    search_rejections: Counter[str] = Counter()
    if plan is None:
        # Newest valid sequence wins. Every shelf and target predates the break.
        for b in range(len(h) - 2, max(24, len(h) - policy.break_lookback) - 1, -1):
            known = _pivots(h[max(0, b - 120) : b], "low", width)
            shelves = sorted(
                {
                    v
                    for v, _ in known
                    if h[b - 1].close >= v and h[b].close < v - policy.break_atr * a
                },
                reverse=True,
            )
            for shelf in shelves:
                touches = [(v, t) for v, t in known if abs(v - shelf) <= policy.shelf_atr * a]
                if len(touches) < 2:
                    continue
                for r in range(min(len(h) - 1, b + policy.retest_window), b, -1):
                    bar = h[r]
                    if not (
                        shelf - policy.shelf_atr * a <= bar.high <= shelf + a
                        and bar.close < shelf - policy.break_atr * a
                        and bar.close < bar.open
                    ):
                        continue
                    if now - bar.closed_at > timedelta(hours=policy.trigger_wait_hours):
                        search_rejections["expired_rejection"] += 1
                        continue
                    # A reclaim between the break and rejection invalidates this sequence.
                    if any(c.close > shelf + policy.shelf_atr * a for c in h[b : r + 1]):
                        continue
                    entry = floor_to_increment(bar.low - policy.break_atr * a, tick)
                    stop = ceil_to_increment(max(shelf, bar.high) + policy.stop_atr * a, tick)
                    supports = [v for v, t in (*known, *lows) if t < h[b].closed_at and v <= entry]
                    if not supports:
                        continue
                    target = ceil_to_increment(max(supports) + policy.break_atr * a, tick)
                    if not valid_execution_prices(Direction.SHORT, entry, stop, (target,)):
                        continue
                    friction = entry * (policy.friction_fraction + spread_fraction)
                    rr = (entry - target - friction) / (stop - entry + friction)
                    candidate = ContinuationPlan(
                        shelf,
                        max(t for _, t in touches),
                        h[b].closed_at,
                        bar.closed_at,
                        entry,
                        stop,
                        target,
                        a,
                        rr,
                    )
                    sequence, room, selling, reason = _plan_checks(
                        candidate,
                        h,
                        now=now,
                        price=price,
                        spread=spread_fraction,
                        policy=policy,
                        is_triggered=False,
                    )
                    # No historical trigger can become a fresh pending opportunity.
                    after = [c for c in h if c.opened_at >= candidate.rejection_at]
                    if price <= candidate.entry or any(c.low <= candidate.entry for c in after):
                        search_rejections["already_triggered"] += 1
                        continue
                    if not all((sequence, room, selling)):
                        search_rejections[reason] += 1
                        continue
                    plan = candidate
                    break
                if plan is not None:
                    break
            if plan is not None:
                break
    if plan is None:
        return ContinuationEvaluation(
            None,
            (True, False, False, False, None),
            "ordered_retest_or_target_absent",
            tuple(sorted(search_rejections.items())),
        )
    sequence, room, selling, plan_reason = _plan_checks(
        plan,
        h,
        now=now,
        price=price,
        spread=spread_fraction,
        policy=policy,
        is_triggered=is_triggered,
    )
    if plan_reason in ("invalid_plan_prices", "invalid_or_expired_plan"):
        return fail(plan_reason)
    if not is_triggered:
        after = [c for c in h if c.opened_at >= plan.rejection_at]
        sequence = sequence and price > plan.entry and all(c.low > plan.entry for c in after)
    flow: bool | None = None
    if derivatives is not None:
        ci = derivatives.continuation_inputs or {}
        window = now.replace(minute=0, second=0, microsecond=0)
        fields = tuple(
            ci.get(k)
            for k in (
                "oi_change_1h",
                "oi_change_4h",
                "oi_change_7d",
                "taker_ratio_1h",
                "taker_ratio_4h",
                "funding_rate",
            )
        )
        timestamps = tuple(ci.get(k) for k in ("observed_at", "funding_observed_at"))
        if (
            not derivatives.degraded_telemetry
            and ci.get("version") == "continuation-inputs-v1"
            and ci.get("oi_window_end") == window
            and ci.get("taker_window_end") == window
            and derivatives.observed_at.tzinfo is not None
            and timedelta(seconds=-5) <= now - derivatives.observed_at <= timedelta(minutes=5)
            and all(
                isinstance(t, datetime)
                and t.tzinfo is not None
                and timedelta(seconds=-5) <= now - t <= timedelta(minutes=5)
                for t in timestamps
            )
            and all(isinstance(v, Decimal) and v.is_finite() for v in fields)
            and reproducible_inputs(ci)
        ):
            oi1, oi4, _, taker1, taker4, funding = (v for v in fields if isinstance(v, Decimal))
            flow = (
                oi1 >= ZERO
                and oi4 >= ZERO
                and ZERO < taker1 <= policy.seller_ratio
                and ZERO < taker4 <= D("1.0")
                and funding >= ZERO
            )
    gates = (bool(trend), bool(sequence), bool(room), bool(selling), flow)
    reason = next(
        (f"C{i + 1}_failed" for i, g in enumerate(gates) if g is False),
        "derivatives_unavailable" if flow is None else "confirmed",
    )
    return ContinuationEvaluation(plan, gates, reason, tuple(sorted(search_rejections.items())))
