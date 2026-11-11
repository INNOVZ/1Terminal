"""Isolated paired-study v3 rank bounds; legacy rank/shadow defaults are untouched."""

from __future__ import annotations

from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.domain.rank_observations import RankPolicy, finite_number, validate_cohort


def normalize_ranks(cohort, rows, observed_at, policy=None):
    validate_cohort(cohort, RankPolicy())
    if (
        observed_at.tzinfo is None
        or not isinstance(rows, tuple)
        or any(not isinstance(r, dict) for r in rows)
    ):
        raise ValueError("invalid rank snapshot")
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get("symbol"), []).append(row)
    valid, reasons = {}, {}
    for inst in cohort:
        symbol = inst.id.symbol
        matches = grouped.get(symbol, ())
        if len(matches) != 1:
            reasons[symbol] = "missing_or_duplicate"
            continue
        row = matches[0]
        price, change = (
            finite_number(row.get("lastPrice")),
            finite_number(row.get("priceChangePercent")),
        )
        cutoff = finite_number(row.get("closeTime"))
        if price is None or price <= 0 or change is None or cutoff is None:
            reasons[symbol] = "invalid_values"
            continue
        try:
            at = datetime.fromtimestamp(float(cutoff / Decimal(1000)), UTC)
        except (ValueError, OverflowError, OSError):
            reasons[symbol] = "invalid_timestamp"
            continue
        if not -5 <= (observed_at - at).total_seconds() <= 60:
            reasons[symbol] = "stale_or_future"
            continue
        valid[symbol] = (price, change, at, row)
    stamps = [v[2] for v in valid.values()]
    span_ok = bool(stamps) and (max(stamps) - min(stamps)).total_seconds() <= 60
    returns = sorted(v[1] for v in valid.values())
    unknown = len(cohort) - len(valid)
    members = []
    for inst in cohort:
        symbol = inst.id.symbol
        value = valid.get(symbol)
        lower = 1 + len(returns) - bisect_right(returns, value[1]) if value and span_ok else None
        upper = lower + unknown if lower is not None else None
        inside = (
            True
            if upper is not None and upper <= 10
            else False
            if lower is not None and lower > 10
            else None
        )
        members.append(
            {
                "symbol": symbol,
                "price": value[0] if value else None,
                "return_24h": value[1] if value else None,
                "cutoff": value[2] if value else None,
                "rank": lower if lower is not None and lower == upper else None,
                "rank_min": lower,
                "rank_max": upper,
                "inside": inside,
                "unknown_reason": reasons.get(symbol) if span_ok else "snapshot_span_unavailable",
            }
        )
    return {
        "complete": unknown == 0 and span_ok,
        "bounded": bool(valid) and span_ok,
        "coverage": {
            "known": len(valid),
            "unknown": unknown,
            "cohort": len(cohort),
            "span_ok": span_ok,
        },
        "members": tuple(members),
    }
