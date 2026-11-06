from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.research_sessions import (
    ResearchEvent,
    ResearchPolicy,
    ResearchSession,
    due_horizon,
    rank_slot,
    reduce_session,
)
from tests.test_shadow_discovery import parent_fixture

ANCHOR = datetime(2026, 10, 25, 12, tzinfo=UTC)


def session_fixture():
    return ResearchSession(
        session_id=str(uuid4()),
        parent_id=str(uuid4()),
        shadow_id=str(uuid4()),
        parent_checksum="a" * 64,
        shadow_checksum="b" * 64,
        cohort=parent_fixture().cohort,
        enrolled_at=ANCHOR,
        anchor=ANCHOR,
        endpoint_24h=ANCHOR + timedelta(hours=24),
        endpoint_48h=ANCHOR + timedelta(hours=48),
        policy=ResearchPolicy(),
        rank_policy=RankPolicy(),
        identity={"measurement": "c" * 64},
    )


def event(session, role, sequence, kind, details=None, at=ANCHOR):
    return ResearchEvent(
        event_id=str(uuid4()),
        session_id=session.session_id,
        role=role,
        sequence=sequence,
        at=at,
        kind=kind,
        details=details or {},
    )


def test_rank_slots_do_not_drift_repeat_or_backfill():
    session = session_fixture()
    for seconds in (300, 330):
        decision = rank_slot(session, ANCHOR + timedelta(seconds=seconds), frozenset({0}))
        assert decision["eligible_slot"] == 1
        assert decision["scheduled_at"] == ANCHOR + timedelta(seconds=300)
    missed = rank_slot(session, ANCHOR + timedelta(seconds=330, microseconds=1), frozenset({0}))
    assert missed["eligible_slot"] is None
    assert missed["missed_slots"] == (1,)
    assert missed["next_at"] == ANCHOR + timedelta(seconds=600)
    assert (
        rank_slot(session, ANCHOR + timedelta(seconds=305), frozenset({0, 1}))["eligible_slot"]
        is None
    )
    assert (
        rank_slot(session, ANCHOR + timedelta(seconds=290), frozenset({0, 1}))["eligible_slot"]
        is None
    )
    final = rank_slot(session, session.endpoint_48h, frozenset())
    assert final["eligible_slot"] == 576
    assert (
        rank_slot(session, session.endpoint_48h + timedelta(microseconds=1), frozenset())[
            "terminal_reason"
        ]
        == "rank_window_ended"
    )
    assert rank_slot(session, ANCHOR - timedelta(seconds=1), frozenset())["next_at"] == ANCHOR


def test_late_enrollment_does_not_reset_window():
    session = replace(session_fixture(), enrolled_at=ANCHOR + timedelta(hours=47))
    assert rank_slot(session, session.endpoint_48h, frozenset())["eligible_slot"] == 576
    with pytest.raises(ValueError):
        replace(session, endpoint_48h=session.enrolled_at + timedelta(hours=48))
    with pytest.raises(ValueError):
        replace(session, enrolled_at=session.endpoint_48h + timedelta(microseconds=1))


@pytest.mark.parametrize("bad", [True, 0, -1, 301, 1.5])
def test_policy_rejects_invalid_cadence(bad):
    with pytest.raises(ValueError):
        ResearchPolicy(rank_interval_seconds=bad)


def test_session_identifiers_timestamps_and_cohort_fail_closed():
    session = session_fixture()
    for changes in (
        {"session_id": "../../x"},
        {"parent_checksum": "bad"},
        {"anchor": ANCHOR.replace(tzinfo=None)},
        {"cohort": ()},
        {"schema_version": 2},
        {"identity": {}},
    ):
        with pytest.raises(ValueError):
            replace(session, **changes)
    with pytest.raises(ValueError):
        rank_slot(session, ANCHOR.replace(tzinfo=None), frozenset())
    with pytest.raises(ValueError):
        rank_slot(session, ANCHOR, frozenset({True}))


def test_horizons_grace_precedence_and_fixed_expiry():
    session = session_fixture()
    assert due_horizon(session, session.endpoint_24h + timedelta(seconds=59), frozenset()) is None
    assert due_horizon(session, session.endpoint_24h + timedelta(seconds=60), frozenset()) == 24
    assert due_horizon(session, session.endpoint_48h + timedelta(seconds=60), frozenset()) == 24
    assert due_horizon(session, session.endpoint_48h + timedelta(seconds=60), frozenset({24})) == 48
    state = reduce_session(session, (), session.endpoint_24h + timedelta(hours=12))
    assert state["horizons"][24]["expired"] is True
    assert state["horizons"][24]["retry_until"] == session.endpoint_24h + timedelta(hours=12)
    attempt = event(
        session,
        "outcomes",
        1,
        "outcome_pass",
        {"horizon_hours": 24},
        at=session.endpoint_24h + timedelta(hours=11),
    )
    assert (
        reduce_session(session, (attempt,), session.endpoint_24h + timedelta(hours=12))["horizons"][
            24
        ]["expired"]
        is True
    )


