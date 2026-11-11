"""Versioned, lossless records for public-data screening observations."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from algotrader.adapters.external.models import (
    FearGreedReading,
    GlobalMarketMetrics,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.models import (
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)

SCHEMA_VERSION = 1


@dataclass
class ScanDecisionJournal:
    events: list[dict[str, Any]] = field(default_factory=list)
    cohort: tuple[Instrument, ...] = ()

    def note(self, symbol: str, stage: str, reason: str, **details: Any) -> None:
        self.events.append({"symbol": symbol, "stage": stage, "reason": reason, **details})

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        import json
        return tuple(sorted(self.events, key=lambda event: json.dumps(event, sort_keys=True, default=str)))


@dataclass(frozen=True)
class RecordedCall:
    method: str
    index: int
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass(frozen=True)
class ScanReceipt:
    scan_id: str
    status: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"scan_id": self.scan_id, "status": self.status, "error": self.error}


@dataclass(frozen=True)
class ScanRecord:
    scan_id: str
    started_at: datetime
    completed_at: datetime
    status: str
    identity: dict[str, Any]
    request: dict[str, Any]
    inputs: tuple[RecordedCall, ...]
    decisions: tuple[dict[str, Any], ...]
    opportunities: tuple[dict[str, Any], ...]
    counts: dict[str, int]
    cohort: tuple[Instrument, ...]
    schema_version: int = SCHEMA_VERSION
    error: str | None = None


# Explicit registry: archives cannot import or instantiate arbitrary Python classes.
_TYPES: dict[str, Any] = {cls.__name__: cls for cls in (
    RecordedCall, ScanReceipt, ScanRecord, InstrumentId, Instrument, Candle, Quote,
    DerivativesSnapshot, FearGreedReading, StablecoinSupplyMetric, GlobalMarketMetrics,
    MacroSentimentContext, AssetClass, Venue, MarketKind, Direction, ScreenerMode,
)}


def encode_value(value: Any) -> Any:
    # Exact primitives only: enum/subclass tagged representations remain unchanged.
    kind = type(value)
    if value is None or kind is str or kind is int or kind is float or kind is bool:
        return value
    if isinstance(value, Enum):
        return {"@": type(value).__name__, "value": value.value}
    if isinstance(value, Decimal):
        return {"@": "Decimal", "value": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("archive timestamps must be timezone-aware")
        return {"@": "datetime", "value": value.astimezone(UTC).isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        if type(value).__name__ not in _TYPES:
            raise ValueError("unsupported archive record type")
        return {"@": type(value).__name__, "fields": {f.name: encode_value(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return {"@": "tuple", "items": [encode_value(item) for item in value]}
    if isinstance(value, list):
        return [encode_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("archive mapping keys must be strings")
        return {"@": "mapping", "items": {key: encode_value(item) for key, item in value.items()}}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"unsupported archive value: {type(value).__name__}")


def decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("@")
    if tag == "Decimal":
        return Decimal(value["value"])
    if tag == "datetime":
        result = datetime.fromisoformat(value["value"])
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("naive archive timestamp")
        return result.astimezone(UTC)
    if tag == "tuple":
        return tuple(decode_value(item) for item in value["items"])
    if tag == "mapping":
        return {key: decode_value(item) for key, item in value["items"].items()}
    if tag not in _TYPES:
        raise ValueError("unknown archive type")
    cls = _TYPES[tag]
    if issubclass(cls, Enum):
        return cls(value["value"])
    return cls(**{key: decode_value(item) for key, item in value["fields"].items()})
