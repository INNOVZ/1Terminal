"""Versioned, lossless records for public-data screening observations."""
from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass, replace
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


@dataclass(slots=True)
class ScanDecisionJournal:
    events: list[dict[str, Any]] = field(default_factory=list)
    cohort: tuple[Instrument, ...] = ()

    def note(self, symbol: str, stage: str, reason: str, **details: Any) -> None:
        self.events.append({"symbol": symbol, "stage": stage, "reason": reason, **details})

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(sorted(self.events, key=lambda event: json.dumps(event, sort_keys=True, default=str)))


@dataclass(frozen=True, slots=True)
class RecordedCall:
    method: str
    index: int
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ScanReceipt:
    scan_id: str
    status: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"scan_id": self.scan_id, "status": self.status, "error": self.error}


@dataclass(frozen=True, slots=True)
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

_FIELD_NAMES_CACHE: dict[type, tuple[str, ...]] = {}


def _sorted_field_names(cls: type) -> tuple[str, ...]:
    names = _FIELD_NAMES_CACHE.get(cls)
    if names is None:
        names = tuple(sorted(f.name for f in fields(cls)))
        _FIELD_NAMES_CACHE[cls] = names
    return names


def _normalize_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("archive timestamps must be timezone-aware")
    if type(value) is datetime and value.tzinfo is UTC:
        return value
    return datetime.fromisoformat(value.astimezone(UTC).isoformat()).astimezone(UTC)


def _format_utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("archive timestamps must be timezone-aware")
    if value.tzinfo is UTC:
        return value.isoformat()
    return value.astimezone(UTC).isoformat()


