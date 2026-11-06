"""Scan-local input recording. No network requests beyond the wrapped scan."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import platform
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument, Quote, Venue
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.scan_observations import (
    RecordedCall,
    ScanDecisionJournal,
    ScanRecord,
    decode_value,
    encode_value,
)
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.infrastructure.scan_archive import canonical_bytes


class MacroContextSource(Protocol):
    async def get_context(self) -> MacroSentimentContext: ...


def screening_identity() -> dict[str, Any]:
    source = Path(__file__).resolve().parents[1]
    hashes = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(source.rglob("*.py"))}
    versions = {name: importlib.metadata.version(name) for name in ("atlas-algotrader", "pydantic", "httpx")}
    try:
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=2).decode().strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    return {"policy": SCREENING_POLICY_VERSION, "source_hash": hashlib.sha256(canonical_bytes(hashes)).hexdigest(),
            "source_files": hashes, "python": platform.python_version(), "packages": versions, "git_revision": revision}


def screening_configuration(settings: Settings) -> dict[str, Any]:
    # Only settings consumed by CryptoOpportunityScreener; never dump operator tokens.
    return {"excluded_base_assets": settings.excluded_base_assets,
            "support_resistance_lookback_bars": settings.support_resistance_lookback_bars}


class CallTape:
    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self._counts: dict[bytes, int] = {}
        self.recording_error: str | None = None

    async def capture(self, method: str, arguments: dict[str, Any], operation: Callable[[], Awaitable[Any]]) -> Any:
        try:
            args = decode_value(encode_value(arguments))
            key = canonical_bytes((method, args))
        except Exception as exc:
            self.recording_error = self.recording_error or type(exc).__name__
            args = {}
            key = f"unrecordable:{method}:{len(self.calls)}".encode()
        index = self._counts.get(key, 0)
        self._counts[key] = index + 1
        position = len(self.calls)
        self.calls.append(RecordedCall(method, index, args, error="incomplete"))
        try:
            result = await operation()
        except (Exception, asyncio.CancelledError) as exc:
            self.calls[position] = RecordedCall(method, index, args, error=type(exc).__name__)
            raise
        try:
            copied = decode_value(encode_value(result))
            self.calls[position] = RecordedCall(method, index, args, copied)
        except Exception as exc:
            self.recording_error = self.recording_error or type(exc).__name__
            self.calls[position] = RecordedCall(method, index, args, error="recording_unavailable")
        return result


class RecordingMarketData:
    def __init__(self, market: MarketDataPort, derivatives: DerivativesDataPort | None, tape: CallTape) -> None:
        self.market = market
        self.derivatives = derivatives
        self.tape = tape
        self.venue = getattr(market, "venue", Venue.BINANCE_USDM)

    async def instruments(self) -> Sequence[Instrument]:
        return cast(Sequence[Instrument], await self.tape.capture("instruments", {}, self.market.instruments))

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return cast(Sequence[Quote], await self.tape.capture("quotes", {"instruments": tuple(instruments)}, lambda: self.market.quotes(instruments)))

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        return cast(Sequence[Candle], await self.tape.capture("candles", {"instrument": instrument, "interval": interval, "limit": limit}, lambda: self.market.candles(instrument, interval, limit)))

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        if self.derivatives is None:
            raise ValueError("no derivatives provider")
        provider = self.derivatives
        return cast(DerivativesSnapshot, await self.tape.capture("derivatives_snapshot", {"instrument": instrument}, lambda: provider.derivatives_snapshot(instrument)))


class RecordingMacroProvider:
    def __init__(self, provider: MacroContextSource, tape: CallTape) -> None:
        self.provider, self.tape = provider, tape

    async def get_context(self) -> MacroSentimentContext:
        return cast(MacroSentimentContext, await self.tape.capture("macro", {}, self.provider.get_context))


@dataclass(frozen=True)
class CapturedScan:
    record: ScanRecord
    opportunities: list[CryptoOpportunity]
    failure: BaseException | None = None
    recording_error: str | None = None


async def capture_scan(
    market: MarketDataPort, derivatives: DerivativesDataPort | None,
    macro: MacroContextSource, settings: Settings, arguments: dict[str, Any],
) -> CapturedScan:
    from algotrader.application.crypto_screener import CryptoOpportunityScreener

    scan_id, started = str(uuid4()), datetime.now(UTC)
    metadata_error = None
    try:
        identity = await asyncio.to_thread(screening_identity)
    except Exception as exc:
        metadata_error = type(exc).__name__
        identity = {"recording_error": metadata_error}
    identity["configuration"] = screening_configuration(settings)
    tape = CallTape()
    recorded_market = RecordingMarketData(market, derivatives, tape)
    journal = ScanDecisionJournal()
    screener = CryptoOpportunityScreener(settings, RecordingMacroProvider(macro, tape), evaluated_at=started, journal=journal)
    result: list[CryptoOpportunity] = []
    failure: BaseException | None = None
    try:
        result = await screener.screen(recorded_market, derivatives_port=recorded_market if derivatives is not None else None, **arguments)
    except (Exception, asyncio.CancelledError) as exc:
        failure = exc
    recording_error = metadata_error or tape.recording_error
    status = "failed" if failure else "incomplete" if recording_error else "complete"
    record = ScanRecord(scan_id, started, datetime.now(UTC), status,
        identity, {**arguments, "derivatives_enabled": derivatives is not None}, tuple(tape.calls), journal.snapshot(),
        tuple(opp.as_dict() for opp in result), dict(screener.scan_counts), journal.cohort,
        error=type(failure).__name__ if failure else recording_error)
    return CapturedScan(record, result, failure, recording_error)
