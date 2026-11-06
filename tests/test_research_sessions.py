from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from algotrader.application.research_sessions import ResearchSessions
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_shadow_benchmark import benchmark_fixture


def test_offline_enrollment_is_idempotent_and_stop_permanent(tmp_path, monkeypatch):
    parent, child = benchmark_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline operations must not construct HTTP clients")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    service = ResearchSessions(tmp_path)
    now = child.completed_at + timedelta(seconds=1)
    session = service.start(child.shadow_id, now=now)
    assert session.anchor == outcome_anchor(child.completed_at)
    assert session.cohort == parent.cohort
    assert service.start(child.shadow_id, now=now).session_id == session.session_id
    other = replace(child, shadow_id=str(uuid4()))
    LocalShadowArchive(tmp_path).write_run(other)
    with pytest.raises(ValueError, match="active"):
        service.start(other.shadow_id, now=now)
    with service.store.role_lock("ranks"):
        service.stop(session.session_id, now=now)
        service.stop(session.session_id, now=now)
        assert service.status(session.session_id, now=now)["state"] == "stopped"
    assert len(service.store.events(session.session_id)) == 1
    assert service.start(child.shadow_id, now=session.endpoint_48h + timedelta(days=1)) == session
    assert service.start(other.shadow_id, now=now).session_id != session.session_id


def test_enrollment_rejects_late_naive_and_invalid_parent_cohort(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    with pytest.raises(ValueError):
        service.start(child.shadow_id, now=child.completed_at.replace(tzinfo=None))
    with pytest.raises(ValueError):
        service.start(
            child.shadow_id,
            now=outcome_anchor(child.completed_at) + timedelta(hours=48, microseconds=1),
        )
    bad = replace(child, shadow_id=str(uuid4()), parent_checksum="0" * 64)
    LocalShadowArchive(tmp_path).write_run(bad)
    with pytest.raises(ArchiveIntegrityError):
        service.start(bad.shadow_id, now=child.completed_at)


def test_identity_mismatch_blocks_acquisition_but_status_remains_readable(tmp_path, monkeypatch):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    monkeypatch.setattr(service, "semantic_identity", lambda child: {"changed": True})
    assert service.guard(session.session_id)["reason"] == "source_mismatch"
    status = service.status(session.session_id, now=child.completed_at)
    assert status["state"] == "source_mismatch"
    assert status["last_successful_arrival"] is None


@pytest.mark.parametrize("free,blocked", [(2 * 1024**3 - 1, True), (2 * 1024**3, False)])
def test_disk_guard_boundary_retains_evidence(tmp_path, monkeypatch, free, blocked):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    monkeypatch.setattr(
        "algotrader.application.research_sessions.shutil.disk_usage",
        lambda path: SimpleNamespace(free=free),
    )
    assert (service.guard(session.session_id)["reason"] == "storage_blocked") == blocked
    assert service.store.read(session.session_id) == session


@pytest.mark.parametrize("size,blocked", [(5 * 1024**3 - 1, False), (5 * 1024**3, True)])
def test_whole_archive_size_guard_not_just_session_namespace(tmp_path, monkeypatch, size, blocked):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    monkeypatch.setattr(
        "algotrader.application.research_sessions.shutil.disk_usage",
        lambda path: SimpleNamespace(free=3 * 1024**3),
    )
    seen = []

    def archive_bytes(root):
        seen.append(root)
        return size

    monkeypatch.setattr(service, "archive_bytes", archive_bytes)
    assert (service.guard(session.session_id)["reason"] == "storage_blocked") == blocked
    assert seen == [tmp_path]


def test_blocked_state_events_are_bounded_by_transition_or_slot(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    for _ in range(5):
        service.record_state(
            session.session_id, "ranks", "storage_blocked", at=session.anchor, slot=0
        )
    assert len(service.store.events(session.session_id)) == 1
    service.record_state(session.session_id, "ranks", "storage_blocked", at=session.anchor, slot=1)
    service.record_state(session.session_id, "ranks", "running", at=session.anchor, slot=1)
    assert len(service.store.events(session.session_id)) == 3


def test_archive_measurement_includes_sibling_evidence(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    service.start(child.shadow_id, now=child.completed_at)
    baseline = service.archive_bytes(tmp_path)
    sibling = tmp_path / "shadow-outcomes" / "growth-test"
    sibling.parent.mkdir(exist_ok=True)
    sibling.write_bytes(b"x" * 2048)
    assert service.archive_bytes(tmp_path) == baseline + 2048


def test_enrollment_serialization_is_cross_process(tmp_path):
    import os
    import subprocess
    import sys

    _, child = benchmark_fixture(tmp_path)
    code = """
from datetime import datetime
from pathlib import Path
import sys
from algotrader.application.research_sessions import ResearchSessions
result = ResearchSessions(Path(sys.argv[1])).start(sys.argv[2], now=datetime.fromisoformat(sys.argv[3]))
print(result.session_id)
"""
    argv = [
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        child.shadow_id,
        child.completed_at.isoformat(),
    ]
    processes = [
        subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        for _ in range(2)
    ]
    results = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            results.append(stdout.strip())
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert results[0] == results[1]
    assert len(ResearchSessions(tmp_path).store.session_ids()) == 1


@pytest.mark.parametrize("retry,seconds", [("7200", 7200), ("bad", 300), ("NaN", 300), ("-1", 300)])
def test_response_cooldown_is_shared_and_honors_retry_after(tmp_path, retry, seconds):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    service.record_response(
        session.session_id,
        "outcomes",
        {"status_code": 429, "retry_after": retry, "observed_weight_1m": None},
        at=session.anchor,
    )
    blocked = service.guard(session.session_id, now=session.anchor)
    assert blocked["ok"] is False
    assert blocked["detail"] == "exchange_cooldown"
    assert blocked["next_allowed_at"] == session.anchor + timedelta(seconds=seconds)
    assert (
        service.guard(session.session_id, now=session.anchor + timedelta(seconds=seconds))["ok"]
        is True
    )
    # A separate process/service instance must see the same durable gate.
    assert ResearchSessions(tmp_path).guard(session.session_id, now=session.anchor)["ok"] is False


def test_unrepresentable_cooldown_blocks_without_overflow(tmp_path):
    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    service.record_response(
        session.session_id,
        "ranks",
        {"status_code": 418, "retry_after": "1e300", "observed_weight_1m": 2200},
        at=session.anchor,
    )
    result = service.guard(session.session_id, now=session.anchor)
    assert result["ok"] is False
    assert result["detail"] == "cooldown_unrepresentable_operator_attention"
    assert result["next_allowed_at"] is None


def test_cooldown_clock_near_datetime_max_fails_closed(tmp_path):
    from datetime import UTC, datetime

    _, child = benchmark_fixture(tmp_path)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    at = datetime.max.replace(tzinfo=UTC)
    service.record_response(
        session.session_id,
        "ranks",
        {"status_code": 429, "retry_after": "1", "observed_weight_1m": None},
        at=at,
    )
    assert (
        service.guard(session.session_id, now=at)["detail"]
        == "cooldown_unrepresentable_operator_attention"
    )
