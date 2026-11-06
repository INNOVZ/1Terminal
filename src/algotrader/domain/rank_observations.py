"""Lossless research envelopes and whole-original-cohort rolling ranks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from algotrader.domain.models import Instrument, MarketKind, Venue
from algotrader.domain.shadow_discovery import ShadowPolicy, classify_shadow

Rows = tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RankPolicy:
    version: str = "prospective-ranks-v1"
    max_cohort: int = 1000
    max_attempts: int = 3
    deadline_seconds: int = 15
    snapshot_max_age_seconds: int = 60
    snapshot_future_skew_seconds: int = 5
    snapshot_max_span_seconds: int = 60
    sampling_target_seconds: int = 300
    sampling_max_gap_seconds: int = 600

    def __post_init__(self) -> None:
        for value, cap in (
            (self.max_cohort, 1000),
            (self.max_attempts, 3),
            (self.deadline_seconds, 15),
            (self.snapshot_max_age_seconds, 60),
            (self.snapshot_future_skew_seconds, 5),
            (self.snapshot_max_span_seconds, 60),
            (self.sampling_target_seconds, 300),
            (self.sampling_max_gap_seconds, 600),
        ):
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("rank policy exceeds safety caps")
        if self.version != "prospective-ranks-v1":
            raise ValueError("unsupported rank policy")


def finite_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def validate_cohort(cohort: tuple[Instrument, ...], policy: RankPolicy) -> None:
    if not isinstance(cohort, tuple) or not 1 <= len(cohort) <= policy.max_cohort:
        raise ValueError("invalid rank cohort size")
    if any(not isinstance(inst, Instrument) for inst in cohort):
        raise ValueError("invalid rank instrument")
    if len({i.id.symbol for i in cohort}) != len(cohort) or len(
        {i.base_asset for i in cohort}
    ) != len(cohort):
        raise ValueError("noncanonical rank cohort")
    if any(
        i.id.venue != Venue.BINANCE_USDM
        or i.market_kind != MarketKind.FUTURES
        or not i.base_asset
        or not i.quote_asset
        or i.id.symbol != i.base_asset + i.quote_asset
        or re.fullmatch(r"[A-Z0-9_]{1,80}", i.id.symbol) is None
        for i in cohort
    ):
        raise ValueError("invalid canonical USD-M cohort")


def normalize_ranks(
    cohort: tuple[Instrument, ...], rows: Rows, observed_at: datetime, policy: RankPolicy
) -> dict[str, Any]:
    validate_cohort(cohort, policy)
    if not isinstance(rows, tuple) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid raw ticker rows")
    compatible = ShadowPolicy(
        snapshot_max_age_seconds=policy.snapshot_max_age_seconds,
        snapshot_future_skew_seconds=policy.snapshot_future_skew_seconds,
        snapshot_max_span_seconds=policy.snapshot_max_span_seconds,
    )
    classification = classify_shadow(
        cohort, [{"symbol": i.id.symbol} for i in cohort], rows, observed_at, compatible
    )
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str):
            by_symbol.setdefault(symbol, []).append(row)
    members = []
    for label in classification.labels:
        matches = by_symbol.get(label["symbol"], [])
        raw = matches[0] if len(matches) == 1 else {}
        change = label["return_24h"]
        volume = finite_number(raw.get("quoteVolume"))
        members.append(
            {
                "symbol": label["symbol"],
                "price": label["price"],
                "return_24h": change,
                "cutoff": label["cutoff"],
                "open_time": raw.get("openTime"),
                "close_time": raw.get("closeTime"),
                "rank": label["rank"],
                "inside": None if label["rank"] is None else label["rank"] <= 10,
                "return_sign": None
                if change is None
                else "positive"
                if change > 0
                else "negative"
                if change < 0
                else "zero",
                "quote_volume": volume if volume is not None and volume >= 0 else None,
            }
        )
    return {
        "complete": classification.coverage["complete"],
        "coverage": classification.coverage,
        "members": tuple(members),
    }


@dataclass(frozen=True)
class RankSnapshotRecord:
    snapshot_id: str
    parent_id: str
    parent_checksum: str
    cohort: tuple[Instrument, ...]
    cohort_hash: str
    policy: dict[str, Any]
    identity: dict[str, Any]
    rows: Rows
    normalized: dict[str, Any]
    requested_at: datetime
    arrived_at: datetime | None
    completed_at: datetime
    accounting: dict[str, Any]
    status: str
    error: str | None = None
    provider_requested_at: datetime | None = None
    provider_finished_at: datetime | None = None
    schema_version: int = 1


@dataclass(frozen=True)
class BenchmarkRecord:
    report_id: str
    parent_id: str
    shadow_id: str
    as_of: datetime
    created_at: datetime
    horizon_hours: int
    policy: dict[str, Any]
    identity: dict[str, Any]
    manifest: dict[str, Any]
    payload: dict[str, Any]
    schema_version: int = 1
