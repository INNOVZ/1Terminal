import asyncio
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from algotrader.adapters.binance.research import ResearchMarketData
from algotrader.application.research_outcome_worker import ResearchOutcomeWorker
from algotrader.application.research_sessions import ResearchSessions
from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.application.shadow_observer import ShadowObserver
from algotrader.domain.rank_observations import RankPolicy
from algotrader.infrastructure.rank_archive import LocalRankArchive, digest
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_shadow_archive import record_fixture
from tests.test_shadow_discovery import parent_fixture


def setup_session(root, count=2):
    parent = replace(
        parent_fixture(count),
        started_at=ANCHOR - timedelta(minutes=3),
        completed_at=ANCHOR - timedelta(minutes=2),
        opportunities=({"symbol": "T1USDT", "direction": "short"},),
    )
    child = replace(
        record_fixture(),
        cohort=parent.cohort,
        parent_checksum=digest(parent),
        started_at=ANCHOR - timedelta(minutes=1),
        completed_at=ANCHOR - timedelta(seconds=15),
        results=(
            {
                "symbol": "T0USDT",
                "qualified": True,
                "direction": "long",
                "suggested_entry": "100",
                "suggested_stop": "95",
                "suggested_targets": ["105", "110"],
            },
        ),
    )
    LocalScanArchive(root).write_scan(parent)
    LocalShadowArchive(root).write_run(child)
    sessions = ResearchSessions(root)
    session = sessions.start(child.shadow_id, now=child.completed_at)
    return sessions, session, child


class History:
    def __init__(self, budget, calls, *, missing=False):
        self.budget, self.calls, self.missing = budget, calls, missing
        self.closed = False

    async def minute_candles_between(self, instrument, start, end):
        assert start == ANCHOR
        self.budget.before_attempt()
        self.calls.append(instrument.id.symbol)
        if self.missing and instrument.id.symbol == "T1USDT":
            raise RuntimeError("delisted")
        return minute_path(hours=int((end - start).total_seconds() / 3600))

    async def close(self):
        self.closed = True


def benchmark(root):
    return ShadowBenchmark(root, RankPolicy())


async def test_outcome_pass_budget_does_not_touch_unvisited_symbols(tmp_path):
    sessions, session, child = setup_session(tmp_path, 40)
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    worker = ResearchOutcomeWorker(
        sessions,
        lambda budget, meta: ResearchMarketData(
            budget, meta, transport=httpx.MockTransport(handler)
        ),
        benchmark(tmp_path),
    )
    result = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60))
    assert len(requests) == 30
    archive = LocalShadowArchive(tmp_path)
    for index in range(40):
        assert len(archive.outcome_attempts(child.shadow_id, 24, f"T{index}USDT")) == (
            1 if index < 30 else 0
        )
    assert result["reports"] == {}
    attempts = [
        event
        for event in sessions.store.events(session.session_id)
        if event.kind == "outcome_pass" and event.details["phase"] != "reserved"
    ]
    assert attempts[-1].details["accounting"]["attempts"] == 30


async def test_grace_and_one_pass_per_slot_resume_only_missing_paths(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    calls, markets = [], []
    missing = True

    def factory(budget, metadata):
        market = History(budget, calls, missing=missing)
        markets.append(market)
        return market

    worker = ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path))
    await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=59))
    assert markets == []
    first = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60))
    assert calls == ["T0USDT", "T1USDT"] and first["reports"] == {}
    await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=61))
    assert len(markets) == 1
    missing = False
    result = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, minutes=5))
    assert calls == ["T0USDT", "T1USDT", "T1USDT"]
    assert all(market.closed for market in markets)
    report = LocalRankArchive(tmp_path).read_report(session.parent_id, result["reports"][24])
    assert report.payload["original_cohort_size"] == 2
    assert report.payload["forward_cohort"]["full_coverage"] is True
    assert result["state"] != "complete"


