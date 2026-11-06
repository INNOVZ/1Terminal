"""One bounded public bulk ticker observation; no scoring or publication."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.shadow_budget import ShadowBudget
from algotrader.domain.rank_observations import (
    RankPolicy,
    RankSnapshotRecord,
    normalize_ranks,
    validate_cohort,
)
from algotrader.domain.shadow_discovery import ShadowPolicy, classify_shadow
from algotrader.infrastructure.rank_archive import LocalRankArchive, aware, digest
from algotrader.infrastructure.scan_archive import (
    ArchiveIntegrityError,
    LocalScanArchive,
    validated_id,
)


class RankMarketPort(Protocol):
    snapshot_started_at: datetime | None
    snapshot_completed_at: datetime | None

    async def decision_tickers(self) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


class RankCaptureWindowExpired(RuntimeError):
    """No HTTP was authorized before the slot's original request cutoff."""


class RankCollector:
    def __init__(
        self,
        root: Path,
        policy: RankPolicy,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        market_factory: Callable[[ShadowBudget], RankMarketPort] | None = None,
    ) -> None:
        self.root, self.policy = root, policy
        self.clock, self.monotonic = clock, monotonic
        self.factory = market_factory or ShadowMarketData
        # Freeze provenance before the invocation's cancellable acquisition/commit lifecycle.
        self.identity = {
            "collector_source": digest(Path(__file__).read_text()),
            "normalizer_source": digest(Path(normalize_ranks.__code__.co_filename).read_text()),
            "classifier_source": digest(Path(classify_shadow.__code__.co_filename).read_text()),
            "provider": "binance_usdm_bulk_rolling_24h",
        }

    async def collect(
        self,
        scan_id: str,
        *,
        snapshot_id: str | None = None,
        earliest_requested_at: datetime | None = None,
        latest_requested_at: datetime | None = None,
    ) -> RankSnapshotRecord:
        if latest_requested_at is not None:
            aware(latest_requested_at)
        if earliest_requested_at is not None:
            aware(earliest_requested_at)
        if (
            earliest_requested_at is not None
            and latest_requested_at is not None
            and earliest_requested_at > latest_requested_at
        ):
            raise ValueError("invalid rank capture window")
        identifier = validated_id(snapshot_id) if snapshot_id is not None else str(uuid4())
        parent = await asyncio.to_thread(LocalScanArchive(self.root).read_scan, scan_id)
        if parent.status != "complete":
            raise ValueError("only complete parents can be rank-observed")
        validate_cohort(parent.cohort, self.policy)
        if snapshot_id is not None and (self.root / "ranks" / parent.scan_id / identifier).exists():
            existing = await asyncio.to_thread(
                LocalRankArchive(self.root).read_snapshot, parent.scan_id, identifier
            )
            if (
                existing.parent_checksum != digest(parent)
                or existing.cohort != parent.cohort
                or existing.policy != asdict(self.policy)
                or existing.identity != self.identity
            ):
                raise ArchiveIntegrityError("reserved rank snapshot provenance mismatch")
            return existing
        requested = self.clock()
        aware(requested)
        if (latest_requested_at is not None and requested > latest_requested_at) or (
            earliest_requested_at is not None and requested < earliest_requested_at
        ):
            raise RankCaptureWindowExpired("rank request window expired before acquisition")
        budget = ShadowBudget(
            ShadowPolicy(
                max_attempts=self.policy.max_attempts, deadline_seconds=self.policy.deadline_seconds
            ),
            monotonic=self.monotonic,
        )
        market: RankMarketPort | None = None
        rows: tuple[dict[str, Any], ...] = ()
        normalized: dict[str, Any] = {}
        arrived = None
        error = None
        status = "failed"
        cancelled = False
        try:
            market = self.factory(budget)
            async with asyncio.timeout(budget.remaining_seconds):
                rows = tuple(await market.decision_tickers())
                arrived = self.clock()
                normalized = normalize_ranks(parent.cohort, rows, arrived, self.policy)
                status = "complete" if normalized["complete"] else "partial"
        except asyncio.CancelledError:
            cancelled, error = True, "CancelledError"
        except Exception as exc:
            error = type(exc).__name__
        finally:
            if market is not None:
                try:
                    async with asyncio.timeout(budget.remaining_seconds):
                        await market.close()
                except asyncio.CancelledError:
                    cancelled, error, status = True, "CancelledError", "failed"
                except Exception as exc:
                    error, status = error or type(exc).__name__, "failed"
        if status == "failed":
            rows, normalized = (), {}
        completed = self.clock()
        record = RankSnapshotRecord(
            snapshot_id=identifier,
            parent_id=parent.scan_id,
            parent_checksum=digest(parent),
            cohort=parent.cohort,
            cohort_hash=digest(parent.cohort),
            policy=asdict(self.policy),
            identity=self.identity,
            rows=rows,
            normalized=normalized,
            requested_at=requested,
            arrived_at=arrived,
            completed_at=completed,
            accounting=budget.snapshot(),
            status=status,
            error=error,
            provider_requested_at=market.snapshot_started_at if market else None,
            provider_finished_at=market.snapshot_completed_at if market else None,
        )
        # The frozen terminal record is the commit boundary: cancellation must not orphan it.
        commit = asyncio.create_task(
            asyncio.to_thread(LocalRankArchive(self.root).write_snapshot, record)
        )
        while True:
            try:
                await asyncio.shield(commit)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError()
        return record
