"""Research-only capture composition with shared immutable snapshots, no strategy fork."""

from __future__ import annotations

import asyncio
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import (
    CallTape,
    CapturedScan,
    RecordingMacroProvider,
    RecordingMarketData,
    screening_configuration,
    screening_identity,
)
from algotrader.domain.scan_observations import RecordedCall, ScanDecisionJournal, ScanRecord
from algotrader.infrastructure.scan_archive import canonical_bytes

_DATETIME_TYPE = datetime


def _immutable(*args, **kwargs):
    raise TypeError("recorded research snapshot is immutable")


class SnapshotList(list):
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self

    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable
    append = extend = insert = pop = remove = clear = reverse = sort = _immutable


class SnapshotDict(dict):
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self

    __setitem__ = __delitem__ = __ior__ = _immutable
    clear = pop = popitem = setdefault = update = _immutable


def freeze(value):
    """Preserve list/dict wire encoding while sharing only immutable nested values."""
    if value is None or isinstance(value, (str, int, float, bool, Decimal, _DATETIME_TYPE, Enum)):
        return value
    if isinstance(value, (SnapshotList, SnapshotDict)):
        return value
    if isinstance(value, list):
        return SnapshotList(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, dict):
        return SnapshotDict({key: freeze(item) for key, item in value.items()})
    if is_dataclass(value) and not isinstance(value, type):
        if not value.__dataclass_params__.frozen:
            raise TypeError("mutable research domain record")
        replacements = {}
        for field in fields(value):
            before = getattr(value, field.name)
            after = freeze(before)
            if before is not after:
                replacements[field.name] = after
        return replace(value, **replacements) if replacements else value
    raise TypeError("unsupported research snapshot type")


class ImmutableTape(CallTape):
    async def capture(self, method, arguments, operation):
        args = freeze(arguments)
        key = canonical_bytes((method, args))
        index = self._counts.get(key, 0)
        self._counts[key] = index + 1
        position = len(self.calls)
        self.calls.append(RecordedCall(method, index, args, error="incomplete"))
        try:
            result = freeze(await operation())
        except (Exception, asyncio.CancelledError) as exc:
            self.calls[position] = RecordedCall(method, index, args, error=type(exc).__name__)
            raise
        self.calls[position] = RecordedCall(method, index, args, result)
        return result


async def capture_control(market, derivatives, macro, settings, arguments):
    """Compose the unchanged production engine with a research-specific recorder.

    This mirrors capture bookkeeping, never strategy/gates. Regression tests require exact
    typed input encoding, outputs, funnel, cohort and journal against ordinary capture_scan.
    """
    started = datetime.now(UTC)
    identity = await asyncio.to_thread(screening_identity)
    identity["configuration"] = screening_configuration(settings)
    tape = ImmutableTape()
    recorded = RecordingMarketData(market, derivatives, tape)
    journal = ScanDecisionJournal()
    engine = CryptoOpportunityScreener(
        settings, RecordingMacroProvider(macro, tape), evaluated_at=started, journal=journal
    )
    result, failure = [], None
    try:
        result = await engine.screen(
            recorded, derivatives_port=recorded if derivatives is not None else None, **arguments
        )
    except (Exception, asyncio.CancelledError) as exc:
        failure = exc
    record = ScanRecord(
        str(uuid4()),
        started,
        datetime.now(UTC),
        "failed" if failure else "complete",
        identity,
        {**arguments, "derivatives_enabled": derivatives is not None},
        tuple(tape.calls),
        journal.snapshot(),
        tuple(o.as_dict() for o in result),
        dict(engine.scan_counts),
        journal.cohort,
        error=type(failure).__name__ if failure else None,
    )
    return CapturedScan(record, result, failure)