async def test_fixed_expiry_finalizes_unknown_without_network_and_both_reports_complete(tmp_path):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args):
        raise AssertionError("expired/complete work must not construct a client")

    worker = ResearchOutcomeWorker(sessions, forbidden, benchmark(tmp_path))
    first = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=48, seconds=60))
    assert set(first["reports"]) == {24}
    report = LocalRankArchive(tmp_path).read_report(session.parent_id, first["reports"][24])
    assert report.payload["original_cohort_size"] == 2
    assert report.payload["forward_cohort"]["ranks"] == {}
    assert all(
        outcome["status"] == "unavailable" for outcome in report.payload["outcomes"].values()
    )
    final = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=60))
    assert final["state"] == "complete" and set(final["reports"]) == {24, 48}
    await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=61))
    assert LocalShadowArchive(tmp_path).outcome_attempts(child.shadow_id, 24, "T0USDT") == ()


async def test_report_commit_before_link_recovers_exact_inputs_not_late_evidence(
    tmp_path, monkeypatch
):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args):
        raise AssertionError("finalization must be offline")

    worker = ResearchOutcomeWorker(sessions, forbidden, benchmark(tmp_path))
    original = sessions.store.append

    def crash_link(identifier, role, kind, details, at):
        if kind == "report_completed":
            raise OSError("injected death after report commit")
        return original(identifier, role, kind, details, at)

    monkeypatch.setattr(sessions.store, "append", crash_link)
    cutoff = ANCHOR + timedelta(hours=36)
    with pytest.raises(OSError):
        await worker.tick(session.session_id, now=cutoff)
    reservation = sessions.store.report_reservation(session.session_id, 24)
    saved = LocalRankArchive(tmp_path).read_report(session.parent_id, reservation["report_id"])
    from algotrader.application.shadow_budget import ShadowBudget
    from algotrader.domain.shadow_discovery import ShadowPolicy

    history = History(ShadowBudget(ShadowPolicy()), [])
    await ShadowObserver(LocalShadowArchive(tmp_path), history, pace_seconds=0).observe(
        child.shadow_id, 24, 2, now=cutoff - timedelta(seconds=1)
    )
    monkeypatch.setattr(sessions.store, "append", original)
    recovered = await worker.tick(session.session_id, now=cutoff + timedelta(seconds=1))
    assert recovered["reports"][24] == saved.report_id
    again = LocalRankArchive(tmp_path).read_report(session.parent_id, saved.report_id)
    assert again == saved
    assert again.manifest == reservation["inputs"]
    assert all(outcome["status"] == "unavailable" for outcome in again.payload["outcomes"].values())


async def test_external_cancel_and_durable_stop_close_market_without_unvisited_attempts(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    entered = asyncio.Event()

    class Blocked(History):
        async def minute_candles_between(self, instrument, start, end):
            self.budget.before_attempt()
            entered.set()
            await asyncio.Event().wait()

    markets = []

    def factory(budget, metadata):
        market = Blocked(budget, [])
        markets.append(market)
        return market

    worker = ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path))
    pending = asyncio.create_task(
        worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60))
    )
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert markets[0].closed
    assert LocalShadowArchive(tmp_path).outcome_attempts(child.shadow_id, 24, "T0USDT") == ()
    entered.clear()
    pending = asyncio.create_task(
        worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, minutes=5))
    )
    await asyncio.wait_for(entered.wait(), 2)
    sessions.stop(session.session_id, now=ANCHOR + timedelta(hours=24, minutes=5))
    result = await asyncio.wait_for(pending, 3)
    assert result["state"] == "stopped" and markets[-1].closed


@pytest.mark.parametrize("reason", ["source_mismatch", "storage_blocked", "stopped"])
async def test_blocked_workers_never_construct_clients(tmp_path, monkeypatch, reason):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args):
        raise AssertionError("client created while blocked")

    if reason == "source_mismatch":
        monkeypatch.setattr(sessions, "semantic_identity", lambda child: {})
    elif reason == "storage_blocked":
        monkeypatch.setattr(sessions, "archive_bytes", lambda root: 5 * 1024**3)
    else:
        sessions.stop(session.session_id, now=ANCHOR)
    result = await ResearchOutcomeWorker(sessions, forbidden, benchmark(tmp_path)).tick(
        session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60)
    )
    assert result["state"] == reason


