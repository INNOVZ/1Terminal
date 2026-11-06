import asyncio
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.rank_collector import RankCollector
from algotrader.application.research_rank_worker import ResearchRankWorker
from algotrader.application.research_sessions import ResearchSessions
from algotrader.domain.rank_observations import RankPolicy
from algotrader.infrastructure.rank_archive import LocalRankArchive
from algotrader.infrastructure.scan_archive import ArchiveIntegrityError
from tests.test_shadow_benchmark import benchmark_fixture
from tests.test_shadow_discovery import ticker_fixture


def setup_worker(root, *, offset=300, handler=None):
    parent, child = benchmark_fixture(root)
    service = ResearchSessions(root)
    session = service.start(child.shadow_id, now=child.completed_at)
    now = session.anchor + timedelta(seconds=offset)
    requests = []

    async def respond(request):
        requests.append(request)
        if handler is not None:
            return await handler(request)
        rows = [dict(row, closeTime=int(now.timestamp() * 1000)) for row in ticker_fixture(parent)]
        return httpx.Response(200, json=rows)

    collector = RankCollector(
        root,
        RankPolicy(),
        clock=lambda: now,
        market_factory=lambda budget: ShadowMarketData(
            budget, transport=httpx.MockTransport(respond)
        ),
    )
    return service, session, collector, now, requests


@pytest.mark.parametrize("journaled", [False, True])
async def test_reserved_slot_crash_never_reissues_snapshot(tmp_path, journaled):
    service, session, collector, now, requests = setup_worker(tmp_path)
    reservation = service.store.reserve_rank(session.session_id, 1, now)
    if journaled:
        service.store.append(session.session_id, "ranks", "rank_reserved", reservation, now)
    worker = ResearchRankWorker(service, collector)
    result = await worker.tick(session.session_id, now=now)
    assert result["rank_slots"][1]["kind"] == "rank_interrupted"
    assert result["rank_slots"][1]["snapshot_id"] == reservation["snapshot_id"]
    await worker.tick(session.session_id, now=now + timedelta(seconds=10))
    assert requests == []


