"""Domain policy and state transitions for tracking rolling top gainers into confirmed shorts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.models import Direction, Instrument, MarketKind, Quote, Venue

ZERO = Decimal("0")


class TopGainerWatchState(StrEnum):
    WATCHING = "watching"
    CONFIRMED_SHORT = "confirmed_short"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass(frozen=True)
class TopGainerWatchPolicy:
    version: str = "top-gainer-short-watch-v2-current-quality"
    top_n_gainers: int = 20
    min_24h_gain_pct: Decimal = Decimal("8.0")
    min_quote_volume_24h: Decimal = Decimal("500000")
    max_spread_bps: Decimal = Decimal("50")
    max_active_watches: int = 30
    max_watch_hours: int = 72
    confirmed_min_conviction: Decimal = Decimal("50.0")
    confirmed_checklist_required: int = 5
    squeeze_stop_cap_pct: Decimal = Decimal("0.06")
    bottom_collapse_cycles_required: int = 2

    def __post_init__(self) -> None:
        if self.top_n_gainers < 1 or self.max_active_watches < self.top_n_gainers:
            raise ValueError("invalid top-gainer short watch capacities")
        if self.max_watch_hours < 1:
            raise ValueError("max_watch_hours must be positive")


@dataclass(frozen=True)
class TopGainerWatchEpisode:
    episode_id: str
    symbol: str
    base_asset: str
    state: TopGainerWatchState
    enrolled_at: datetime
    updated_at: datetime
    anchor_price: Decimal
    anchor_24h_pct: Decimal
    peak_price: Decimal
    peak_24h_pct: Decimal
    latest_price: Decimal
    latest_24h_pct: Decimal
    current_archetype: str | None = None
    gates_passed: bool = False
    checklist_passed: int = 0
    failed_checklist_items: tuple[str, ...] = ()
    latest_blocker: str | None = None
    latest_score: Decimal | None = None
    latest_conviction: Decimal | None = None
    pos_in_range_30d: Decimal | None = None
    watch_tier: str = "watching"
    confirmed_at: datetime | None = None
    confirmed_opportunity: dict[str, Any] | None = None
    invalidation_reason: str | None = None
    collapse_streak: int = 0
    evaluation_count: int = 1
    policy_version: str = "top-gainer-short-watch-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "symbol": self.symbol,
            "base_asset": self.base_asset,
            "state": self.state.value,
            "enrolled_at": self.enrolled_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "anchor_price": float(self.anchor_price),
            "anchor_24h_pct": float(self.anchor_24h_pct),
            "peak_price": float(self.peak_price),
            "peak_24h_pct": float(self.peak_24h_pct),
            "latest_price": float(self.latest_price),
            "latest_24h_pct": float(self.latest_24h_pct),
            "drawdown_from_peak_pct": (
                round(
                    float((self.latest_price - self.peak_price) / self.peak_price * Decimal("100")),
                    2,
                )
                if self.peak_price > 0
                else 0.0
            ),
            "current_archetype": self.current_archetype,
            "gates_passed": self.gates_passed,
            "checklist_passed": self.checklist_passed,
            "failed_checklist_items": list(self.failed_checklist_items),
            "latest_blocker": self.latest_blocker,
            "latest_score": float(self.latest_score) if self.latest_score is not None else None,
            "latest_conviction": float(self.latest_conviction)
            if self.latest_conviction is not None
            else None,
            "pos_in_range_30d": float(self.pos_in_range_30d)
            if self.pos_in_range_30d is not None
            else None,
            "watch_tier": self.watch_tier,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "confirmed_opportunity": self.confirmed_opportunity,
            "invalidation_reason": self.invalidation_reason,
            "collapse_streak": self.collapse_streak,
            "evaluation_count": self.evaluation_count,
            "policy_version": self.policy_version,
        }


def _deduplicated_liquid_usdm(
    instruments: Sequence[Instrument],
    quotes: Mapping[str, Quote],
    policy: TopGainerWatchPolicy,
) -> dict[str, Instrument]:
    best_by_base: dict[str, tuple[Decimal, int, Instrument]] = {}
    for inst in instruments:
        if (
            not inst.trading
            or inst.market_kind != MarketKind.FUTURES
            or inst.id.venue != Venue.BINANCE_USDM
            or inst.quote_asset not in ("USDT", "USDC")
        ):
            continue
        q = quotes.get(inst.id.symbol)
        if q is None or not q.is_valid_book:
            continue
        if q.quote_volume_24h < policy.min_quote_volume_24h or q.spread_bps > policy.max_spread_bps:
            continue
        pref = 1 if inst.quote_asset == "USDT" else 0
        existing = best_by_base.get(inst.base_asset)
        if existing is None or (q.quote_volume_24h, pref) > (existing[0], existing[1]):
            best_by_base[inst.base_asset] = (q.quote_volume_24h, pref, inst)
    return {item[2].id.symbol: item[2] for item in best_by_base.values()}


def select_top_gainer_instruments(
    instruments: Sequence[Instrument],
    quotes: Mapping[str, Quote],
    policy: TopGainerWatchPolicy,
    *,
    pinned_symbols: Sequence[str] = (),
) -> tuple[Instrument, ...]:
    """Select active watched gainers plus current top-N 24h gainers in Stage 0."""
    eligible_by_symbol = _deduplicated_liquid_usdm(instruments, quotes, policy)
    selected: list[Instrument] = []
    seen_symbols: set[str] = set()
    seen_bases: set[str] = set()

    for sym in pinned_symbols:
        inst = eligible_by_symbol.get(sym)
        if inst is None or inst.id.symbol in seen_symbols or inst.base_asset in seen_bases:
            continue
        selected.append(inst)
        seen_symbols.add(inst.id.symbol)
        seen_bases.add(inst.base_asset)
        if len(selected) >= policy.max_active_watches:
            return tuple(selected)

    ranked_gainers: list[tuple[Decimal, Decimal, str, Instrument]] = []
    for sym, inst in eligible_by_symbol.items():
        if sym in seen_symbols or inst.base_asset in seen_bases:
            continue
        q = quotes[sym]
        ret_24h = q.price_change_pct_24h
        if ret_24h is None or not ret_24h.is_finite() or ret_24h < policy.min_24h_gain_pct:
            continue
        ranked_gainers.append((ret_24h, q.quote_volume_24h, sym, inst))

    ranked_gainers.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    new_slots = min(policy.top_n_gainers, policy.max_active_watches - len(selected))
    for _, _, _, inst in ranked_gainers[: max(0, new_slots)]:
        selected.append(inst)
    return tuple(selected)


def extract_short_diagnostic(
    symbol: str,
    journal_events: Sequence[dict[str, Any]],
    opportunity: CryptoOpportunity | None,
) -> dict[str, Any]:
    """Summarize the latest short screening outcome and exact blocker for a symbol."""
    sym_events = [e for e in journal_events if e.get("symbol") == symbol]
    cand_events = [e for e in sym_events if e.get("stage") == "distribution_candidate"]
    gate_events = [e for e in sym_events if e.get("stage") == "distribution_gates"]
    rej_events = [e for e in sym_events if e.get("stage") == "distribution_rejection"]

    if opportunity is not None:
        failed_items = tuple(title for title, passed, _ in opportunity.checklist if not passed)
        passed_count = sum(1 for _, passed, _ in opportunity.checklist if passed)
        eff_score = (
            opportunity.conviction if opportunity.conviction is not None else opportunity.score
        )
        blocker = None
        if passed_count < 5:
            blocker = f"checklist_{passed_count}_of_5:" + ",".join(failed_items)
        elif eff_score < Decimal("50.0"):
            blocker = "conviction_below_50"
        return {
            "archetype": opportunity.setup_label.value,
            "gates_passed": True,
            "checklist_passed": passed_count,
            "failed_checklist_items": failed_items,
            "blocker": blocker,
            "score": opportunity.score,
            "conviction": eff_score,
            "pos_in_range_30d": (cand_events[-1].get("pos_in_range_30d") if cand_events else None),
        }

    if cand_events:
        last = cand_events[-1]
        failed = tuple(str(x) for x in (last.get("failed_checklist_items") or ()))
        cp = int(last.get("checklist_passed") or 0)
        return {
            "archetype": last.get("archetype"),
            "gates_passed": True,
            "checklist_passed": cp,
            "failed_checklist_items": failed,
            "blocker": (
                f"checklist_{cp}_of_5:" + ",".join(failed) if cp < 5 else "filtered_post_evaluation"
            ),
            "score": last.get("score"),
            "conviction": last.get("conviction"),
            "pos_in_range_30d": last.get("pos_in_range_30d"),
        }

    if rej_events:
        last = rej_events[-1]
        reason = str(last.get("reason") or "rejected")
        if reason == "short_gates_failed" and last.get("failed_gates"):
            reason = "short_gates_failed:" + ",".join(str(x) for x in last["failed_gates"])
        return {
            "archetype": last.get("archetype"),
            "gates_passed": bool(gate_events),
            "checklist_passed": 0,
            "failed_checklist_items": (),
            "blocker": reason,
            "score": None,
            "conviction": None,
            "pos_in_range_30d": last.get("pos_in_range_30d"),
        }

    return {
        "archetype": None,
        "gates_passed": False,
        "checklist_passed": 0,
        "failed_checklist_items": (),
        "blocker": "not_evaluated",
        "score": None,
        "conviction": None,
        "pos_in_range_30d": None,
    }


def _classify_watch_tier(
    state: TopGainerWatchState,
    pos_30d: Decimal | None,
    archetype: str | None,
    ret_24h: Decimal,
    policy: TopGainerWatchPolicy,
) -> str:
    if state == TopGainerWatchState.CONFIRMED_SHORT:
        return "confirmed_short"
    if archetype is not None or (pos_30d is not None and pos_30d >= Decimal("0.45")):
        return "upper_range_watch"
    if pos_30d is not None and pos_30d < Decimal("0.45") and ret_24h >= policy.min_24h_gain_pct:
        return "lower_range_bounce_watch"
    return "cooling_watch"


def advance_top_gainer_watch_episode(
    previous: TopGainerWatchEpisode | None,
    *,
    symbol: str,
    base_asset: str,
    quote: Quote,
    diagnostic: Mapping[str, Any],
    opportunity: CryptoOpportunity | None,
    evaluated_at: datetime,
    policy: TopGainerWatchPolicy,
) -> TopGainerWatchEpisode:
    """Advance or enroll one Top-Gainer Short Watch episode."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    now = evaluated_at.astimezone(UTC)
    price = quote.last
    if not price.is_finite() or price <= ZERO:
        raise ValueError("quote price must be finite and positive")
    ret_24h = quote.price_change_pct_24h if quote.price_change_pct_24h is not None else ZERO

    archetype = str(diagnostic["archetype"]) if diagnostic.get("archetype") else None
    gates_passed = bool(diagnostic.get("gates_passed", False))
    checklist_passed = int(diagnostic.get("checklist_passed") or 0)
    failed_items = tuple(str(x) for x in (diagnostic.get("failed_checklist_items") or ()))
    blocker = str(diagnostic["blocker"]) if diagnostic.get("blocker") else None
    raw_score = diagnostic.get("score")
    raw_conv = diagnostic.get("conviction")
    raw_pos = diagnostic.get("pos_in_range_30d")
    latest_score = Decimal(str(raw_score)) if raw_score is not None else None
    latest_conviction = Decimal(str(raw_conv)) if raw_conv is not None else None
    pos_30d = Decimal(str(raw_pos)) if raw_pos is not None else None

    is_confirmed_now = (
        opportunity is not None
        and opportunity.direction is Direction.SHORT
        and gates_passed
        and blocker is None
        and checklist_passed >= policy.confirmed_checklist_required
        and (opportunity.conviction if opportunity.conviction is not None else opportunity.score)
        >= policy.confirmed_min_conviction
    )

    if previous is None:
        state = (
            TopGainerWatchState.CONFIRMED_SHORT
            if is_confirmed_now
            else TopGainerWatchState.WATCHING
        )
        return TopGainerWatchEpisode(
            episode_id=str(uuid4()),
            symbol=symbol,
            base_asset=base_asset,
            state=state,
            enrolled_at=now,
            updated_at=now,
            anchor_price=price,
            anchor_24h_pct=ret_24h,
            peak_price=price,
            peak_24h_pct=ret_24h,
            latest_price=price,
            latest_24h_pct=ret_24h,
            current_archetype=archetype,
            gates_passed=gates_passed,
            checklist_passed=checklist_passed,
            failed_checklist_items=failed_items,
            latest_blocker=None if is_confirmed_now else blocker,
            latest_score=latest_score,
            latest_conviction=latest_conviction,
            pos_in_range_30d=pos_30d,
            watch_tier=_classify_watch_tier(state, pos_30d, archetype, ret_24h, policy),
            confirmed_at=now if is_confirmed_now else None,
            confirmed_opportunity=opportunity.as_dict()
            if is_confirmed_now and opportunity is not None
            else None,
            policy_version=policy.version,
        )

    if previous.state in (TopGainerWatchState.INVALIDATED, TopGainerWatchState.EXPIRED):
        return previous

    base_update = replace(
        previous,
        updated_at=now,
        peak_price=max(previous.peak_price, price),
        peak_24h_pct=max(previous.peak_24h_pct, ret_24h),
        latest_price=price,
        latest_24h_pct=ret_24h,
        current_archetype=archetype,
        gates_passed=gates_passed,
        checklist_passed=checklist_passed,
        failed_checklist_items=failed_items,
        latest_blocker=blocker,
        latest_score=latest_score,
        latest_conviction=latest_conviction,
        pos_in_range_30d=pos_30d,
        evaluation_count=previous.evaluation_count + 1,
        policy_version=policy.version,
    )

    if previous.confirmed_opportunity is not None:
        conf = previous.confirmed_opportunity or {}
        entry_val = conf.get("suggested_entry") or float(previous.latest_price)
        stop_val = conf.get("suggested_stop")
        squeeze_cap = Decimal(str(entry_val)) * (Decimal("1") + policy.squeeze_stop_cap_pct)
        effective_stop = (
            min(Decimal(str(stop_val)), squeeze_cap) if stop_val is not None else squeeze_cap
        )
        if price >= effective_stop:
            return replace(
                base_update,
                state=TopGainerWatchState.INVALIDATED,
                watch_tier="invalidated",
                invalidation_reason="confirmed_short_squeeze_stop_breached",
                collapse_streak=0,
            )
        if (now - previous.enrolled_at) >= timedelta(hours=policy.max_watch_hours):
            return replace(
                base_update,
                state=TopGainerWatchState.EXPIRED,
                watch_tier="expired",
                invalidation_reason="watch_horizon_exceeded",
                collapse_streak=0,
            )
        # Keep the original thesis bracket for stop monitoring; current qualification
        # determines publication, never the existence of an old confirmation.
        return replace(
            base_update,
            state=TopGainerWatchState.CONFIRMED_SHORT
            if is_confirmed_now
            else TopGainerWatchState.WATCHING,
            watch_tier="confirmed_short" if is_confirmed_now else "reverification_required",
            latest_blocker=None if is_confirmed_now else (blocker or "current_quality_unverified"),
            invalidation_reason=None,
            collapse_streak=0,
        )

    if (now - previous.enrolled_at) >= timedelta(hours=policy.max_watch_hours):
        return replace(
            base_update,
            state=TopGainerWatchState.EXPIRED,
            watch_tier="expired",
            invalidation_reason="watch_horizon_exceeded",
        )

    if is_confirmed_now:
        return replace(
            base_update,
            state=TopGainerWatchState.CONFIRMED_SHORT,
            watch_tier="confirmed_short",
            gates_passed=True,
            latest_blocker=None,
            confirmed_at=now,
            confirmed_opportunity=opportunity.as_dict() if opportunity is not None else None,
            collapse_streak=0,
        )

    collapsed_low = (
        blocker == "no_short_archetype"
        and pos_30d is not None
        and pos_30d < Decimal("0.45")
        and ret_24h < ZERO
    )
    collapse_streak = previous.collapse_streak + 1 if collapsed_low else 0
    if collapse_streak >= policy.bottom_collapse_cycles_required:
        next_state = TopGainerWatchState.INVALIDATED
        inv_reason: str | None = "collapsed_below_upper_range"
    elif (now - previous.enrolled_at) >= timedelta(hours=policy.max_watch_hours):
        next_state = TopGainerWatchState.EXPIRED
        inv_reason = "watch_horizon_exceeded"
    else:
        next_state = TopGainerWatchState.WATCHING
        inv_reason = None

    return replace(
        base_update,
        state=next_state,
        watch_tier=_classify_watch_tier(next_state, pos_30d, archetype, ret_24h, policy),
        confirmed_at=None,
        confirmed_opportunity=None,
        invalidation_reason=inv_reason,
        collapse_streak=collapse_streak,
    )