def test_stop_dominates_later_start_and_heartbeat_is_not_success():
    session = session_fixture()
    heartbeat = event(session, "ranks", 1, "heartbeat", at=ANCHOR + timedelta(minutes=5))
    state = reduce_session(session, (heartbeat,), ANCHOR + timedelta(minutes=6))
    assert state["last_successful_arrival"] is None
    assert state["heartbeats"]["ranks"] == heartbeat.at
    stopped = event(session, "control", 1, "stop_requested")
    restarted = event(session, "ranks", 2, "state", {"state": "running"})
    assert reduce_session(session, (restarted, stopped, heartbeat), ANCHOR)["state"] == "stopped"


@pytest.mark.parametrize(
    "status", ["disabled", "starting", "running", "degraded", "storage_blocked", "source_mismatch"]
)
def test_status_is_separate_from_success_and_uses_role_sequence(status):
    session = session_fixture()
    old = event(
        session, "ranks", 1, "state", {"state": "running"}, at=ANCHOR + timedelta(minutes=10)
    )
    latest = event(session, "ranks", 2, "state", {"state": status}, at=ANCHOR)
    result = reduce_session(session, (latest, old), ANCHOR)
    assert result["state"] == status
    assert result["last_successful_arrival"] is None


def test_reports_complete_only_when_both_horizons_finalized():
    session = session_fixture()
    first = event(
        session, "outcomes", 1, "report_completed", {"horizon_hours": 24, "report_id": str(uuid4())}
    )
    second = event(
        session, "outcomes", 2, "report_completed", {"horizon_hours": 48, "report_id": str(uuid4())}
    )
    assert reduce_session(session, (first,), session.endpoint_48h)["state"] != "complete"
    assert reduce_session(session, (second, first), session.endpoint_48h)["state"] == "complete"


def test_event_stream_rejects_ambiguous_sequences_and_wrong_session():
    session = session_fixture()
    first = event(session, "ranks", 1, "heartbeat")
    duplicate = event(session, "ranks", 1, "heartbeat")
    with pytest.raises(ValueError):
        reduce_session(session, (first, duplicate), ANCHOR)
    with pytest.raises(ValueError):
        reduce_session(session, (replace(first, session_id=str(uuid4())),), ANCHOR)
    for changes in (
        {"role": "bad"},
        {"sequence": True},
        {"kind": "unknown"},
        {"at": ANCHOR.replace(tzinfo=None)},
    ):
        with pytest.raises(ValueError):
            replace(first, **changes)


def test_role_sequence_gaps_fail_closed():
    session = session_fixture()
    with pytest.raises(ValueError):
        reduce_session(session, (event(session, "ranks", 2, "heartbeat"),), ANCHOR)


def test_clock_rollback_before_enrollment_cannot_backfill():
    session = replace(session_fixture(), enrolled_at=ANCHOR + timedelta(hours=1))
    decision = rank_slot(session, ANCHOR, frozenset())
    assert decision["eligible_slot"] is None
    assert decision["next_at"] == session.enrolled_at


def test_arrival_and_actual_gaps_are_not_inferred_from_heartbeats():
    session = session_fixture()
    first = event(
        session,
        "ranks",
        1,
        "rank_completed",
        {
            "slot": 0,
            "status": "complete",
            "arrived_at": ANCHOR,
        },
    )
    second = event(
        session,
        "ranks",
        2,
        "rank_completed",
        {
            "slot": 3,
            "status": "complete",
            "arrived_at": ANCHOR + timedelta(minutes=15),
        },
    )
    partial = event(
        session,
        "ranks",
        3,
        "rank_completed",
        {
            "slot": 4,
            "status": "partial",
            "arrived_at": ANCHOR + timedelta(minutes=20),
        },
    )
    result = reduce_session(session, (first, second, partial), ANCHOR + timedelta(minutes=25))
    assert result["last_successful_arrival"] == second.details["arrived_at"]
    assert result["sampling_gaps"] == (
        {
            "after": ANCHOR,
            "before": ANCHOR + timedelta(minutes=15),
            "seconds": 900.0,
        },
    )
    assert result["unobserved_tail_seconds"] == 600
