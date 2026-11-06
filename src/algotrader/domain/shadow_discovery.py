"""Pure research admission and labels; never changes core publication policy."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel
from algotrader.domain.models import Instrument, Quote
from algotrader.domain.scan_observations import ScanRecord


@dataclass(frozen=True)
class ShadowPolicy:
    version: str = "bounded-shadow-v1"
    max_tokens: int = 15
    max_attempts: int = 150
    deadline_seconds: int = 90
    parent_max_age_seconds: int = 300
    snapshot_max_age_seconds: int = 60
    snapshot_future_skew_seconds: int = 5
    snapshot_max_span_seconds: int = 60
    macro_context_max_age_seconds: int = 300
    macro_component_max_age_seconds: int = 172800
    derivatives_max_age_seconds: int = 60

    def __post_init__(self) -> None:
        limits = (
            (self.max_tokens, 15),
            (self.max_attempts, 150),
            (self.deadline_seconds, 90),
            (self.parent_max_age_seconds, 300),
            (self.snapshot_max_age_seconds, 60),
            (self.snapshot_future_skew_seconds, 5),
            (self.snapshot_max_span_seconds, 60),
            (self.macro_context_max_age_seconds, 300),
            (self.macro_component_max_age_seconds, 172800),
            (self.derivatives_max_age_seconds, 60),
        )
        if any(type(value) is not int or not 1 <= value <= cap for value, cap in limits):
            raise ValueError("shadow limits must be positive integers within safety caps")


@dataclass(frozen=True)
class ShadowAdmission:
    instrument: Instrument
    bucket: str


@dataclass(frozen=True)
class AdmissionReport:
    selected: tuple[ShadowAdmission, ...]
    decisions: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ClassificationReport:
    labels: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("shadow clocks must be timezone-aware")


def admit_shadow(parent: ScanRecord, policy: ShadowPolicy, now: datetime) -> AdmissionReport:
    if str(UUID(parent.scan_id)) != parent.scan_id:
        raise ValueError("parent ID must be a canonical UUID")
    _aware(now)
    _aware(parent.started_at)
    _aware(parent.completed_at)
    age = (now - parent.completed_at).total_seconds()
    score_floor = _number(parent.request.get("min_score", "68"))
    if score_floor is None or score_floor < 68:
        raise ValueError("diagnostic score floors are not production-quality")
    if (
        parent.schema_version != 1
        or parent.status != "complete"
        or parent.error is not None
        or parent.started_at > parent.completed_at
        or not 0 <= age <= policy.parent_max_age_seconds
        or parent.request.get("mode", ScreenerMode.ACCUMULATION) != ScreenerMode.ALL
        or parent.request.get("enforce_quality", True) is not True
    ):
        raise ValueError("requires a fresh complete production-quality ALL parent")
    if len({inst.base_asset for inst in parent.cohort}) != len(parent.cohort):
        raise ValueError("parent cohort must be canonical per asset")
    selected_core = {
        event["symbol"]
        for event in parent.decisions
        if event.get("stage") == "selection" and event.get("reason") == "selected"
    }
    published = {item["symbol"] for item in parent.opportunities}
    quotes: dict[str, Quote] = {}
    duplicate_quotes: set[str] = set()
    for call in parent.inputs:
        if call.method != "quotes" or call.error is not None:
            continue
        for quote in call.result:
            symbol = quote.instrument_id.symbol
            if symbol in quotes:
                duplicate_quotes.add(symbol)
            quotes[symbol] = quote
    decisions: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[Instrument]] = {
        "quote_anomaly": [],
        "momentum_control": [],
        "coverage_probe": [],
    }
    for inst in parent.cohort:
        symbol = inst.id.symbol
        quote = quotes.get(symbol)
        reason = "eligible"
        if symbol in published:
            reason = "core_published"
        elif quote is None or symbol in duplicate_quotes:
            reason = "missing_or_ambiguous_quote"
        else:
            volume, bid, ask = (
                _number(quote.quote_volume_24h),
                _number(quote.bid),
                _number(quote.ask),
            )
            if (
                volume is None
                or bid is None
                or ask is None
                or bid <= 0
                or ask < bid
                or volume < Decimal("1500000")
                or (ask - bid) / ((ask + bid) / 2) * 10000 > 25
            ):
                reason = "liquidity_hygiene"
        decisions[symbol] = {
            "symbol": symbol,
            "reason": reason,
            "core_selected": symbol in selected_core,
            "eligible_buckets": [],
        }
        if reason != "eligible" or quote is None:
            continue
        funding, basis, change = (
            _number(quote.funding_rate),
            _number(quote.basis_pct),
            _number(quote.price_change_pct_24h),
        )
        if symbol not in selected_core:
            buckets["coverage_probe"].append(inst)
            if funding is not None and basis is not None and funding < 0 and basis > 0:
                buckets["quote_anomaly"].append(inst)
        if change is not None and change > 0:
            buckets["momentum_control"].append(inst)
        decisions[symbol].update(
            funding_rate=funding,
            basis_pct=basis,
            return_24h=change,
            quote_volume=quote.quote_volume_24h,
            quote_observed_at=quote.observed_at,
        )
    buckets["quote_anomaly"].sort(
        key=lambda inst: (
            cast(Decimal, quotes[inst.id.symbol].funding_rate),
            -cast(Decimal, quotes[inst.id.symbol].basis_pct),
            inst.id.symbol,
        )
    )
    buckets["momentum_control"].sort(
        key=lambda inst: (
            -cast(Decimal, quotes[inst.id.symbol].price_change_pct_24h),
            -quotes[inst.id.symbol].quote_volume_24h,
            inst.id.symbol,
        )
    )
    buckets["coverage_probe"].sort(
        key=lambda inst: (
            hashlib.sha256(f"{parent.scan_id}:{inst.id.symbol}".encode()).hexdigest(),
            inst.id.symbol,
        )
    )
    for bucket, instruments in buckets.items():
        for inst in instruments:
            decisions[inst.id.symbol]["eligible_buckets"].append(bucket)
    admitted: list[ShadowAdmission] = []
    seen: set[str] = set()
    consumed: Counter[str] = Counter()
    while len(admitted) < policy.max_tokens:
        progress = False
        for bucket, instruments in buckets.items():
            if len(admitted) >= policy.max_tokens:
                break
            while instruments and instruments[0].base_asset in seen:
                instruments.pop(0)
            if consumed[bucket] >= 5 or not instruments:
                continue
            inst = instruments.pop(0)
            admitted.append(ShadowAdmission(inst, bucket))
            seen.add(inst.base_asset)
            consumed[bucket] += 1
            decisions[inst.id.symbol].update(reason="admitted", bucket=bucket)
            progress = True
        if not progress:
            break
    for decision in decisions.values():
        if decision["reason"] == "eligible":
            decision["reason"] = (
                "run_or_bucket_limit" if decision["eligible_buckets"] else "no_admission_evidence"
            )
    return AdmissionReport(
        tuple(admitted), tuple(decisions[symbol] for symbol in sorted(decisions))
    )


def classify_shadow(
    cohort: Sequence[Instrument],
    candidates: Sequence[dict[str, Any]],
    rows: Sequence[dict[str, Any]],
    observed_at: datetime,
    policy: ShadowPolicy,
) -> ClassificationReport:
    _aware(observed_at)
    symbols = {inst.id.symbol for inst in cohort}
    counts = Counter(str(row.get("symbol")) for row in rows)
    valid: dict[str, tuple[Decimal, Decimal, datetime]] = {}
    invalid: dict[str, str] = {}
    for row in rows:
        symbol = str(row.get("symbol"))
        if symbol not in symbols:
            continue
        price, change, timestamp = (
            _number(row.get("lastPrice")),
            _number(row.get("priceChangePercent")),
            _number(row.get("closeTime")),
        )
        if (
            counts[symbol] != 1
            or price is None
            or change is None
            or price <= 0
            or timestamp is None
        ):
            invalid[symbol] = "invalid_or_duplicate_ticker"
            continue
        try:
            cutoff = datetime.fromtimestamp(float(timestamp / 1000), UTC)
        except (OverflowError, ValueError, OSError):
            invalid[symbol] = "invalid_timestamp"
            continue
        if (
            not -policy.snapshot_future_skew_seconds
            <= (observed_at - cutoff).total_seconds()
            <= policy.snapshot_max_age_seconds
        ):
            invalid[symbol] = "stale_or_future_ticker"
            continue
        valid[symbol] = (price, change, cutoff)
    cutoffs = [item[2] for item in valid.values()]
    span_ok = (
        bool(cutoffs)
        and (max(cutoffs) - min(cutoffs)).total_seconds() <= policy.snapshot_max_span_seconds
    )
    complete = (
        bool(symbols) and len(symbols) == len(cohort) and len(valid) == len(symbols) and span_ok
    )
    # Competition ranks retain gaps after ties: 1 + number of strictly greater
    # returns. Sort once instead of comparing every candidate with every member.
    ranks: dict[Decimal, int] = {}
    if complete:
        for index, change in enumerate(
            sorted((value[1] for value in valid.values()), reverse=True), 1
        ):
            ranks.setdefault(change, index)
    labels: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol"))
        values = valid.get(symbol)
        trigger = _number(candidate.get("breakout_trigger"))
        phase = "unknown"
        if (
            values is not None
            and trigger is not None
            and trigger > 0
            and candidate.get("direction") == "long"
        ):
            phase = "pre_trigger" if values[0] < trigger else "at_or_above_trigger"
        rank = ranks[values[1]] if complete and values else None
        status = "unknown" if rank is None else "within_top_10" if rank <= 10 else "outside_top_10"
        labels.append(
            {
                "symbol": symbol,
                "price_phase": phase,
                "decision_gainer_status": status,
                "rank": rank,
                "early_candidate": phase == "pre_trigger" and status == "outside_top_10",
                "price": values[0] if values else None,
                "return_24h": values[1] if values else None,
                "cutoff": values[2] if values else None,
                "classified_at": observed_at,
            }
        )
    return ClassificationReport(
        tuple(labels),
        {
            "complete": complete,
            "expected": len(cohort),
            "valid": len(valid),
            "missing": sorted(symbols - valid.keys()),
            "invalid": invalid,
            "span_ok": span_ok,
        },
    )


_FAMILIES = {
    "structural_accumulation": {
        "COIL_SPRING_LONG",
        "VCP_COMPRESSION_LONG",
        "RANGE_BOTTOM_ACCUMULATION_LONG",
    },
    "positioning_squeeze": {
        "OI_ACCUMULATION_LONG",
        "SHORT_SQUEEZE_ABSORPTION_LONG",
        "FUNDING_SQUEEZE_SPRING_LONG",
    },
    "active_expansion": {"BREAKOUT_LONG", "MOMENTUM_EMERGING_LONG"},
    "distribution_reversal_short": {
        "UTAD_DIVERGENCE_SHORT",
        "EXHAUSTION_REVERSAL_SHORT",
        "DISTRIBUTION_SHORT",
        "VCP_DISTRIBUTION_SHORT",
    },
    "legacy_compatibility": {
        "STEALTH_OI_ACCUMULATION",
        "COIL_BREAKDOWN_SHORT",
        "BREAKOUT_SHORT",
        "CONTINUATION_LONG",
        "CONTINUATION_SHORT",
        "SQUEEZE_LONG",
        "SQUEEZE_SHORT",
        "MOMENTUM_EMERGING_SHORT",
        "RANGE_FADE_LONG",
        "RANGE_FADE_SHORT",
        "NO_SETUP",
    },
}


def setup_family(label: str) -> str:
    value = SetupLabel.__members__[label].value if label in SetupLabel.__members__ else label
    for family, members in _FAMILIES.items():
        if value in members:
            return family
    raise ValueError("unmapped setup label")
