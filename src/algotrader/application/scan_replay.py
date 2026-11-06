"""Offline replay through the production screener, with no current-data fallback."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from algotrader.application.scan_recording import screening_identity
from algotrader.config import Settings
from algotrader.domain.models import Venue
from algotrader.domain.scan_observations import (
    SCHEMA_VERSION,
    RecordedCall,
    ScanDecisionJournal,
    ScanRecord,
)
from algotrader.infrastructure.scan_archive import canonical_bytes


class ReplayMismatch(ValueError):
    pass


class ReplayTape:
    def __init__(self, calls: tuple[RecordedCall, ...]) -> None:
        self.calls: dict[bytes, list[RecordedCall]] = {}
        self.missing = False
        for call in calls:
            self.calls.setdefault(canonical_bytes((call.method, call.arguments)), []).append(call)
        for group in self.calls.values():
            group.sort(key=lambda call: call.index)

    def take(self, method: str, arguments: dict[str, Any]) -> Any:
        group = self.calls.get(canonical_bytes((method, arguments)))
        if not group:
            self.missing = True
            raise ReplayMismatch("missing recorded input")
        call = group.pop(0)
        if call.error:
            if call.error == "CancelledError":
                raise asyncio.CancelledError
            if call.error == "TimeoutError":
                raise TimeoutError("recorded provider timeout")
            raise RuntimeError("recorded provider failure: " + call.error)
        return call.result

    def assert_consumed(self) -> None:
        if self.missing or any(self.calls.values()):
            raise ReplayMismatch("recorded input calls did not match replay")


class ReplayMarket:
    venue = Venue.BINANCE_USDM

    def __init__(self, tape: ReplayTape) -> None:
        self.tape = tape

    async def instruments(self) -> Any:
        return self.tape.take("instruments", {})

    async def quotes(self, instruments: Any) -> Any:
        return self.tape.take("quotes", {"instruments": tuple(instruments)})

    async def candles(self, instrument: Any, interval: str, limit: int) -> Any:
        return self.tape.take("candles", {"instrument": instrument, "interval": interval, "limit": limit})

    async def derivatives_snapshot(self, instrument: Any) -> Any:
        return self.tape.take("derivatives_snapshot", {"instrument": instrument})

    async def get_context(self) -> Any:
        return self.tape.take("macro", {})


@dataclass(frozen=True)
class ReplayReport:
    matches: bool
    counts: dict[str, int]


async def replay_scan(record: ScanRecord) -> ReplayReport:
    from algotrader.application.crypto_screener import CryptoOpportunityScreener

    if record.schema_version != SCHEMA_VERSION or record.status != "complete":
        raise ReplayMismatch("only supported, complete scans can be replayed")
    current = await asyncio.to_thread(screening_identity)
    for key in ("policy", "source_hash", "python", "packages"):
        if record.identity.get(key) != current[key]:
            raise ReplayMismatch("replay identity mismatch: " + key)
    settings = Settings(**{**record.identity["configuration"], "_env_file": None})
    tape = ReplayTape(record.inputs)
    market = ReplayMarket(tape)
    journal = ScanDecisionJournal()
    screener = CryptoOpportunityScreener(settings, market, evaluated_at=record.started_at, journal=journal)
    arguments = dict(record.request)
    enabled = arguments.pop("derivatives_enabled")
    result = await screener.screen(market, derivatives_port=market if enabled else None, **arguments)
    tape.assert_consumed()
    if (tuple(item.as_dict() for item in result) != record.opportunities or screener.scan_counts != record.counts
        or journal.snapshot() != record.decisions or journal.cohort != record.cohort):
        raise ReplayMismatch("replayed decisions or funnel counts differ")
    return ReplayReport(True, dict(screener.scan_counts))