async def test_cooldown_does_not_bypass_storage_guard_for_expired_reports(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    now = ANCHOR + timedelta(hours=36)
    sessions.record_response(
        session.session_id,
        "ranks",
        {"status_code": 429, "retry_after": "7200", "observed_weight_1m": None},
        at=now,
    )
    monkeypatch.setattr(sessions, "archive_bytes", lambda root: 5 * 1024**3)

    def forbidden(*args):
        raise AssertionError("storage-blocked work must stay idle")

    result = await ResearchOutcomeWorker(sessions, forbidden, benchmark(tmp_path)).tick(
        session.session_id, now=now
    )
    assert result["state"] == "storage_blocked"
    assert result["reports"] == {}
    assert not (tmp_path / "benchmarks").exists()


async def test_one_pass_reservation_survives_restart_without_retry_in_same_slot(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    now = ANCHOR + timedelta(hours=24, seconds=60)
    from uuid import uuid4

    sessions.store.append(
        session.session_id,
        "outcomes",
        "outcome_pass",
        {
            "phase": "reserved",
            "horizon_hours": 24,
            "slot": 288,
            "pass_id": str(uuid4()),
        },
        now,
    )

    def forbidden(*args):
        raise AssertionError("a reserved pass must never repeat after crash")

    result = await ResearchOutcomeWorker(sessions, forbidden, benchmark(tmp_path)).tick(
        session.session_id, now=now
    )
    assert result["reports"] == {}


async def test_total_pass_deadline_includes_iteration_and_cleanup(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    elapsed = [0.0]
    entered_cleanup = asyncio.Event()
    calls = []

    class SlowHistory(History):
        async def minute_candles_between(self, instrument, start, end):
            self.budget.before_attempt()
            calls.append(instrument.id.symbol)
            elapsed[0] = 91.0
            return minute_path()

        async def close(self):
            entered_cleanup.set()
            await asyncio.Event().wait()

    worker = ResearchOutcomeWorker(
        sessions,
        lambda budget, metadata: SlowHistory(budget, []),
        benchmark(tmp_path),
        monotonic=lambda: elapsed[0],
    )
    await asyncio.wait_for(
        worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60)), 3
    )
    assert calls == ["T0USDT"] and entered_cleanup.is_set()
    assert LocalShadowArchive(tmp_path).outcome_attempts(child.shadow_id, 24, "T0USDT") == ()
    terminal = [
        event for event in sessions.store.events(session.session_id) if event.kind == "outcome_pass"
    ][-1]
    assert terminal.details["accounting"]["attempt_limit"] == 30
    assert terminal.details["accounting"]["remaining_seconds"] == 0


async def test_guard_read_failure_does_not_invent_unvisited_symbol_attempts(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    calls = []

    def failed_guard(*args, **kwargs):
        raise OSError("cannot verify resource guard")

    def factory(budget, metadata):
        monkeypatch.setattr(sessions, "guard", failed_guard)
        return History(budget, calls)

    with pytest.raises(OSError):
        await ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path)).tick(
            session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60)
        )
    assert calls == []
    archive = LocalShadowArchive(tmp_path)
    assert archive.outcome_attempts(child.shadow_id, 24, "T0USDT") == ()
    assert archive.outcome_attempts(child.shadow_id, 24, "T1USDT") == ()


