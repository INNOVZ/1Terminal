"""Offline operator operations and fail-closed research acquisition guards."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from algotrader.application.rank_collector import RankCollector
from algotrader.application.shadow_observer import _measurement_identity
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.rank_observations import RankPolicy, normalize_ranks, validate_cohort
from algotrader.domain.rank_timeline import membership_timeline
from algotrader.domain.research_sessions import (
    ResearchPolicy,
    ResearchSession,
    Role,
    aware_utc,
    reduce_session,
)
from algotrader.domain.shadow_benchmark import freeze_benchmark_arms
from algotrader.domain.shadow_discovery import classify_shadow
from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.rank_archive import digest
from algotrader.infrastructure.research_sessions import LocalResearchSessions
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError, LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive


class ResearchSessions:
    def __init__(self, root: Path) -> None:
        self.store = LocalResearchSessions(root)
        self.root = self.store.root

    def semantic_identity(self, child: ShadowRunRecord) -> dict[str, Any]:
        # No HTTP construction: collector initialization only freezes source provenance.
        package = Path(__file__).parents[1]
        paths = (
            package / "application" / "shadow_benchmark.py",
            Path(freeze_benchmark_arms.__code__.co_filename),
            Path(membership_timeline.__code__.co_filename),
            Path(normalize_ranks.__code__.co_filename),
            Path(classify_shadow.__code__.co_filename),
        )
        return {
            "rank": RankCollector(self.root, RankPolicy()).identity,
            "report_source": digest(
                tuple((str(path.relative_to(package)), path.read_text()) for path in paths)
            ),
            "measurement": _measurement_identity(
                child.parent_checksum, child.shadow_id, child.completed_at
            ),
            "runner_policy": "research-runner-v1",
            "report_policy": "matched-anchor-benchmark-v1",
        }

    def _child(self, shadow_id: str) -> ShadowRunRecord:
        child = LocalShadowArchive(self.root).read_run(shadow_id)
        parent = LocalScanArchive(self.root).read_scan(child.parent_id)
        validate_cohort(child.cohort, RankPolicy())
        if child.status != "complete" or parent.status != "complete":
            raise ValueError("research enrollment requires complete child and parent")
        if child.parent_checksum != digest(parent) or child.cohort != parent.cohort:
            raise ArchiveIntegrityError("research parent/cohort mismatch")
        aware_utc(child.started_at)
        aware_utc(child.completed_at)
        if child.started_at < parent.completed_at or child.completed_at < child.started_at:
            raise ArchiveIntegrityError("invalid research decision chronology")
        return child

    def start(self, shadow_id: str, *, now: datetime) -> ResearchSession:
        now = aware_utc(now)
        with self.store.enrollment_lock():
            existing = tuple(self.store.read(identifier) for identifier in self.store.session_ids())
            for session in existing:
                if session.shadow_id == shadow_id:
                    return session
            for session in existing:
                if not reduce_session(session, self.store.events(session.session_id), now)[
                    "terminal"
                ]:
                    raise ValueError("an active research session already exists")
            child = self._child(shadow_id)
            anchor = outcome_anchor(child.completed_at)
            if now < child.completed_at or now > anchor + timedelta(hours=48):
                raise ValueError("enrollment is outside original research window")
            session = ResearchSession(
                str(uuid4()),
                child.parent_id,
                child.shadow_id,
                child.parent_checksum,
                digest(
                    {
                        "kind": "shadow_run",
                        "record": {
                            name: getattr(child, name) for name in child.__dataclass_fields__
                        },
                    }
                ),
                child.cohort,
                now,
                anchor,
                anchor + timedelta(hours=24),
                anchor + timedelta(hours=48),
                ResearchPolicy(),
                RankPolicy(),
                self.semantic_identity(child),
            )
            self.store.write(session)
            return session

    def status(self, session_id: str, *, now: datetime) -> dict[str, Any]:
        session = self.store.read(session_id)
        events = self.store.events(session_id)
        result = reduce_session(session, events, now)
        result["accounting"] = {}
        for role in ("ranks", "outcomes"):
            accounted = [
                event.details["accounting"]
                for event in events
                if event.role == role
                and event.kind in ("rank_completed", "outcome_pass")
                and "accounting" in event.details
            ]
            result["accounting"][role] = {
                "actual_attempts": sum(item["attempts"] for item in accounted),
                "latest": accounted[-1] if accounted else None,
            }
        result["accounting_scope"] = "session_research_attempts_not_account_wide_quota"
        result["outcome_jobs"] = {
            horizon: next(
                (
                    event.details
                    for event in reversed(events)
                    if event.kind == "outcome_pass" and event.details["horizon_hours"] == horizon
                ),
                None,
            )
            for horizon in (24, 48)
        }
        if not result["terminal"]:
            guard = self.guard(session_id, now=now)
            result["guard"] = guard
            if not guard["ok"]:
                result["state"] = guard["reason"]
        result["definition"] = {
            "parent_id": session.parent_id,
            "shadow_id": session.shadow_id,
            "anchor": session.anchor,
            "enrolled_at": session.enrolled_at,
            "endpoint_24h": session.endpoint_24h,
            "endpoint_48h": session.endpoint_48h,
            "cohort_size": len(session.cohort),
            "policy": asdict(session.policy),
        }
        return result

    def stop(self, session_id: str, *, now: datetime) -> None:
        aware_utc(now)
        with self.store.journal_lock("control"):
            if not any(event.kind == "stop_requested" for event in self.store.events(session_id)):
                self.store._append_locked(session_id, "control", "stop_requested", {}, now)

    @staticmethod
    def archive_bytes(root: Path) -> int:
        total = 0

        def fail(error: OSError) -> None:
            raise error

        for directory, subdirs, files in os.walk(root, followlinks=False, onerror=fail):
            for name in (*subdirs, *files):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ArchiveIntegrityError("research archive symlink refused")
                if name in files:
                    total += path.stat().st_size
        return total

    def guard(self, session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = aware_utc(now if now is not None else datetime.now(UTC))
        session = self.store.read(session_id)
        events = self.store.events(session_id)
        state = reduce_session(session, events, session.enrolled_at)
        result: dict[str, Any] = {"ok": False, "reason": None, "next_allowed_at": None}
        if state["terminal"]:
            return {**result, "reason": state["state"]}
        child = self._child(session.shadow_id)
        checksum = digest(
            {
                "kind": "shadow_run",
                "record": {name: getattr(child, name) for name in child.__dataclass_fields__},
            }
        )
        if (
            checksum != session.shadow_checksum
            or child.parent_checksum != session.parent_checksum
            or child.cohort != session.cohort
        ):
            raise ArchiveIntegrityError("session frozen evidence mismatch")
        if self.semantic_identity(child) != session.identity:
            return {**result, "reason": "source_mismatch"}
        if (
            shutil.disk_usage(self.root).free < session.policy.min_free_bytes
            or self.archive_bytes(self.root) >= session.policy.max_archive_bytes
        ):
            return {**result, "reason": "storage_blocked"}
        cooldowns = [event for event in events if event.kind == "cooldown"]
        if any(event.details.get("blocked") is True for event in cooldowns):
            return {
                **result,
                "reason": "degraded",
                "detail": "cooldown_unrepresentable_operator_attention",
            }
        until = max((aware_utc(event.details["until"]) for event in cooldowns), default=None)
        if until is not None and now < until:
            return {
                **result,
                "reason": "degraded",
                "detail": "exchange_cooldown",
                "next_allowed_at": until,
            }
        return {**result, "ok": True}

    def record_response(
        self, session_id: str, role: Role, response: dict[str, Any], *, at: datetime
    ) -> None:
        """Persist a cooperative gate immediately, before client retry handling."""
        at = aware_utc(at)
        session = self.store.read(session_id)
        status, weight = response.get("status_code"), response.get("observed_weight_1m")
        high_weight = type(weight) is int and weight >= 2150
        if status not in (418, 429) and not high_weight:
            return
        elapsed = (at - session.anchor).total_seconds()
        next_slot = int(elapsed // session.policy.rank_interval_seconds) + 1
        blocked = False
        try:
            until = session.anchor + timedelta(
                seconds=next_slot * session.policy.rank_interval_seconds
            )
        except OverflowError:
            until, blocked = at, True
        raw = response.get("retry_after")
        try:
            delay = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            delay = Decimal("NaN")
        if status in (418, 429) and delay.is_finite() and delay > 0:
            available = datetime.max.replace(tzinfo=UTC) - at
            max_microseconds = (
                available.days * 86400 + available.seconds
            ) * 1000000 + available.microseconds
            if delay > Decimal(max_microseconds) / 1000000:
                blocked = True
            else:
                try:
                    micros = max(
                        1, int((delay * 1000000).to_integral_value(rounding=ROUND_CEILING))
                    )
                    until = at + timedelta(microseconds=micros)
                except (OverflowError, InvalidOperation):
                    blocked = True
        elif status not in (418, 429):
            until = at
        if high_weight and not blocked:
            try:
                until = max(until, at + timedelta(seconds=60))
            except OverflowError:
                blocked = True
        self.store.append(
            session_id,
            role,
            "cooldown",
            {
                "blocked": blocked,
                "until": None if blocked else until,
                "status_code": status,
                "observed_weight_1m": weight,
                "retry_after": str(raw),
            },
            at,
        )

    def record_state(
        self, session_id: str, role: Role, state: str, *, at: datetime, slot: int | None = None
    ) -> None:
        with self.store.journal_lock(role):
            previous = [
                event
                for event in self.store.events(session_id)
                if event.role == role and event.kind == "state"
            ]
            details = {"state": state, "slot": slot}
            if not previous or previous[-1].details != details:
                self.store._append_locked(session_id, role, "state", details, at)
