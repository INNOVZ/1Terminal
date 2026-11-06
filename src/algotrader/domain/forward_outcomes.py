"""Fixed-horizon price observations, explicitly not fills, trades or profitability."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.models import Candle, Direction


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outcome timestamps must be timezone-aware")
    return value.astimezone(UTC)


def outcome_anchor(completed_at: datetime) -> datetime:
    value = _utc(completed_at)
    floor = value.replace(second=0, microsecond=0)
    return floor if value == floor else floor + timedelta(minutes=1)


@dataclass(frozen=True)
class ForwardObservation:
    status: str
    anchor: datetime
    horizon_hours: int
    reason: str | None = None
    baseline: Decimal | None = None
    end_price: Decimal | None = None
    raw_return_pct: Decimal | None = None
    directional_return_pct: Decimal | None = None
    high_excursion_pct: Decimal | None = None
    low_excursion_pct: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_forward_path(
    *, anchor: datetime, horizon_hours: int, candles: Sequence[Candle], now: datetime,
    direction: Direction | None = None,
) -> ForwardObservation:
    anchor, now = _utc(anchor), _utc(now)
    if horizon_hours not in (24, 48) or anchor != outcome_anchor(anchor):
        raise ValueError("outcomes require a minute-aligned anchor and a 24h or 48h horizon")
    end = anchor + timedelta(hours=horizon_hours)
    if now < end:
        return ForwardObservation("not_due", anchor, horizon_hours)
    by_time: dict[datetime, Candle] = {}
    for candle in candles:
        opened, closed = _utc(candle.opened_at), _utc(candle.closed_at)
        if not anchor <= opened < end or not candle.closed or closed > now:
            continue
        if opened in by_time:
            return ForwardObservation("invalid", anchor, horizon_hours, "duplicate_minute")
        prices = (candle.open, candle.high, candle.low, candle.close)
        if (any(not p.is_finite() or p <= 0 for p in prices)
            or not candle.volume.is_finite() or candle.volume < 0
            or not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high
            or opened != outcome_anchor(opened)
            or closed - opened not in (timedelta(minutes=1), timedelta(milliseconds=59999))):
            return ForwardObservation("invalid", anchor, horizon_hours, "invalid_minute_ohlcv")
        by_time[opened] = candle
    expected = [anchor + timedelta(minutes=i) for i in range(horizon_hours * 60)]
    if any(stamp not in by_time for stamp in expected):
        return ForwardObservation("missing", anchor, horizon_hours, "incomplete_minute_window")
    bars = [by_time[stamp] for stamp in expected]
    baseline, finish = bars[0].open, bars[-1].close
    raw = (finish / baseline - 1) * 100
    directional = -raw if direction is Direction.SHORT else raw if direction is Direction.LONG else None
    return ForwardObservation("complete", anchor, horizon_hours, baseline=baseline, end_price=finish,
        raw_return_pct=raw, directional_return_pct=directional,
        high_excursion_pct=(max(bar.high for bar in bars) / baseline - 1) * 100,
        low_excursion_pct=(min(bar.low for bar in bars) / baseline - 1) * 100)


@dataclass(frozen=True)
class CohortSummary:
    ranks: dict[str, int]
    observed: int
    total: int
    full_coverage: bool
    missing: dict[str, str]


def rank_cohort(observations: Mapping[str, ForwardObservation]) -> CohortSummary:
    complete = sorted(((symbol, obs.raw_return_pct) for symbol, obs in observations.items()
                       if obs.status == "complete" and obs.raw_return_pct is not None), key=lambda row: (-row[1], row[0]))
    ranks: dict[str, int] = {}
    previous: Decimal | None = None
    rank = 0
    for index, (symbol, value) in enumerate(complete, 1):
        if previous is None or value != previous:
            rank = index
        ranks[symbol] = rank
        previous = value
    missing = {symbol: obs.status for symbol, obs in observations.items() if symbol not in ranks}
    return CohortSummary(ranks, len(complete), len(observations), bool(observations) and not missing, missing)


def diagnose_touches(
    candles: Sequence[Candle], *, direction: Direction, entry: Decimal, stop: Decimal,
    targets: tuple[Decimal, ...],
) -> dict[str, Any]:
    """Range touches only: OHLC cannot establish intrabar entry/exit ordering."""
    entry_seen = False
    stop_seen = False
    target_seen = [False] * len(targets)
    ambiguous = False
    for bar in sorted(candles, key=lambda candle: candle.opened_at):
        entry_hit = bar.low <= entry <= bar.high
        stop_hit = bar.low <= stop if direction is Direction.LONG else bar.high >= stop
        hits = [bar.high >= target if direction is Direction.LONG else bar.low <= target for target in targets]
        if (stop_hit or any(hits)) and (not entry_seen or (stop_hit and any(hits))):
            ambiguous = True
        entry_seen |= entry_hit
        stop_seen |= stop_hit
        target_seen = [previous or hit for previous, hit in zip(target_seen, hits, strict=True)]
    return {"entry_touched": entry_seen, "stop_touched": stop_seen, "targets_touched": tuple(target_seen),
            "ambiguous_order": ambiguous, "interpretation": "range_touches_not_fills"}
