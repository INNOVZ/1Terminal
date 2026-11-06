"""Original-cohort forward observations with a child-specific anchor and cache identity."""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from algotrader.application.forward_observer import MinuteHistoryPort
from algotrader.domain import forward_outcomes
from algotrader.domain.forward_outcomes import (
    ForwardObservation,
    diagnose_touches,
    evaluate_forward_path,
    outcome_anchor,
    rank_cohort,
)
from algotrader.domain.models import Candle, Direction
from algotrader.infrastructure.scan_archive import (
    ArchiveIntegrityError,
    LocalScanArchive,
    canonical_bytes,
)
from algotrader.infrastructure.shadow_archive import LocalShadowArchive


def _measurement_identity(parent_checksum: str, shadow_id: str, completed_at: datetime) -> str:
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(forward_outcomes.__file__).read_bytes()
        + canonical_bytes((parent_checksum, shadow_id, completed_at))
    ).hexdigest()


class ShadowObserver:
    def __init__(
        self, archive: LocalShadowArchive, market: MinuteHistoryPort, *, pace_seconds: float = 0.2
    ) -> None:
        if not math.isfinite(pace_seconds) or pace_seconds < 0:
            raise ValueError("invalid observation pacing")
        self.archive, self.market, self.pace_seconds = archive, market, pace_seconds

    async def observe(
        self,
        shadow_id: str,
        horizon_hours: int,
        max_symbols: int,
        *,
        now: datetime | None = None,
        symbol_order: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if horizon_hours not in (24, 48) or not 1 <= max_symbols <= 1000:
            raise ValueError("horizon must be 24/48h and max_symbols 1..1000")
        record = await asyncio.to_thread(self.archive.read_run, shadow_id)
        parent = await asyncio.to_thread(
            LocalScanArchive(self.archive.root).read_scan, record.parent_id
        )
        if record.status != "complete" or parent.status != "complete":
            raise ValueError("only complete child and parent can be observed")
        if (
            record.parent_checksum != hashlib.sha256(canonical_bytes(parent)).hexdigest()
            or record.cohort != parent.cohort
        ):
            raise ArchiveIntegrityError("child parent/cohort identity mismatch")
        if len(record.cohort) > max_symbols:
            raise ValueError("cohort exceeds explicit run limit")
        if len({inst.base_asset for inst in record.cohort}) != len(record.cohort):
            raise ArchiveIntegrityError("noncanonical child cohort")
        cohort = record.cohort
        if symbol_order is not None:
            instruments = {inst.id.symbol: inst for inst in cohort}
            if len(symbol_order) != len(cohort) or set(symbol_order) != set(instruments):
                raise ValueError("outcome traversal must be an exact whole-cohort permutation")
            cohort = tuple(instruments[symbol] for symbol in symbol_order)
        now = now or datetime.now(UTC)
        if now.utcoffset() is None:
            raise ValueError("observation clock must be timezone-aware")
        anchor = outcome_anchor(record.completed_at)
        end = anchor + timedelta(hours=horizon_hours)
        identity = await asyncio.to_thread(
            _measurement_identity, record.parent_checksum, record.shadow_id, record.completed_at
        )
        candidates = {item["symbol"]: item for item in record.results if item.get("qualified")}
        observations: dict[str, ForwardObservation] = {}
        selected: dict[str, str] = {}
        for instrument in cohort:
            symbol = instrument.id.symbol
            attempts = await asyncio.to_thread(
                self.archive.outcome_attempts, shadow_id, horizon_hours, symbol
            )
            compatible = [
                attempt for attempt in attempts if attempt.get("measurement_identity") == identity
            ]
            for attempt in compatible:
                if (
                    attempt.get("shadow_id") != shadow_id
                    or attempt.get("symbol") != symbol
                    or attempt.get("anchor") != anchor
                    or attempt.get("horizon_hours") != horizon_hours
                ):
                    raise ArchiveIntegrityError("shadow outcome identity mismatch")
            complete = [
                attempt for attempt in compatible if attempt["observation"]["status"] == "complete"
            ]
            if complete:
                attempt = complete[-1]
                observations[symbol] = ForwardObservation(**attempt["observation"])
                selected[symbol] = attempt["attempt_id"]
                continue
            candidate = candidates.get(symbol)
            direction = Direction(candidate["direction"]) if candidate else None
            candles: tuple[Candle, ...] = ()
            if now < end:
                observation = evaluate_forward_path(
                    anchor=anchor, horizon_hours=horizon_hours, candles=(), now=now
                )
            else:
                try:
                    candles = tuple(
                        await self.market.minute_candles_between(instrument, anchor, end)
                    )
                    observation = evaluate_forward_path(
                        anchor=anchor,
                        horizon_hours=horizon_hours,
                        candles=candles,
                        now=now,
                        direction=direction,
                    )
                except Exception as exc:
                    observation = ForwardObservation(
                        "unavailable", anchor, horizon_hours, reason=type(exc).__name__
                    )
                await asyncio.sleep(self.pace_seconds)
            touches = None
            if candidate and observation.status == "complete":
                touches = diagnose_touches(
                    [
                        bar
                        for bar in candles
                        if anchor <= bar.opened_at < end and bar.closed and bar.closed_at <= now
                    ],
                    direction=Direction(candidate["direction"]),
                    entry=Decimal(str(candidate["suggested_entry"])),
                    stop=Decimal(str(candidate["suggested_stop"])),
                    targets=tuple(Decimal(str(value)) for value in candidate["suggested_targets"]),
                )
            attempt_id = str(uuid4())
            payload = {
                "shadow_id": shadow_id,
                "symbol": symbol,
                "attempt_id": attempt_id,
                "horizon_hours": horizon_hours,
                "anchor": anchor,
                "observed_at": now,
                "measurement_identity": identity,
                "observation": observation.as_dict(),
                "candles": candles,
                "touches": touches,
            }
            await asyncio.to_thread(
                self.archive.write_outcome, shadow_id, horizon_hours, symbol, attempt_id, payload
            )
            observations[symbol], selected[symbol] = observation, attempt_id
        baseline = tuple(item["symbol"] for item in parent.opportunities)
        summary = {
            **asdict(rank_cohort(observations)),
            "shadow_id": shadow_id,
            "parent_id": parent.scan_id,
            "anchor": anchor,
            "parent_anchor": outcome_anchor(parent.completed_at),
            "parent_lag_seconds": (record.completed_at - parent.completed_at).total_seconds(),
            "horizon_hours": horizon_hours,
            "observed_at": now,
            "attempts": selected,
            "measurement_identity": identity,
            "comparison": "staggered_shadow",
            "qualified_symbols": tuple(candidates),
            "baseline_symbols": baseline,
            "overlap_symbols": tuple(sorted(set(candidates) & set(baseline))),
            "additional_qualified_symbols": tuple(sorted(set(candidates) - set(baseline))),
            "interpretation": "price_observations_not_trading_returns",
            "validation_status": "uncalibrated_research",
        }
        summary_id = str(uuid4())
        await asyncio.to_thread(
            self.archive.write_outcome,
            shadow_id,
            horizon_hours,
            "_SUMMARIES",
            summary_id,
            {**summary, "attempt_id": summary_id},
        )
        return summary
