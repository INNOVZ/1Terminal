"""Checksummed research journals and POSIX ownership, separate from core state."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.research_sessions import (
    ROLES,
    ResearchEvent,
    ResearchPolicy,
    ResearchSession,
    Role,
    aware_utc,
    canonical_id,
    reduce_session,
)
from algotrader.infrastructure.scan_archive import (
    ArchiveConflict,
    ArchiveIntegrityError,
    LocalScanArchive,
)


class LocalResearchSessions:
    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise ArchiveIntegrityError("research root cannot be a symlink")
        self.root = root.resolve()
        self._store = LocalScanArchive(self.root)

    def _safe(self, path: Path) -> Path:
        if self.root.is_symlink():
            raise ArchiveIntegrityError("research root cannot be a symlink")
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ArchiveIntegrityError("research path escapes root") from exc
        current = self.root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ArchiveIntegrityError("research archive symlink refused")
        return path

    def _directory(self, session_id: str) -> Path:
        return self._safe(self.root / "sessions" / canonical_id(session_id))

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        self._safe(path)
        self._store._write(path, payload)

    def _read(self, path: Path) -> dict[str, Any]:
        self._safe(path)
        for name in ("payload.json", "manifest.json"):
            target = self._safe(path / name)
            if target.exists() and target.stat().st_size > 16 * 1024**2:
                raise ArchiveIntegrityError("oversized research journal record")
        payload = self._store._read(path)
        if not isinstance(payload, dict):
            raise ArchiveIntegrityError("invalid research envelope")
        return payload

    @contextmanager
    def _lock(self, name: str, *, blocking: bool) -> Iterator[None]:
        directory = self._safe(self.root / ".research-locks")
        directory.mkdir(parents=True, exist_ok=True)
        path = self._safe(directory / name)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise RuntimeError("research role is already owned") from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextmanager
    def role_lock(self, role: Role) -> Iterator[None]:
        if role not in ROLES:
            raise ValueError("invalid research role")
        with self._lock(f"worker-{role}", blocking=False):
            yield

    @contextmanager
    def enrollment_lock(self) -> Iterator[None]:
        with self._lock("enrollment", blocking=True):
            yield

    @contextmanager
    def journal_lock(self, role: Role) -> Iterator[None]:
        if role not in ROLES:
            raise ValueError("invalid research role")
        with self._lock(f"journal-{role}", blocking=True):
            yield

    def write(self, session: ResearchSession) -> None:
        # Reconstruct to revalidate even if a caller changed mutable metadata after creation.
        values = {field.name: getattr(session, field.name) for field in fields(session)}
        ResearchSession(**values)
        values["policy"], values["rank_policy"] = (
            asdict(session.policy),
            asdict(session.rank_policy),
        )
        self._write(
            self._directory(session.session_id) / "definition",
            {
                "kind": "research_session",
                "record": values,
            },
        )

    def read(self, session_id: str) -> ResearchSession:
        payload = self._read(self._directory(session_id) / "definition")
        try:
            if set(payload) != {"kind", "record"} or payload["kind"] != "research_session":
                raise ValueError("unsupported research envelope")
            values = dict(payload["record"])
            values["policy"] = ResearchPolicy(**values["policy"])
            values["rank_policy"] = RankPolicy(**values["rank_policy"])
            session = ResearchSession(**values)
            if session.session_id != session_id:
                raise ValueError("research session identity mismatch")
            return session
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ArchiveIntegrityError("invalid research session") from exc

    def session_ids(self) -> tuple[str, ...]:
        directory = self._safe(self.root / "sessions")
        if not directory.exists():
            return ()
        ids = []
        for path in directory.iterdir():
            if path.name.startswith(".staging-"):
                continue
            try:
                canonical_id(path.name)
                self._safe(path)
                definition = path / "definition"
                if definition.exists():
                    self.read(path.name)
                    ids.append(path.name)
                elif any(not child.name.startswith(".staging-") for child in path.iterdir()):
                    raise ValueError("session directory has no definition")
            except (ValueError, OSError) as exc:
                raise ArchiveIntegrityError("invalid research session directory") from exc
        return tuple(sorted(ids))

    def events(self, session_id: str) -> tuple[ResearchEvent, ...]:
        session = self.read(session_id)
        directory = self._directory(session_id) / "events"
        events = []
        if directory.exists():
            self._safe(directory)
            for role_path in directory.iterdir():
                self._safe(role_path)
                if role_path.name not in ROLES:
                    raise ArchiveIntegrityError("unknown research journal role")
                for path in role_path.iterdir():
                    if path.name.startswith(".staging-"):
                        continue
                    payload = self._read(path)
                    try:
                        if (
                            set(payload) != {"kind", "record"}
                            or payload["kind"] != "research_event"
                        ):
                            raise ValueError("invalid event envelope")
                        event = ResearchEvent(**payload["record"])
                        if (
                            event.role != role_path.name
                            or path.name != f"{event.sequence:010d}-{event.event_id}"
                        ):
                            raise ValueError("event path/identity mismatch")
                        events.append(event)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ArchiveIntegrityError("invalid research event") from exc
        result = tuple(sorted(events, key=lambda event: (event.role, event.sequence)))
        try:
            reduce_session(session, result, session.enrolled_at)
        except (ValueError, TypeError) as exc:
            raise ArchiveIntegrityError("invalid research journal stream") from exc
        return result

    def append(
        self, session_id: str, role: Role, kind: str, details: dict[str, Any], at: datetime
    ) -> ResearchEvent:
        with self.journal_lock(role):
            return self._append_locked(session_id, role, kind, details, at)

    def _append_locked(
        self, session_id: str, role: Role, kind: str, details: dict[str, Any], at: datetime
    ) -> ResearchEvent:
        events = self.events(session_id)
        sequence = 1 + max((event.sequence for event in events if event.role == role), default=0)
        event = ResearchEvent(str(uuid4()), session_id, role, sequence, at, kind, details)
        path = self._directory(session_id) / "events" / role / f"{sequence:010d}-{event.event_id}"
        self._write(
            path,
            {
                "kind": "research_event",
                "record": {field.name: getattr(event, field.name) for field in fields(event)},
            },
        )
        return event

    def append_many(
        self,
        session_id: str,
        role: Role,
        items: tuple[tuple[str, dict[str, Any], datetime], ...],
    ) -> tuple[ResearchEvent, ...]:
        """Publish an ordered prefix atomically per record, with one journal scan.

        A crash can leave only a valid contiguous prefix. The next tick records
        the remainder; it never rewrites a committed event or claims a batch transaction.
        """
        with self.journal_lock(role):
            existing = self.events(session_id)
            sequence = 1 + max(
                (event.sequence for event in existing if event.role == role), default=0
            )
            records = tuple(
                ResearchEvent(str(uuid4()), session_id, role, sequence + index, at, kind, details)
                for index, (kind, details, at) in enumerate(items)
            )
            for event in records:
                path = (
                    self._directory(session_id)
                    / "events"
                    / role
                    / f"{event.sequence:010d}-{event.event_id}"
                )
                self._write(
                    path,
                    {
                        "kind": "research_event",
                        "record": {
                            field.name: getattr(event, field.name) for field in fields(event)
                        },
                    },
                )
            return records

    def reserved_rank_slots(self, session_id: str) -> tuple[int, ...]:
        self.read(session_id)
        directory = self._safe(self._directory(session_id) / "reservations" / "ranks")
        if not directory.exists():
            return ()
        slots = []
        for path in directory.iterdir():
            if path.name.startswith(".staging-"):
                continue
            self._safe(path)
            try:
                slot = int(path.name)
                if str(slot) != path.name or not 0 <= slot <= 576:
                    raise ValueError("noncanonical slot")
                slots.append(slot)
            except ValueError as exc:
                raise ArchiveIntegrityError("invalid rank reservation directory") from exc
        return tuple(sorted(slots))

    def rank_reservation(self, session_id: str, slot: int) -> dict[str, Any] | None:
        if type(slot) is not int or not 0 <= slot <= 576:
            raise ValueError("invalid rank reservation slot")
        session = self.read(session_id)
        path = self._safe(self._directory(session_id) / "reservations" / "ranks" / str(slot))
        if not path.exists():
            return None
        payload = self._read(path)
        try:
            if set(payload) != {
                "kind",
                "session_id",
                "slot",
                "snapshot_id",
                "scheduled_at",
                "reserved_at",
            }:
                raise ValueError("unsupported rank reservation")
            if (
                payload["kind"] != "research_rank_reservation"
                or payload["session_id"] != session_id
                or type(payload["slot"]) is not int
                or payload["slot"] != slot
            ):
                raise ValueError("rank reservation identity mismatch")
            canonical_id(payload["snapshot_id"])
            aware_utc(payload["reserved_at"])
            if payload["scheduled_at"] != session.anchor + timedelta(
                seconds=slot * session.policy.rank_interval_seconds
            ):
                raise ValueError("rank reservation clock mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise ArchiveIntegrityError("invalid rank reservation") from exc
        return payload

    def reserve_rank(self, session_id: str, slot: int, at: datetime) -> dict[str, Any]:
        aware_utc(at)
        with self.journal_lock("ranks"):
            existing = self.rank_reservation(session_id, slot)
            if existing is not None:
                return existing
            session = self.read(session_id)
            payload = {
                "kind": "research_rank_reservation",
                "session_id": session_id,
                "slot": slot,
                "snapshot_id": str(uuid4()),
                "reserved_at": at,
                "scheduled_at": session.anchor
                + timedelta(seconds=slot * session.policy.rank_interval_seconds),
            }
            self._write(self._directory(session_id) / "reservations" / "ranks" / str(slot), payload)
            return payload

    def report_reservation(self, session_id: str, horizon_hours: int) -> dict[str, Any] | None:
        if type(horizon_hours) is not int or horizon_hours not in (24, 48):
            raise ValueError("invalid report horizon")
        session = self.read(session_id)
        path = self._safe(
            self._directory(session_id) / "reservations" / "reports" / str(horizon_hours)
        )
        if not path.exists():
            return None
        payload = self._read(path)
        try:
            if set(payload) != {
                "kind",
                "session_id",
                "horizon_hours",
                "report_id",
                "as_of",
                "inputs",
            }:
                raise ValueError("unsupported report reservation")
            if (
                payload["kind"] != "research_report_reservation"
                or payload["session_id"] != session_id
                or type(payload["horizon_hours"]) is not int
                or payload["horizon_hours"] != horizon_hours
            ):
                raise ValueError("report reservation identity mismatch")
            canonical_id(payload["report_id"])
            if aware_utc(payload["as_of"]) < session.anchor + timedelta(hours=horizon_hours):
                raise ValueError("report cutoff precedes horizon")
            if not isinstance(payload["inputs"], dict):
                raise ValueError("invalid frozen report inputs")
        except (ValueError, TypeError, KeyError) as exc:
            raise ArchiveIntegrityError("invalid report reservation") from exc
        return payload

    def reserve_report(
        self, session_id: str, horizon_hours: int, as_of: datetime, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        as_of = aware_utc(as_of)
        with self.journal_lock("outcomes"):
            existing = self.report_reservation(session_id, horizon_hours)
            if existing is not None:
                if existing["as_of"] != as_of or existing["inputs"] != inputs:
                    raise ArchiveConflict("report inputs already frozen")
                return existing
            session = self.read(session_id)
            if as_of < session.anchor + timedelta(hours=horizon_hours) or not isinstance(
                inputs, dict
            ):
                raise ValueError("invalid report cutoff/inputs")
            payload = {
                "kind": "research_report_reservation",
                "session_id": session_id,
                "horizon_hours": horizon_hours,
                "report_id": str(uuid4()),
                "as_of": as_of,
                "inputs": inputs,
            }
            self._write(
                self._directory(session_id) / "reservations" / "reports" / str(horizon_hours),
                payload,
            )
            return payload
