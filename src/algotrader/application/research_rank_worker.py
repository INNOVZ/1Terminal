"""One idempotent rank tick; the supervised job owns the lifetime role lock."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from algotrader.application.rank_collector import RankCaptureWindowExpired, RankCollector
from algotrader.application.research_sessions import ResearchSessions
from algotrader.domain.rank_observations import RankSnapshotRecord
from algotrader.domain.research_sessions import ResearchSession, aware_utc, rank_slot
from algotrader.infrastructure.rank_archive import LocalRankArchive, digest, envelope
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError


class ResearchRankWorker:
    def __init__(self, sessions: ResearchSessions, collector: RankCollector) -> None:
        if collector.root.resolve() != sessions.root:
            raise ValueError("rank worker and collector must share one archive")
        self.sessions, self.collector = sessions, collector

    def _link(self, session: ResearchSession, reservation: dict[str, Any], at: datetime) -> None:
        store = self.sessions.store
        slot = reservation["slot"]
        states = {
            event.details["slot"]: event.kind
            for event in store.events(session.session_id)
            if event.kind.startswith("rank_")
        }
        if states.get(slot) in ("rank_completed", "rank_interrupted", "rank_missed"):
            return
        identifier = reservation["snapshot_id"]
        directory = self.sessions.root / "ranks" / session.parent_id / identifier
        base = {
            "slot": slot,
            "snapshot_id": identifier,
            "scheduled_at": reservation["scheduled_at"],
        }
        if directory.is_symlink():
            raise ArchiveIntegrityError("reserved snapshot symlink refused")
        if not directory.exists():
            store.append(
                session.session_id,
                "ranks",
                "rank_interrupted",
                {
                    **base,
                    "reason": "reserved_without_committed_snapshot",
                },
                at,
            )
            return
        snapshot = LocalRankArchive(self.sessions.root).read_snapshot(session.parent_id, identifier)
        self._validate_snapshot(session, reservation, snapshot)
        latest = min(
            session.endpoint_48h,
            reservation["scheduled_at"] + timedelta(seconds=session.policy.rank_lateness_seconds),
        )
        if not reservation["scheduled_at"] <= snapshot.requested_at <= latest:
            store.append(
                session.session_id,
                "ranks",
                "rank_interrupted",
                {
                    **base,
                    "reason": "snapshot_outside_capture_window",
                    "checksum": digest(envelope("rank_snapshot", snapshot)),
                    "requested_at": snapshot.requested_at,
                },
                at,
            )
            return
        store.append(
            session.session_id,
            "ranks",
            "rank_completed",
            {
                **base,
                "checksum": digest(envelope("rank_snapshot", snapshot)),
                "requested_at": snapshot.requested_at,
                "arrived_at": snapshot.arrived_at,
                "completed_at": snapshot.completed_at,
                "status": snapshot.status,
                "error": snapshot.error,
                "accounting": snapshot.accounting,
            },
            at,
        )

    @staticmethod
    def _validate_snapshot(
        session: ResearchSession, reservation: dict[str, Any], snapshot: RankSnapshotRecord
    ) -> None:
        if (
            snapshot.parent_id != session.parent_id
            or snapshot.parent_checksum != session.parent_checksum
            or snapshot.cohort != session.cohort
            or snapshot.policy != asdict(session.rank_policy)
            or snapshot.identity != session.identity["rank"]
        ):
            raise ArchiveIntegrityError("reserved snapshot differs from frozen session/slot")

    def _recover(self, session: ResearchSession, now: datetime) -> None:
        store = self.sessions.store
        slots = store.reserved_rank_slots(session.session_id)
        events = store.events(session.session_id)
        terminal_slots = {
            event.details["slot"]
            for event in events
            if event.kind in ("rank_completed", "rank_interrupted", "rank_missed")
        }
        for event in events:
            if event.kind == "rank_reserved" and event.details["slot"] not in slots:
                raise ArchiveIntegrityError("rank event has no durable reservation")
        for slot in slots:
            if slot in terminal_slots:
                continue
            reservation = store.rank_reservation(session.session_id, slot)
            if reservation is None:
                raise ArchiveIntegrityError("rank reservation disappeared")
            self._link(session, reservation, now)

    def _stopped(self, session_id: str) -> bool:
        return any(
            event.kind == "stop_requested" for event in self.sessions.store.events(session_id)
        )

    async def tick(self, session_id: str, *, now: datetime) -> dict[str, Any]:
        now = aware_utc(now)
        session = self.sessions.store.read(session_id)
        if (
            self.collector.policy != session.rank_policy
            or self.collector.identity != session.identity["rank"]
        ):
            self.sessions.record_state(session_id, "ranks", "source_mismatch", at=now)
            return self.sessions.status(session_id, now=now)
        guard = self.sessions.guard(session_id, now=now)
        if not guard["ok"]:
            if guard["reason"] not in ("stopped", "complete"):
                self.sessions.record_state(session_id, "ranks", guard["reason"], at=now)
            return self.sessions.status(session_id, now=now)
        self._recover(session, now)
        status = self.sessions.status(session_id, now=now)
        decision = rank_slot(session, now, frozenset(status["rank_slots"]))
        if decision["missed_slots"]:
            self.sessions.store.append_many(
                session_id,
                "ranks",
                tuple(
                    (
                        "rank_missed",
                        {
                            "slot": slot,
                            "scheduled_at": session.anchor
                            + timedelta(seconds=slot * session.policy.rank_interval_seconds),
                            "reason": "missed_scheduled_window",
                        },
                        now,
                    )
                    for slot in decision["missed_slots"]
                ),
            )
        slot = decision["eligible_slot"]
        if slot is None:
            return self.sessions.status(session_id, now=now)
        # The reservation survives a crash before the first network call or journal event.
        reservation = self.sessions.store.reserve_rank(session_id, slot, now)
        self.sessions.store.append(session_id, "ranks", "rank_reserved", reservation, now)
        acquisition = asyncio.create_task(
            self.collector.collect(
                session.parent_id,
                snapshot_id=reservation["snapshot_id"],
                earliest_requested_at=reservation["scheduled_at"],
                latest_requested_at=min(
                    session.endpoint_48h,
                    reservation["scheduled_at"]
                    + timedelta(seconds=session.policy.rank_lateness_seconds),
                ),
            )
        )
        stopping = False
        try:
            while not acquisition.done():
                done, _ = await asyncio.wait({acquisition}, timeout=0.2)
                if not done and self._stopped(session_id):
                    stopping = True
                    acquisition.cancel()
                    break
            await acquisition
        except asyncio.CancelledError:
            acquisition.cancel()
            # Do not abandon the collector's cancellation-safe durable commit.
            while not acquisition.done():
                try:
                    await asyncio.shield(acquisition)
                except asyncio.CancelledError:
                    continue
            self._link(session, reservation, now)
            if not stopping:
                raise
        except RankCaptureWindowExpired:
            self._link(session, reservation, now)
        except Exception:
            self._link(session, reservation, now)
            raise
        else:
            self._link(session, reservation, now)
        result = self.sessions.status(session_id, now=now)
        if not result["terminal"]:
            terminal = result["rank_slots"][slot]
            state = "running" if terminal.get("status") == "complete" else "degraded"
            self.sessions.record_state(session_id, "ranks", state, at=now, slot=slot)
        return self.sessions.status(session_id, now=now)
