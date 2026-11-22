"""Closed-window evidence for continuation; legacy aggregate values remain untouched."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

VERSION = "continuation-inputs-v1"
HOUR_MS = 3_600_000


def aligned_inputs(
    oi: Mapping[int, Decimal],
    taker: Mapping[int, tuple[Decimal, Decimal]],
    premium: Mapping[str, Any],
    reference_ms: int,
    observed_at: datetime,
) -> dict[str, Any] | None:
    """Require the latest closed UTC hour, not independently lagging components."""
    end = (reference_ms // HOUR_MS) * HOUR_MS
    try:
        funding_at = datetime.fromtimestamp(int(premium["time"]) / 1000, UTC)
        funding = Decimal(str(premium["lastFundingRate"]))
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    if (
        not funding.is_finite()
        or observed_at.tzinfo is None
        or not timedelta(seconds=-5) <= observed_at - funding_at <= timedelta(minutes=5)
        or abs(int(observed_at.timestamp() * 1000) - reference_ms) > 300_000
        or end not in oi
        or end - HOUR_MS not in taker
    ):
        return None

    def change(hours: int) -> Decimal | None:
        current, previous = oi.get(end), oi.get(end - hours * HOUR_MS)
        if (
            current is None
            or previous is None
            or not current.is_finite()
            or not previous.is_finite()
            or current <= 0
            or previous <= 0
        ):
            return None
        return (current - previous) / previous

    def ratio(hours: int) -> Decimal | None:
        rows = [taker.get(end - offset * HOUR_MS) for offset in range(1, hours + 1)]
        if any(row is None for row in rows):
            return None
        buys = sum((row[0] for row in rows if row is not None), Decimal("0"))
        sells = sum((row[1] for row in rows if row is not None), Decimal("0"))
        if not buys.is_finite() or not sells.is_finite() or buys < 0 or sells <= 0:
            return None
        return buys / sells

    next_ms = premium.get("nextFundingTime")
    next_at = None
    if isinstance(next_ms, (int, float)) and next_ms > 0:
        try:
            next_at = datetime.fromtimestamp(next_ms / 1000, UTC)
        except (ValueError, OverflowError, OSError):
            next_at = None
    window = datetime.fromtimestamp(end / 1000, UTC)
    return {
        "version": VERSION,
        "reference_at": datetime.fromtimestamp(reference_ms / 1000, UTC),
        "oi_points": tuple(
            (datetime.fromtimestamp(t / 1000, UTC), oi[t])
            for t in (end, end - HOUR_MS, end - 4 * HOUR_MS, end - 168 * HOUR_MS)
            if t in oi
        ),
        "taker_points": tuple(
            (datetime.fromtimestamp(t / 1000, UTC), *taker[t])
            for t in (end - HOUR_MS, end - 2 * HOUR_MS, end - 3 * HOUR_MS, end - 4 * HOUR_MS)
            if t in taker
        ),
        "oi_window_end": window,
        "taker_window_end": window,
        "observed_at": observed_at,
        "oi_change_1h": change(1),
        "oi_change_4h": change(4),
        "oi_change_7d": change(168),
        "taker_ratio_1h": ratio(1),
        "taker_ratio_4h": ratio(4),
        "funding_rate": funding,
        "funding_observed_at": funding_at,
        "funding_next_at": next_at,
        "funding_interval_hours": None,
        "funding_policy": "sign_only_no_interval_magnitude_comparison",
    }


def reproducible_inputs(evidence: Mapping[str, Any]) -> bool:
    """Certify aggregates against the archived endpoint observations and window keys."""
    try:
        oi: dict[int, Decimal] = {}
        taker: dict[int, tuple[Decimal, Decimal]] = {}
        for row in evidence["oi_points"]:
            if (
                len(row) != 2
                or not isinstance(row[0], datetime)
                or row[0].tzinfo is None
                or not isinstance(row[1], Decimal)
                or not row[1].is_finite()
                or row[1] <= 0
            ):
                return False
            key = int(row[0].timestamp() * 1000)
            if key in oi:
                return False
            oi[key] = row[1]
        for row in evidence["taker_points"]:
            if (
                len(row) != 3
                or not isinstance(row[0], datetime)
                or row[0].tzinfo is None
                or not all(isinstance(v, Decimal) and v.is_finite() and v >= 0 for v in row[1:])
            ):
                return False
            key = int(row[0].timestamp() * 1000)
            if key in taker:
                return False
            taker[key] = (row[1], row[2])
        times = tuple(evidence[k] for k in ("reference_at", "observed_at", "funding_observed_at"))
        if not all(isinstance(t, datetime) and t.tzinfo is not None for t in times):
            return False
        reference, observed, funding_at = times
        rebuilt = aligned_inputs(
            oi,
            taker,
            {
                "time": int(funding_at.timestamp() * 1000),
                "lastFundingRate": evidence["funding_rate"],
            },
            int(reference.timestamp() * 1000),
            observed,
        )
        keys = (
            "oi_window_end",
            "taker_window_end",
            "oi_change_1h",
            "oi_change_4h",
            "oi_change_7d",
            "taker_ratio_1h",
            "taker_ratio_4h",
            "funding_rate",
        )
        return rebuilt is not None and all(rebuilt[k] == evidence[k] for k in keys)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        return False