async def test_snapshot_commit_before_event_reconciles_without_http(tmp_path):
    service, session, collector, now, requests = setup_worker(tmp_path)
    reservation = service.store.reserve_rank(session.session_id, 1, now)
    committed = await collector.collect(session.parent_id, snapshot_id=reservation["snapshot_id"])
    worker = ResearchRankWorker(service, collector)
    result = await worker.tick(session.session_id, now=now)
    assert result["rank_slots"][1]["kind"] == "rank_completed"
    assert result["rank_slots"][1]["arrived_at"] == committed.arrived_at
    assert len(requests) == 1
    await worker.tick(session.session_id, now=now)
    assert len(requests) == 1
    assert (
        len(
            [
                event
                for event in service.store.events(session.session_id)
                if event.kind == "rank_completed"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("offset,count", [(329, 1), (331, 0), (1200, 1), (172800, 1), (172801, 0)])
async def test_restart_lateness_and_forward_jump_no_catchup_burst(tmp_path, offset, count):
    service, session, collector, now, requests = setup_worker(tmp_path, offset=offset)
    result = await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert len(requests) == count
    if offset > 330:
        assert result["rank_slots"][1]["kind"] == "rank_missed"


async def test_backward_clock_does_not_repeat_consumed_slots(tmp_path):
    service, session, collector, now, requests = setup_worker(tmp_path)
    worker = ResearchRankWorker(service, collector)
    await worker.tick(session.session_id, now=now)
    await worker.tick(session.session_id, now=now - timedelta(seconds=300))
    await worker.tick(session.session_id, now=now)
    assert len(requests) == 1


@pytest.mark.parametrize("status", ["stopped", "source_mismatch", "storage_blocked"])
async def test_blocked_and_stopped_sessions_never_construct_market(tmp_path, monkeypatch, status):
    service, session, collector, now, requests = setup_worker(tmp_path)
    if status == "stopped":
        service.stop(session.session_id, now=now)
    elif status == "source_mismatch":
        monkeypatch.setattr(service, "semantic_identity", lambda child: {})
    else:
        monkeypatch.setattr(service, "archive_bytes", lambda root: 5 * 1024**3)
    result = await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert result["state"] == status
    assert requests == []


async def test_partial_snapshot_remains_partial_not_successful_arrival(tmp_path):
    async def empty(request):
        return httpx.Response(200, json=[])

    service, session, collector, now, requests = setup_worker(tmp_path, handler=empty)
    result = await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert result["rank_slots"][1]["status"] == "partial"
    assert result["last_successful_arrival"] is None
    assert len(requests) == 1


async def test_failed_snapshot_is_degraded_not_a_success(tmp_path):
    async def malformed(request):
        return httpx.Response(200, json={"bad": "provider payload"})

    service, session, collector, now, requests = setup_worker(tmp_path, handler=malformed)
    result = await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert result["rank_slots"][1]["status"] == "failed"
    assert result["state"] == "degraded"
    assert result["last_successful_arrival"] is None


async def test_external_cancellation_propagates_after_snapshot_and_journal_commit(tmp_path):
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    service, session, collector, now, requests = setup_worker(tmp_path, handler=blocked)
    pending = asyncio.create_task(
        ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    )
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    events = service.store.events(session.session_id)
    linked = [event for event in events if event.kind == "rank_completed"]
    assert len(linked) == 1 and linked[0].details["status"] == "failed"
    assert linked[0].details["error"] == "CancelledError"


async def test_stop_during_acquisition_cancels_and_persists_terminal_snapshot(tmp_path):
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    service, session, collector, now, requests = setup_worker(tmp_path, handler=blocked)
    pending = asyncio.create_task(
        ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    )
    await asyncio.wait_for(entered.wait(), 2)
    service.stop(session.session_id, now=now)
    result = await asyncio.wait_for(pending, 3)
    assert result["state"] == "stopped"
    reservation = service.store.rank_reservation(session.session_id, 1)
    snapshot = LocalRankArchive(tmp_path).read_snapshot(
        session.parent_id, reservation["snapshot_id"]
    )
    assert snapshot.status == "failed" and snapshot.error == "CancelledError"


async def test_unrelated_reserved_snapshot_content_fails_closed(tmp_path):
    service, session, collector, now, requests = setup_worker(tmp_path)
    reservation = service.store.reserve_rank(session.session_id, 1, now)
    original = await collector.collect(session.parent_id)
    invalid = replace(
        original, snapshot_id=reservation["snapshot_id"], identity={"different": True}
    )
    LocalRankArchive(tmp_path).write_snapshot(invalid)
    with pytest.raises(ArchiveIntegrityError):
        await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert len(requests) == 1


async def test_missed_slots_do_not_reread_journal_per_slot(tmp_path, monkeypatch):
    service, session, collector, now, requests = setup_worker(tmp_path, offset=3000)
    reads = 0
    original = service.store.events

    def count_reads(*args):
        nonlocal reads
        reads += 1
        return original(*args)

    monkeypatch.setattr(service.store, "events", count_reads)
    await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    # Includes guards, recovery, reservation, completion and returned status.
    assert reads <= 14


async def test_recovery_does_not_reread_entire_journal_per_terminal_reservation(
    tmp_path, monkeypatch
):
    service, session, collector, now, requests = setup_worker(tmp_path, offset=3000)
    for slot in range(10):
        reservation = service.store.reserve_rank(session.session_id, slot, now)
        service.store.append(session.session_id, "ranks", "rank_interrupted", reservation, now)
    reads = 0
    original = service.store.events

    def count_reads(*args):
        nonlocal reads
        reads += 1
        return original(*args)

    monkeypatch.setattr(service.store, "events", count_reads)
    await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert reads <= 14


async def test_outcome_cooldown_blocks_rank_requests(tmp_path):
    service, session, collector, now, requests = setup_worker(tmp_path)
    service.record_response(
        session.session_id,
        "outcomes",
        {"status_code": 429, "retry_after": "7200", "observed_weight_1m": None},
        at=now,
    )
    result = await ResearchRankWorker(service, collector).tick(session.session_id, now=now)
    assert requests == []
    assert result["guard"]["next_allowed_at"] == now + timedelta(hours=2)


@pytest.mark.parametrize("already_committed", [False, True])
@pytest.mark.parametrize("clock_offset", [-1, 31])
async def test_late_capture_terminates_slot_without_poisoning_recovery(
    tmp_path, already_committed, clock_offset
):
    service, session, collector, now, requests = setup_worker(tmp_path)
    collector.clock = lambda: now + timedelta(seconds=clock_offset)
    if already_committed:
        reserved = service.store.reserve_rank(session.session_id, 1, now)
        await collector.collect(session.parent_id, snapshot_id=reserved["snapshot_id"])
    worker = ResearchRankWorker(service, collector)
    result = await worker.tick(session.session_id, now=now)
    assert result["rank_slots"][1]["kind"] == "rank_interrupted"
    assert result["last_successful_arrival"] is None
    assert len(requests) == int(already_committed)
    collector.clock = lambda: now + timedelta(minutes=5)
    result = await worker.tick(session.session_id, now=now + timedelta(minutes=5))
    assert result["rank_slots"][2]["kind"] == "rank_completed"
    assert len(requests) == 1 + int(already_committed)
