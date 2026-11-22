"""Frozen research episodes and closed-fifteen-minute recovery confirmation."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from algotrader.domain.crypto_screener_models import CryptoOpportunity, SetupLabel
from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument, Quote
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION, recovery_flow_confirmed

EPISODE_POLICY = "recovery-episodes-v1-15m"
INTERVAL = timedelta(minutes=15)
Status = Literal["waiting", "triggered", "invalidated", "target_hit", "expired", "indeterminate"]
TERMINAL = frozenset({"invalidated", "target_hit", "expired", "indeterminate"})


def require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("episode timestamps must be timezone-aware")


def boundary(at: datetime) -> datetime:
    require_aware(at)
    return datetime.fromtimestamp(int(at.timestamp()) // 900 * 900, UTC)


@dataclass(frozen=True)
class RecoveryEpisode:
    episode_id: str
    instrument: Instrument
    source_at: datetime
    first_seen: datetime
    qualified_at: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    checked_at: datetime
    last_bar_open: datetime
    status: Status = "waiting"
    reason: str = "qualified_hourly_waiting_15m"
    triggered_at: datetime | None = None
    confirmation_bar: datetime | None = None
    trigger_price: Decimal | None = None
    last_price: Decimal | None = None
    last_price_at: datetime | None = None
    ended_at: datetime | None = None
    coverage_complete: bool = True
    initial_interval_verified: bool = True
    peak: Decimal | None = None
    trough: Decimal | None = None
    revision: int = 0
    policy: str = EPISODE_POLICY

    def __post_init__(self) -> None:
        for at in (self.source_at, self.first_seen, self.qualified_at, self.checked_at,
                   self.last_bar_open, self.triggered_at, self.confirmation_bar, self.ended_at,
                   self.last_price_at):
            if at is not None:
                require_aware(at)
        optional_prices = (self.trigger_price, self.last_price, self.peak, self.trough)
        if (self.policy != EPISODE_POLICY or self.status not in TERMINAL | {"waiting", "triggered"}
                or any(not p.is_finite() or p <= 0 for p in (self.stop, self.entry, self.target))
                or any(p is not None and (not p.is_finite() or p <= 0) for p in optional_prices)
                or not self.stop < self.entry < self.target
                or not self.source_at <= self.first_seen <= self.checked_at
                or boundary(self.last_bar_open) != self.last_bar_open
                or self.qualified_at != self.first_seen or self.revision < 0
                or (self.status == "triggered" and self.triggered_at is None)
                or (self.status == "waiting" and self.triggered_at is not None)
                or (self.triggered_at is not None and not self.first_seen <= self.triggered_at <= self.checked_at)):
            raise ValueError("invalid recovery episode")

    @classmethod
    def enroll(cls, opportunity: CryptoOpportunity, at: datetime) -> RecoveryEpisode:
        require_aware(at)
        require_aware(opportunity.evaluated_at)
        if (opportunity.setup_label is not SetupLabel.RECOVERY_RECLAIM_LONG
                or opportunity.research_policy_version != RECOVERY_POLICY_VERSION
                or len(opportunity.checklist) != 5
                or not all(passed is True for _, passed, _ in opportunity.checklist)
                or not 0 <= (at - opportunity.evaluated_at).total_seconds() <= 600):
            raise ValueError("enrollment requires a fresh five-gate recovery candidate")
        source = opportunity.evaluated_at.astimezone(UTC)
        key = f"{EPISODE_POLICY}|{opportunity.instrument.id}|{source.isoformat()}"
        # The candle overlapping enrollment cannot establish post-enrollment extremes.
        watermark = boundary(at) - INTERVAL if boundary(at) == at else boundary(at)
        return cls(hashlib.sha256(key.encode()).hexdigest(), opportunity.instrument,
                   source, at, at, opportunity.suggested_entry, opportunity.suggested_stop,
                   opportunity.suggested_targets[-1], at, watermark,
                   initial_interval_verified=boundary(at) == at)


def valid_bars(bars: Sequence[Candle], at: datetime) -> tuple[Candle, ...]:
    for candle in bars:
        require_aware(candle.opened_at)
        require_aware(candle.closed_at)
    result = tuple(c for c in bars if c.closed is True and c.closed_at <= at)
    if len(result) > 100:
        raise ValueError("bounded recovery history exceeded")
    for candle in result:
        values = (candle.open, candle.high, candle.low, candle.close)
        require_aware(candle.opened_at)
        require_aware(candle.closed_at)
        if (boundary(candle.opened_at) != candle.opened_at
                or not 899 <= (candle.closed_at - candle.opened_at).total_seconds() <= 900
                or any(not p.is_finite() or p <= 0 for p in values)
                or not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high
                or not candle.quote_volume.is_finite() or candle.quote_volume < 0):
            raise ValueError("invalid closed fifteen-minute candle")
    if any(b.opened_at <= a.opened_at for a, b in zip(result, result[1:], strict=False)):
        raise ValueError("duplicate or unordered recovery candles")
    return result


def advance_episode(
    episode: RecoveryEpisode, *, at: datetime, bars: Sequence[Candle],
    quote: Quote | None, derivatives: DerivativesSnapshot | None,
) -> RecoveryEpisode:
    require_aware(at)
    if at < episode.checked_at:
        raise ValueError("out-of-order recovery observation")
    if at == episode.checked_at or episode.status in TERMINAL:
        return episode
    state = replace(episode, checked_at=at, revision=episode.revision + 1)
    horizon = timedelta(hours=48 if state.triggered_at else 24)
    deadline = state.first_seen + horizon
    evaluation_at = min(at, deadline)
    fresh_quote = (
        quote is not None
        and quote.instrument_id == episode.instrument.id
        and quote.observed_at.tzinfo is not None
        and quote.observed_at.utcoffset() is not None
        and at <= deadline
        and quote.is_valid_book
        and quote.is_fresh_at(at, max_age_seconds=120.0, max_future_seconds=5.0)
        and 0 <= quote.spread_bps <= 25
        and quote.quote_volume_24h >= 500000
    )
    if fresh_quote:
        assert quote is not None
        state = replace(state, last_price=quote.last, last_price_at=at)
        if state.triggered_at is not None:
            state = replace(state, peak=max(state.peak or state.entry, quote.last),
                            trough=min(state.trough or state.entry, quote.last))
    closed = valid_bars(bars, evaluation_at)
    if not state.initial_interval_verified:
        overlapping = [c for c in closed if c.opened_at < state.first_seen <= c.closed_at]
        if not overlapping:
            if at >= deadline:
                return replace(state, status="indeterminate", ended_at=deadline,
                               coverage_complete=False, reason="initial_interval_unobserved")
            return replace(state, coverage_complete=False, reason="initial_interval_unobserved")
        if overlapping[0].low <= state.stop:
            return replace(state, status="indeterminate", ended_at=at,
                           coverage_complete=False, reason="initial_intrabar_order_unknown")
        state = replace(state, initial_interval_verified=True)
    new = tuple(c for c in closed if c.opened_at > episode.last_bar_open)
    expected = episode.last_bar_open + INTERVAL
    complete = bool(new) and new[0].opened_at == expected and all(
        b.opened_at - a.opened_at == INTERVAL for a, b in zip(new, new[1:], strict=False))
    latest_expected = boundary(evaluation_at) - INTERVAL
    if (new and (not complete or new[-1].opened_at != latest_expected)
            or not new and latest_expected > episode.last_bar_open):
        if at - state.first_seen >= horizon:
            return replace(state, status="indeterminate", ended_at=deadline,
                           coverage_complete=False, reason="horizon_with_history_gap")
        return replace(state, coverage_complete=False, reason="history_gap")
    if new:
        state = replace(state, last_bar_open=new[-1].opened_at, coverage_complete=True)
    for candle in new:
        if candle.opened_at < state.first_seen:
            continue
        if state.triggered_at is None:
            if candle.low <= state.stop:
                return replace(state, status="invalidated", reason="preconfirmation_stop_breach", ended_at=at)
        else:
            stop_hit, target_hit = candle.low <= state.stop, candle.high >= state.target
            # An overlapping candle or both touches cannot prove event ordering.
            if (stop_hit or target_hit) and (candle.opened_at < state.triggered_at or (stop_hit and target_hit)):
                return replace(state, status="indeterminate", reason="intrabar_order_unknown", ended_at=at)
            if candle.opened_at >= state.triggered_at:
                state = replace(state, peak=max(state.peak or state.entry, candle.high),
                                trough=min(state.trough or state.entry, candle.low))
            if stop_hit or target_hit:
                return replace(state, status="invalidated" if stop_hit else "target_hit",
                               reason="closed_bar_stop" if stop_hit else "closed_bar_final_target", ended_at=at)
    if fresh_quote and quote is not None and quote.last <= state.stop:
        return replace(state, status="invalidated", reason="observed_stop_breach", ended_at=at)
    if state.triggered_at is not None:
        if fresh_quote:
            assert quote is not None
            if quote.last >= state.target:
                return replace(state, status="target_hit", reason="observed_final_target", ended_at=at)
        if at - state.first_seen >= timedelta(hours=48):
            return replace(state, status="expired", reason="research_horizon_complete", ended_at=deadline)
        return replace(state, reason="tracking_triggered_episode")
    if at - state.first_seen >= timedelta(hours=24):
        return replace(state, status="expired", reason="confirmation_window_expired", ended_at=deadline)
    if not new:
        return replace(state, reason="awaiting_closed_15m")
    latest = new[-1]
    baseline = closed[-5:-1]
    risk = state.entry - state.stop
    confirmation = (
        fresh_quote and recovery_flow_confirmed(derivatives, at)
        and 0 <= (at - latest.closed_at).total_seconds() <= 120
        and len(baseline) == 4
        and all(b.opened_at - a.opened_at == INTERVAL
                for a, b in zip(closed[-5:], closed[-4:], strict=False))
        and sum((c.quote_volume for c in baseline), Decimal("0")) > 0
        and latest.quote_volume >= sum((c.quote_volume for c in baseline), Decimal("0")) / 4
        and latest.close > latest.open
        and state.entry <= latest.close <= state.entry + risk * Decimal("0.5")
        and quote is not None and state.entry <= quote.last <= state.entry + risk * Decimal("0.5")
    )
    if confirmation:
        return replace(state, status="triggered", reason="closed_15m_confirmed",
                       triggered_at=at, confirmation_bar=latest.closed_at,
                       trigger_price=quote.last if quote else None,
                       peak=quote.last if quote else None, trough=quote.last if quote else None)
    return replace(state, reason="confirmation_unavailable_or_failed")
