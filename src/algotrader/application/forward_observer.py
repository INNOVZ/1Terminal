"""Resumable observations of the original scan cohort, without trading semantics."""
from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from algotrader.domain import forward_outcomes
from algotrader.domain.forward_outcomes import (
    ForwardObservation,
    diagnose_touches,
    evaluate_forward_path,
    outcome_anchor,
    rank_cohort,
)
from algotrader.domain.models import Candle, Direction, Instrument
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError, LocalScanArchive

OUTCOME_POLICY_VERSION = "fixed-minute-v1"


def _measurement_identity() -> str:
    return hashlib.sha256(Path(forward_outcomes.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest()


class MinuteHistoryPort(Protocol):
    async def minute_candles_between(self, instrument: Instrument, start: datetime, end: datetime) -> Sequence[Candle]: ...


class ForwardObserver:
    def __init__(self, archive: LocalScanArchive, market: MinuteHistoryPort, *, pace_seconds: float = 0.2) -> None:
        if not math.isfinite(pace_seconds) or pace_seconds < 0:
            raise ValueError("invalid observation pacing")
        self.archive, self.market, self.pace_seconds = archive, market, pace_seconds

    async def observe(self, scan_id: str, horizon_hours: int, max_symbols: int, *, now: datetime | None = None) -> dict[str, Any]:
        if horizon_hours not in (24, 48) or not 1 <= max_symbols <= 1000:
            raise ValueError("horizon must be 24/48h and max_symbols 1..1000")
        record = await asyncio.to_thread(self.archive.read_scan, scan_id)
        if record.status != "complete":
            raise ValueError("cannot observe an incomplete/failed scan")
        if len(record.cohort) > max_symbols:
            raise ValueError(f"cohort contains {len(record.cohort)} symbols, exceeding the explicit run limit {max_symbols}")
        if len({inst.base_asset for inst in record.cohort}) != len(record.cohort):
            raise ArchiveIntegrityError("scan cohort is not canonical per base asset")
        now = now or datetime.now(UTC)
        if now.utcoffset() is None:
            raise ValueError("observation time must be timezone-aware")
        anchor = outcome_anchor(record.completed_at)
        end = anchor + timedelta(hours=horizon_hours)
        measurement_identity = await asyncio.to_thread(_measurement_identity)
        observations: dict[str, ForwardObservation] = {}
        selected_attempts: dict[str, str] = {}
        candidates = {str(item["symbol"]): item for item in record.opportunities}
        for instrument in record.cohort:
            symbol = instrument.id.symbol
            attempts = await asyncio.to_thread(self.archive.outcome_attempts, scan_id, horizon_hours, symbol)
            compatible = [item for item in attempts if item.get("measurement_identity") == measurement_identity]
            for item in compatible:
                if (item.get("scan_id") != scan_id or item.get("symbol") != symbol
                    or item.get("horizon_hours") != horizon_hours or item.get("anchor") != anchor):
                    raise ArchiveIntegrityError("outcome attempt identity mismatch")
            completed = [item for item in compatible if item["observation"]["status"] == "complete"]
            if completed:
                selected = completed[-1]
                observations[symbol] = ForwardObservation(**selected["observation"])
                selected_attempts[symbol] = selected["attempt_id"]
                continue
            candidate = candidates.get(symbol)
            direction = Direction(candidate["direction"]) if candidate is not None else None
            candles: Sequence[Candle] = ()
            if now < end:
                observation = evaluate_forward_path(anchor=anchor, horizon_hours=horizon_hours, candles=(), now=now)
            else:
                try:
                    candles = await self.market.minute_candles_between(instrument, anchor, end)
                    observation = evaluate_forward_path(anchor=anchor, horizon_hours=horizon_hours, candles=candles, now=now, direction=direction)
                except Exception as exc:
                    observation = ForwardObservation("unavailable", anchor, horizon_hours, reason=type(exc).__name__)
                await asyncio.sleep(self.pace_seconds)
            touches = None
            if candidate is not None and observation.status == "complete":
                touches = diagnose_touches([bar for bar in candles if anchor <= bar.opened_at < end and bar.closed and bar.closed_at <= now], direction=Direction(candidate["direction"]),
                    entry=Decimal(str(candidate["suggested_entry"])), stop=Decimal(str(candidate["suggested_stop"])),
                    targets=tuple(Decimal(str(target)) for target in candidate["suggested_targets"]))
            attempt_id = str(uuid4())
            payload = {"attempt_id": attempt_id, "scan_id": scan_id, "symbol": symbol,
                "horizon_hours": horizon_hours, "anchor": anchor, "observed_at": now,
                "outcome_policy_version": OUTCOME_POLICY_VERSION, "measurement_identity": measurement_identity,
                "observation": observation.as_dict(), "candles": tuple(candles), "touches": touches}
            await asyncio.to_thread(self.archive.write_outcome, scan_id, horizon_hours, symbol, attempt_id, payload)
            observations[symbol] = observation
            selected_attempts[symbol] = attempt_id
        summary = asdict(rank_cohort(observations))
        summary.update({"scan_id": scan_id, "horizon_hours": horizon_hours, "anchor": anchor,
            "observed_at": now, "outcome_policy_version": OUTCOME_POLICY_VERSION,
            "measurement_identity": measurement_identity, "attempts": selected_attempts,
            "attempt_selection": "latest compatible complete, otherwise this invocation's attempt",
            "published_symbols": tuple(candidates), "interpretation": "price_observations_not_trading_returns"})
        await asyncio.to_thread(self.archive.write_summary, scan_id, horizon_hours, str(uuid4()), summary)
        return summary
