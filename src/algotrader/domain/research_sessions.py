"""Pure, anchored research-session decisions; no clients, clocks or filesystem I/O."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from algotrader.domain.models import Instrument
from algotrader.domain.rank_observations import RankPolicy, validate_cohort

Role = Literal["ranks", "outcomes", "control"]
ROLES = ("ranks", "outcomes", "control")
STATES = ("running", "starting", "degraded", "storage_blocked", "source_mismatch", "disabled")
KINDS = {
    "heartbeat",
    "state",
    "stop_requested",
    "rank_reserved",
    "rank_completed",
    "rank_interrupted",
    "rank_missed",
    "outcome_pass",
    "outcome_symbol_reserved",
    "report_reserved",
    "report_completed",
    "clock_discontinuity",
    "cooldown",
}


def aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research timestamps must be timezone-aware")
    return value.astimezone(UTC)


def canonical_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("research identifier must be a canonical UUID")
    try:
        if str(UUID(value)) != value:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as exc:
        raise ValueError("research identifier must be a canonical UUID") from exc
    return value


def _checksum(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid research checksum")


def _metadata(value: Any) -> None:
    """Reject nonfinite/unsupported data before immutable journal publication."""
    if value is None or type(value) in (str, bool, int):
        return
    if isinstance(value, datetime):
        aware_utc(value)
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _metadata(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _metadata(item)
        return
    raise ValueError("invalid research metadata")


@dataclass(frozen=True)
class ResearchPolicy:
    version: str = "research-runner-v1"
    rank_interval_seconds: int = 300
    rank_lateness_seconds: int = 30
    publication_grace_seconds: int = 60
    retry_window_seconds: int = 43200
    outcome_max_attempts: int = 30
    outcome_deadline_seconds: int = 90
    min_free_bytes: int = 2 * 1024**3
    max_archive_bytes: int = 5 * 1024**3

    def __post_init__(self) -> None:
        # V1 fixes the experiment schedule; tuning creates a different policy version.
        fixed = (
            (self.rank_interval_seconds, 300),
            (self.rank_lateness_seconds, 30),
            (self.publication_grace_seconds, 60),
            (self.retry_window_seconds, 43200),
            (self.outcome_max_attempts, 30),
            (self.outcome_deadline_seconds, 90),
        )
        if self.version != "research-runner-v1" or any(
            type(value) is not int or value != expected for value, expected in fixed
        ):
            raise ValueError("unsupported research policy")
        if (
            type(self.min_free_bytes) is not int
            or self.min_free_bytes < 2 * 1024**3
            or type(self.max_archive_bytes) is not int
            or not 1 <= self.max_archive_bytes <= 5 * 1024**3
        ):
            raise ValueError("research storage policy exceeds safety caps")


@dataclass(frozen=True)
class ResearchSession:
    session_id: str
    parent_id: str
    shadow_id: str
    parent_checksum: str
    shadow_checksum: str
    cohort: tuple[Instrument, ...]
    enrolled_at: datetime
    anchor: datetime
    endpoint_24h: datetime
    endpoint_48h: datetime
    policy: ResearchPolicy
    rank_policy: RankPolicy
    identity: dict[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        for value in (self.session_id, self.parent_id, self.shadow_id):
            canonical_id(value)
        for checksum in (self.parent_checksum, self.shadow_checksum):
            _checksum(checksum)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported research session schema")
        if not isinstance(self.policy, ResearchPolicy) or not isinstance(
            self.rank_policy, RankPolicy
        ):
            raise ValueError("invalid research policies")
        validate_cohort(self.cohort, self.rank_policy)
        for name in ("anchor", "enrolled_at", "endpoint_24h", "endpoint_48h"):
            object.__setattr__(self, name, aware_utc(getattr(self, name)))
        if self.anchor.second or self.anchor.microsecond:
            raise ValueError("research anchor must be a full minute")
        if (
            self.endpoint_24h != self.anchor + timedelta(hours=24)
            or self.endpoint_48h != self.anchor + timedelta(hours=48)
            or self.enrolled_at > self.endpoint_48h
        ):
            raise ValueError("research windows must retain original child endpoints")
        if not isinstance(self.identity, dict) or not self.identity:
            raise ValueError("missing research semantic identity")
        _metadata(self.identity)


@dataclass(frozen=True)
class ResearchEvent:
    event_id: str
    session_id: str
    role: Role
    sequence: int
    at: datetime
    kind: str
    details: dict[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        canonical_id(self.event_id)
        canonical_id(self.session_id)
        object.__setattr__(self, "at", aware_utc(self.at))
        if self.role not in ROLES or type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("invalid research event role or sequence")
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.kind not in KINDS
        ):
            raise ValueError("unsupported research event")
        if not isinstance(self.details, dict):
            raise ValueError("research event details must be a mapping")
        _metadata(self.details)
        if self.kind == "state" and self.details.get("state") not in STATES:
            raise ValueError("invalid research state")
        if self.kind == "stop_requested" and self.role != "control":
            raise ValueError("stop events belong to control journal")
        if self.kind.startswith("rank_") and self.role != "ranks":
            raise ValueError("rank events belong to ranks journal")
        if self.kind.startswith(("report_", "outcome_")) and self.role != "outcomes":
            raise ValueError("outcome events belong to outcomes journal")
        if self.kind.startswith("rank_"):
            slot = self.details.get("slot")
            if type(slot) is not int or not 0 <= slot <= 576:
                raise ValueError("invalid rank slot")
        if self.kind.startswith(("report_", "outcome_")):
            horizon = self.details.get("horizon_hours")
            if type(horizon) is not int or horizon not in (24, 48):
                raise ValueError("invalid research horizon")
        if self.kind == "report_completed":
            report_id = self.details.get("report_id")
            if not isinstance(report_id, str):
                raise ValueError("missing final report identifier")
            canonical_id(report_id)
        if self.kind == "outcome_symbol_reserved":
            symbol = self.details.get("symbol")
            if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9_]{1,80}", symbol) is None:
                raise ValueError("invalid reserved outcome symbol")
            pass_id = self.details.get("pass_id")
            if not isinstance(pass_id, str):
                raise ValueError("invalid reserved outcome pass")
            canonical_id(pass_id)


def _indices(values: frozenset[int], allowed: range | tuple[int, ...]) -> None:
    if not isinstance(values, frozenset) or any(
        type(value) is not int or value not in allowed for value in values
    ):
        raise ValueError("invalid consumed research indices")


def rank_slot(session: ResearchSession, now: datetime, consumed: frozenset[int]) -> dict[str, Any]:
    now = aware_utc(now)
    _indices(consumed, range(577))
    if now < session.enrolled_at:
        return {
            "eligible_slot": None,
            "scheduled_at": None,
            "missed_slots": (),
            "next_at": max(session.anchor, session.enrolled_at),
            "terminal_reason": None,
        }
    interval = session.policy.rank_interval_seconds
    lateness = timedelta(seconds=session.policy.rank_lateness_seconds)
    elapsed = (now - session.anchor).total_seconds()
    current = math.floor(elapsed / interval)
    eligible = None
    if 0 <= current <= 576 and now <= session.endpoint_48h and current not in consumed:
        scheduled = session.anchor + timedelta(seconds=current * interval)
        if now <= scheduled + lateness:
            eligible = current
    missed = tuple(
        slot
        for slot in range(min(max(current + 1, 0), 577))
        if slot not in consumed
        and slot != eligible
        and (
            now > session.anchor + timedelta(seconds=slot * interval) + lateness
            or now > session.endpoint_48h
        )
    )
    next_slot = max(0, current + 1)
    while next_slot in consumed:
        next_slot += 1
    return {
        "eligible_slot": eligible,
        "scheduled_at": None
        if eligible is None
        else session.anchor + timedelta(seconds=eligible * interval),
        "missed_slots": missed,
        "next_at": session.anchor + timedelta(seconds=next_slot * interval)
        if next_slot <= 576
        else None,
        "terminal_reason": "rank_window_ended" if now > session.endpoint_48h else None,
    }


def due_horizon(session: ResearchSession, now: datetime, completed: frozenset[int]) -> int | None:
    now = aware_utc(now)
    _indices(completed, (24, 48))
    for horizon in (24, 48):
        endpoint = session.anchor + timedelta(hours=horizon)
        if horizon not in completed and now >= endpoint + timedelta(
            seconds=session.policy.publication_grace_seconds
        ):
            return horizon
    return None


def reduce_session(
    session: ResearchSession, events: tuple[ResearchEvent, ...], now: datetime
) -> dict[str, Any]:
    now = aware_utc(now)
    if not isinstance(events, tuple) or any(
        not isinstance(event, ResearchEvent) for event in events
    ):
        raise ValueError("invalid research event stream")
    if any(event.session_id != session.session_id for event in events):
        raise ValueError("cross-session journal event")
    if len({(event.role, event.sequence) for event in events}) != len(events) or len(
        {event.event_id for event in events}
    ) != len(events):
        raise ValueError("ambiguous research journal sequence")
    ordered = sorted(events, key=lambda event: (event.role, event.sequence))
    for role in ROLES:
        sequences = [event.sequence for event in ordered if event.role == role]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("research journal has a sequence gap")
    heartbeats: dict[str, datetime] = {}
    role_states: dict[str, str] = {}
    reports: dict[int, str] = {}
    slots: dict[int, dict[str, Any]] = {}
    arrivals: list[datetime] = []
    stopped = False
    for event in ordered:
        if event.kind == "stop_requested":
            stopped = True
        elif event.kind == "heartbeat":
            heartbeats[event.role] = event.at
        elif event.kind == "state":
            role_states[event.role] = event.details["state"]
        elif event.kind == "report_completed":
            horizon = event.details["horizon_hours"]
            report_id = event.details["report_id"]
            if horizon in reports and reports[horizon] != report_id:
                raise ValueError("multiple final reports for one horizon")
            reports[horizon] = report_id
        elif event.kind.startswith("rank_"):
            slots[event.details["slot"]] = {"kind": event.kind, **event.details}
            if event.kind == "rank_completed" and event.details.get("status") == "complete":
                arrival = event.details.get("arrived_at")
                if arrival is not None:
                    arrivals.append(aware_utc(arrival))
    state = max(role_states.values(), key=STATES.index) if role_states else "starting"
    if len(reports) == 2:
        state = "complete"
    if stopped:
        state = "stopped"
    horizons = {}
    for horizon in (24, 48):
        endpoint = session.anchor + timedelta(hours=horizon)
        due_at = endpoint + timedelta(seconds=session.policy.publication_grace_seconds)
        retry_until = endpoint + timedelta(seconds=session.policy.retry_window_seconds)
        horizons[horizon] = {
            "endpoint": endpoint,
            "due_at": due_at,
            "retry_until": retry_until,
            "due": now >= due_at,
            "expired": now >= retry_until,
            "report_id": reports.get(horizon),
            "state": "complete"
            if horizon in reports
            else "expired"
            if now >= retry_until
            else "due"
            if now >= due_at
            else "pending",
        }
    arrivals.sort()
    sampling_gaps = tuple(
        {"after": previous, "before": current, "seconds": (current - previous).total_seconds()}
        for previous, current in zip(arrivals, arrivals[1:], strict=False)
        if (current - previous).total_seconds() > session.rank_policy.sampling_max_gap_seconds
    )
    last_arrival = max(arrivals, default=None)
    return {
        "session_id": session.session_id,
        "state": state,
        "role_states": role_states,
        "heartbeats": heartbeats,
        "last_successful_arrival": last_arrival,
        "successful_arrivals": tuple(sorted(arrivals)),
        "rank_slots": slots,
        "sampling_gaps": sampling_gaps,
        "unobserved_tail_seconds": max(
            0.0, (min(now, session.endpoint_48h) - (last_arrival or session.anchor)).total_seconds()
        ),
        "rank_decision": rank_slot(session, now, frozenset(slots)),
        "horizons": horizons,
        "reports": reports,
        "terminal": state in ("stopped", "complete"),
    }
