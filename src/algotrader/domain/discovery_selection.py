"""Observable activity ranks and coverage scheduling for a versioned research policy."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument, Quote
from algotrader.domain.recovery_episodes import require_aware

ACTIVITY_POLICY = "recovery-activity-watch-v1-12rank8rotation"


def liquid_quote(quote: Quote, at: datetime, *, max_age_seconds: int = 120) -> bool:
    return (
        quote.is_valid_book
        and quote.quote_volume_24h > 0
        and quote.observed_at.utcoffset() is not None
        and quote.is_fresh_at(at, max_age_seconds=float(max_age_seconds), max_future_seconds=5.0)
        and quote.quote_volume_24h >= 500000
        and 0 <= quote.spread_bps <= 25
    )


def activity_scores(instruments: Sequence[Instrument], quotes: Mapping[str, Quote], at: datetime, *, max_age_seconds: int = 120) -> dict[str, Decimal]:
    require_aware(at)
    features = {}
    for instrument in instruments:
        quote = quotes.get(instrument.id.symbol)
        if quote is None or not liquid_quote(quote, at, max_age_seconds=max_age_seconds):
            continue
        high, low, change = quote.high_24h, quote.low_24h, quote.price_change_pct_24h
        if (high is None or low is None or change is None
                or not all(v.is_finite() for v in (high, low, change))
                or not 0 < low <= quote.last <= high or high <= low or change > 9):
            continue
        features[instrument.id.symbol] = ((quote.last - low) / (high - low), change)
    # Midranks handle ties consistently without injecting turnover as a hidden weight.
    result = {}
    for symbol, values in features.items():
        ranks = []
        for column, value in enumerate(values):
            smaller = sum(other[column] < value for other in features.values())
            equal = sum(other[column] == value for other in features.values())
            ranks.append((Decimal(smaller) + Decimal(equal) / 2) / len(features))
        result[symbol] = sum(ranks, Decimal("0")) / 2
    return result


def rotation_key(symbol: str, at: datetime) -> str:
    bucket = int(at.timestamp()) // (4 * 3600)
    return hashlib.sha256(f"{ACTIVITY_POLICY}|{bucket}|{symbol}".encode()).hexdigest()


def activity_watch_selection(
    selected: list[Instrument], liquid: list[Instrument], quotes: Mapping[str, Quote],
    budget: int, at: datetime, *, rotation_at: datetime | None = None,
) -> list[Instrument]:
    rotation = rotation_at or at
    ids = {i.id for i in selected}
    pool = [i for i in liquid if i.id not in ids and i.id.symbol in quotes
            and liquid_quote(quotes[i.id.symbol], at, max_age_seconds=600)
            and quotes[i.id.symbol].quote_volume_24h < 1500000]
    remaining = min(20, max(0, budget - len(selected)))
    scores = activity_scores(pool, quotes, at, max_age_seconds=600)
    ranked = sorted((i for i in pool if i.id.symbol in scores),
                    key=lambda i: (-scores[i.id.symbol], rotation_key(i.id.symbol, rotation)))
    chosen = ranked[:min(12, remaining)]
    chosen_ids = {i.id for i in chosen}
    rest = sorted((i for i in pool if i.id not in chosen_ids), key=lambda i: rotation_key(i.id.symbol, rotation))
    return [*selected, *chosen, *rest[:remaining - len(chosen)]]


def scout_selection(
    cohort: Sequence[Instrument], quotes: Mapping[str, Quote], seen: Mapping[str, datetime],
    at: datetime, *, limit: int = 64,
) -> tuple[Instrument, ...]:
    if not 1 <= limit <= 64:
        raise ValueError("invalid scout capacity")
    pool = [i for i in cohort if i.id.symbol in quotes and liquid_quote(quotes[i.id.symbol], at)]
    scores = activity_scores(pool, quotes, at)
    priority = sorted((i for i in pool if i.id.symbol in scores),
                      key=lambda i: (-scores[i.id.symbol], rotation_key(i.id.symbol, at)))[:min(16, limit)]
    ids = {i.id for i in priority}
    rest = sorted((i for i in pool if i.id not in ids), key=lambda i: (
        seen.get(i.id.symbol, at - timedelta(days=36500)), rotation_key(i.id.symbol, at)))
    return tuple((*priority, *rest[:limit - len(priority)]))


def scout_features(candles: Sequence[Candle], btc: Sequence[Candle], at: datetime) -> dict[str, object]:
    own, benchmark = tuple(candles[-96:]), tuple(btc[-24:])
    for series in (own, benchmark):
        if (not series or any(c.closed is not True or c.closed_at > at for c in series)
                or any(b.opened_at - a.opened_at != timedelta(minutes=15)
                       for a, b in zip(series, series[1:], strict=False))
                or not 0 <= (at - series[-1].closed_at).total_seconds() <= 901):
            return {"available": False, "reason": "unaligned_or_stale_history"}
    if len(own) != 96 or len(benchmark) != 24 or own[-1].closed_at != benchmark[-1].closed_at:
        return {"available": False, "reason": "short_or_unaligned_history"}
    values = [c.quote_volume for c in own]
    if (any(not v.is_finite() or v < 0 for v in values)
            or any(not c.open.is_finite() or c.open <= 0 or not c.close.is_finite() or c.close <= 0
                   for c in (*own, *benchmark))):
        return {"available": False, "reason": "invalid_prices_or_turnover"}
    prior = sum(values[:-24], Decimal("0")) / 72
    if prior <= 0:
        return {"available": False, "reason": "nonpositive_turnover_baseline"}
    acceleration = sum(values[-24:], Decimal("0")) / 24 / prior
    relative = (own[-1].close / own[-24].open - benchmark[-1].close / benchmark[0].open) * 100
    ret24 = (own[-1].close / own[0].open - 1) * 100
    return {"available": True, "relative_6h_pp": relative, "turnover_acceleration": acceleration,
            "return_24h_pct": ret24, "emerging": relative > 0 and acceleration > 1 and ret24 <= 9,
            "last_closed_at": own[-1].closed_at}


def positioning_regime(snapshot: DerivativesSnapshot | None, at: datetime) -> str:
    if snapshot is None or snapshot.degraded_telemetry or snapshot.observed_at.utcoffset() is None:
        return "unknown"
    value = snapshot.open_interest_change_1h
    if value is None or not value.is_finite() or not -5 <= (at - snapshot.observed_at).total_seconds() <= 300:
        return "unknown"
    return "oi_expanding" if value > 0 else "oi_contracting" if value < 0 else "oi_flat"