async def test_retry_expiry_bounds_in_progress_pass_and_pagination(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    elapsed, requests = [0.0], []

    async def respond(request):
        requests.append(request)
        elapsed[0] += 2.0
        # A full page would normally authorize a second pagination request.
        start = int(request.url.params["startTime"])
        return httpx.Response(
            200,
            json=[
                [
                    start + i * 60000,
                    "100",
                    "112",
                    "95",
                    "110",
                    "10",
                    start + (i + 1) * 60000 - 1,
                    "1000",
                ]
                for i in range(1000)
            ],
        )

    worker = ResearchOutcomeWorker(
        sessions,
        lambda budget, meta: ResearchMarketData(
            budget, meta, transport=httpx.MockTransport(respond)
        ),
        benchmark(tmp_path),
        monotonic=lambda: elapsed[0],
    )
    result = await worker.tick(session.session_id, now=ANCHOR + timedelta(hours=36, seconds=-1))
    assert len(requests) == 1
    assert 24 in result["reports"]
    assert LocalShadowArchive(tmp_path).outcome_attempts(child.shadow_id, 24, "T1USDT") == ()


async def test_missing_prefix_does_not_starve_unvisited_symbols_after_restart(tmp_path):
    sessions, session, child = setup_session(tmp_path, 40)
    calls = []

    async def respond(request):
        calls.append(request.url.params["symbol"])
        return httpx.Response(200, json=[])

    def factory(budget, metadata):
        return ResearchMarketData(budget, metadata, transport=httpx.MockTransport(respond))

    await ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path)).tick(
        session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60)
    )
    sessions = ResearchSessions(tmp_path)
    await ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path)).tick(
        session.session_id, now=ANCHOR + timedelta(hours=24, minutes=5)
    )
    assert len(calls) == 60 and len(set(calls)) == 40
    assert calls[30:40] == [f"T{i}USDT" for i in range(30, 40)]


async def test_cancelled_symbol_reservation_advances_fair_priority(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    entered, calls = asyncio.Event(), []

    async def blocked(request):
        calls.append(request.url.params["symbol"])
        entered.set()
        await asyncio.Event().wait()

    def factory(budget, metadata):
        return ResearchMarketData(budget, metadata, transport=httpx.MockTransport(blocked))

    worker = ResearchOutcomeWorker(sessions, factory, benchmark(tmp_path))
    pending = asyncio.create_task(
        worker.tick(session.session_id, now=ANCHOR + timedelta(hours=24, seconds=60))
    )
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert LocalShadowArchive(tmp_path).outcome_attempts(child.shadow_id, 24, "T0USDT") == ()

    async def missing(request):
        calls.append(request.url.params["symbol"])
        return httpx.Response(200, json=[])

    sessions = ResearchSessions(tmp_path)
    await ResearchOutcomeWorker(
        sessions,
        lambda budget, meta: ResearchMarketData(
            budget, meta, transport=httpx.MockTransport(missing)
        ),
        benchmark(tmp_path),
    ).tick(session.session_id, now=ANCHOR + timedelta(hours=24, minutes=5))
    assert calls[:2] == ["T0USDT", "T1USDT"]


async def test_report_input_preparation_does_not_block_worker_control_loop(tmp_path, monkeypatch):
    import threading

    sessions, session, child = setup_session(tmp_path)
    builder = benchmark(tmp_path)
    entered, release = threading.Event(), threading.Event()
    timed_out = []
    original = builder.freeze_inputs

    def slow_freeze(*args, **kwargs):
        entered.set()
        timed_out.append(not release.wait(timeout=1))
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "freeze_inputs", slow_freeze)
    worker = ResearchOutcomeWorker(
        sessions, lambda *args: pytest.fail("expired work is offline"), builder
    )
    pending = asyncio.create_task(worker.tick(session.session_id, now=ANCHOR + timedelta(hours=36)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        release.set()  # models an async control action during expensive report reads
        await pending
    finally:
        release.set()
        if not pending.done():
            await pending
    assert timed_out == [False]


async def test_report_preparation_cannot_extend_original_retry_window(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    elapsed = [0.0]
    builder = benchmark(tmp_path)
    original = builder.freeze_inputs

    def slow_prepare(*args, **kwargs):
        elapsed[0] = 2.0
        return original(*args, **kwargs)

    monkeypatch.setattr(builder, "freeze_inputs", slow_prepare)

    def forbidden(*args):
        pytest.fail("preparation exhausted the original retry window")

    result = await ResearchOutcomeWorker(
        sessions, forbidden, builder, monotonic=lambda: elapsed[0]
    ).tick(session.session_id, now=ANCHOR + timedelta(hours=36, seconds=-1))
    assert 24 in result["reports"]