def snapshot_recorded_value(value: Any) -> Any:
    """Validate and freeze recorded inputs without allocating duplicate immutable objects."""
    kind = type(value)
    if value is None or kind is str or kind is int or kind is float or kind is bool:
        return value
    if kind is Decimal:
        return value
    if kind is datetime:
        return _normalize_utc_datetime(value)
    if kind is Candle:
        if (
            type(value.opened_at) is datetime
            and type(value.closed_at) is datetime
            and type(value.open) is Decimal
            and type(value.high) is Decimal
            and type(value.low) is Decimal
            and type(value.close) is Decimal
            and type(value.volume) is Decimal
            and type(value.quote_volume) is Decimal
            and type(value.closed) is bool
            and type(value.taker_buy_volume) is Decimal
            and type(value.taker_buy_quote_volume) is Decimal
        ):
            opened = _normalize_utc_datetime(value.opened_at)
            closed = _normalize_utc_datetime(value.closed_at)
            if opened is value.opened_at and closed is value.closed_at:
                return value
            return replace(value, opened_at=opened, closed_at=closed)
    if kind is InstrumentId:
        if type(value.venue) is Venue and type(value.symbol) is str:
            return value
    if kind is Instrument:
        if (
            type(value.id) is InstrumentId
            and type(value.id.venue) is Venue
            and type(value.id.symbol) is str
            and type(value.base_asset) is str
            and type(value.quote_asset) is str
            and type(value.asset_class) is AssetClass
            and type(value.market_kind) is MarketKind
            and type(value.trading) is bool
            and type(value.price_tick) is Decimal
            and type(value.quantity_step) is Decimal
            and type(value.min_quantity) is Decimal
            and type(value.min_notional) is Decimal
            and type(value.max_leverage) is int
        ):
            return value
    if kind is Quote:
        if (
            type(value.instrument_id) is InstrumentId
            and type(value.instrument_id.venue) is Venue
            and type(value.instrument_id.symbol) is str
            and type(value.bid) is Decimal
            and type(value.ask) is Decimal
            and type(value.last) is Decimal
            and type(value.quote_volume_24h) is Decimal
            and type(value.observed_at) is datetime
            and (value.high_24h is None or type(value.high_24h) is Decimal)
            and (value.low_24h is None or type(value.low_24h) is Decimal)
            and (value.price_change_pct_24h is None or type(value.price_change_pct_24h) is Decimal)
            and (value.funding_rate is None or type(value.funding_rate) is Decimal)
            and (value.basis_pct is None or type(value.basis_pct) is Decimal)
        ):
            obs = _normalize_utc_datetime(value.observed_at)
            if obs is value.observed_at:
                return value
            return replace(value, observed_at=obs)
    if isinstance(value, Enum):
        cls_name = type(value).__name__
        if cls_name not in _TYPES:
            raise ValueError(f"unsupported archive value: {cls_name}")
        return _TYPES[cls_name](value.value)
    if isinstance(value, Decimal):
        return Decimal(str(value))
    if isinstance(value, datetime):
        return _normalize_utc_datetime(value)
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        cls_name = cls.__name__
        if cls_name not in _TYPES:
            raise ValueError("unsupported archive record type")
        target_cls = _TYPES[cls_name]
        return target_cls(**{f.name: snapshot_recorded_value(getattr(value, f.name)) for f in fields(value)})
    if isinstance(value, tuple):
        return tuple(snapshot_recorded_value(item) for item in value)
    if isinstance(value, list):
        return [snapshot_recorded_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("archive mapping keys must be strings")
        return {
            (key if type(key) is str else str(key)): snapshot_recorded_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (str, int, float, bool)):
        return decode_value(encode_value(value))
    raise ValueError(f"unsupported archive value: {type(value).__name__}")


def recorded_call_key(value: Any) -> Any:
    """Build a deterministic hashable structural key isomorphic to canonical_bytes."""
    kind = type(value)
    if value is None:
        return ("null",)
    if kind is bool:
        return ("bool", value)
    if kind is int:
        return ("int", value)
    if kind is float:
        if math.isnan(value) or math.isinf(value):
            raise ValueError("Out of range float values are not JSON compliant")
        return ("float", value)
    if kind is str:
        return ("str", value)
    if kind is Decimal or isinstance(value, Decimal):
        return ("Decimal", str(value))
    if kind is datetime or isinstance(value, datetime):
        return ("datetime", _format_utc_iso(value))
    if kind is InstrumentId:
        return ("InstrumentId", value.venue.value, value.symbol)
    if kind is Instrument:
        return (
            "Instrument",
            value.id.venue.value,
            value.id.symbol,
            value.base_asset,
            value.quote_asset,
            value.asset_class.value,
            value.market_kind.value,
            value.trading,
            str(value.price_tick),
            str(value.quantity_step),
            str(value.min_quantity),
            str(value.min_notional),
            value.max_leverage,
        )
    if isinstance(value, Enum):
        cls_name = type(value).__name__
        if cls_name not in _TYPES:
            raise ValueError(f"unsupported archive value: {cls_name}")
        return ("Enum", cls_name, recorded_call_key(value.value))
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        cls_name = cls.__name__
        if cls_name not in _TYPES:
            raise ValueError("unsupported archive record type")
        return (
            "dataclass",
            cls_name,
            tuple((fname, recorded_call_key(getattr(value, fname))) for fname in _sorted_field_names(cls)),
        )
    if isinstance(value, tuple):
        return ("tuple", tuple(recorded_call_key(item) for item in value))
    if isinstance(value, list):
        return ("list", tuple(recorded_call_key(item) for item in value))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("archive mapping keys must be strings")
        return ("mapping", tuple((key, recorded_call_key(value[key])) for key in sorted(value)))
    if isinstance(value, (str, int, float, bool)):
        return recorded_call_key(decode_value(encode_value(value)))
    raise ValueError(f"unsupported archive value: {type(value).__name__}")


def iter_canonical_json(value: Any) -> Iterator[str]:
    """Stream canonical JSON tokens matching json.dumps(encode_value(value), sort_keys=True, separators=(',', ':'))."""
    kind = type(value)
    if value is None:
        yield "null"
        return
    if kind is bool:
        yield "true" if value else "false"
        return
    if kind is str:
        yield json.dumps(value, ensure_ascii=True, allow_nan=False)
        return
    if kind is int or kind is float:
        yield json.dumps(value, ensure_ascii=True, allow_nan=False)
        return
    if kind is Decimal:
        if value.is_finite():
            yield f'{{"@":"Decimal","value":"{value}"}}'
        else:
            yield '{"@":"Decimal","value":' + json.dumps(str(value), ensure_ascii=True) + "}"
        return
    if kind is datetime:
        yield f'{{"@":"datetime","value":"{_format_utc_iso(value)}"}}'
        return
    if kind is Candle and (
        type(value.closed) is bool
        and type(value.open) is Decimal
        and type(value.high) is Decimal
        and type(value.low) is Decimal
        and type(value.close) is Decimal
        and type(value.volume) is Decimal
        and type(value.quote_volume) is Decimal
        and type(value.taker_buy_volume) is Decimal
        and type(value.taker_buy_quote_volume) is Decimal
        and type(value.opened_at) is datetime
        and type(value.closed_at) is datetime
        and value.open.is_finite()
        and value.high.is_finite()
        and value.low.is_finite()
        and value.close.is_finite()
        and value.volume.is_finite()
        and value.quote_volume.is_finite()
        and value.taker_buy_volume.is_finite()
        and value.taker_buy_quote_volume.is_finite()
    ):
        closed_str = "true" if value.closed else "false"
        closed_at_str = _format_utc_iso(value.closed_at)
        opened_at_str = _format_utc_iso(value.opened_at)
        yield (
            '{"@":"Candle","fields":{'
            f'"close":{{"@":"Decimal","value":"{value.close}"}},'
            f'"closed":{closed_str},'
            f'"closed_at":{{"@":"datetime","value":"{closed_at_str}"}},'
            f'"high":{{"@":"Decimal","value":"{value.high}"}},'
            f'"low":{{"@":"Decimal","value":"{value.low}"}},'
            f'"open":{{"@":"Decimal","value":"{value.open}"}},'
            f'"opened_at":{{"@":"datetime","value":"{opened_at_str}"}},'
            f'"quote_volume":{{"@":"Decimal","value":"{value.quote_volume}"}},'
            f'"taker_buy_quote_volume":{{"@":"Decimal","value":"{value.taker_buy_quote_volume}"}},'
            f'"taker_buy_volume":{{"@":"Decimal","value":"{value.taker_buy_volume}"}},'
            f'"volume":{{"@":"Decimal","value":"{value.volume}"}}'
            "}}"
        )
        return
    if isinstance(value, Enum):
        yield '{"@":'
        yield json.dumps(type(value).__name__, ensure_ascii=True)
        yield ',"value":'
        yield from iter_canonical_json(value.value)
        yield "}"
        return
    if isinstance(value, Decimal):
        yield '{"@":"Decimal","value":'
        yield json.dumps(str(value), ensure_ascii=True)
        yield "}"
        return
    if isinstance(value, datetime):
        yield f'{{"@":"datetime","value":"{_format_utc_iso(value)}"}}'
        return
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        cls_name = cls.__name__
        if cls_name not in _TYPES:
            raise ValueError("unsupported archive record type")
        yield '{"@":'
        yield json.dumps(cls_name, ensure_ascii=True)
        yield ',"fields":{'
        first = True
        for fname in _sorted_field_names(cls):
            if not first:
                yield ","
            first = False
            yield json.dumps(fname, ensure_ascii=True)
            yield ":"
            yield from iter_canonical_json(getattr(value, fname))
        yield "}}"
        return
    if isinstance(value, tuple):
        yield '{"@":"tuple","items":['
        first = True
        for item in value:
            if not first:
                yield ","
            first = False
            yield from iter_canonical_json(item)
        yield "]}"
        return
    if isinstance(value, list):
        yield "["
        first = True
        for item in value:
            if not first:
                yield ","
            first = False
            yield from iter_canonical_json(item)
        yield "]"
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("archive mapping keys must be strings")
        yield '{"@":"mapping","items":{'
        first = True
        for key in sorted(value):
            if not first:
                yield ","
            first = False
            yield json.dumps(key, ensure_ascii=True, allow_nan=False)
            yield ":"
            yield from iter_canonical_json(value[key])
        yield "}}"
        return
    if isinstance(value, (str, int, float, bool)):
        yield json.dumps(value, ensure_ascii=True, allow_nan=False)
        return
    raise ValueError(f"unsupported archive value: {type(value).__name__}")


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
