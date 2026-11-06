import asyncio
import os
import signal
from datetime import timedelta

import httpx
import pytest

from algotrader.adapters.binance.research import ResearchMarketData
from algotrader.application.research_sessions import ResearchSessions
from algotrader.jobs.research_worker import run_research_worker, worker_health
from tests.test_forward_outcomes import ANCHOR
from tests.test_research_outcome_worker import setup_session
from tests.test_shadow_discovery import ticker_fixture


@pytest.mark.parametrize("mode", ["stopped", "storage_blocked", "source_mismatch", "not_due"])
async def test_workers_never_build_markets_when_stopped_blocked_or_not_due(
    tmp_path, monkeypatch, mode
):
    sessions, session, child = setup_session(tmp_path)
    if mode == "stopped":
        sessions.stop(session.session_id, now=ANCHOR)
    elif mode == "storage_blocked":
        monkeypatch.setattr(
            ResearchSessions, "archive_bytes", staticmethod(lambda root: 5 * 1024**3)
        )
    elif mode == "source_mismatch":
        monkeypatch.setattr(ResearchSessions, "semantic_identity", lambda self, child: {})

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP constructed while idle/blocked")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    stop = asyncio.Event()
    waits = []

    async def wait(seconds, event):
        waits.append(seconds)
        event.set()

    assert (
        await run_research_worker(
            tmp_path, session.session_id, "outcomes", clock=lambda: ANCHOR, wait=wait, stop=stop
        )
        == 0
    )
    assert waits and 0 < waits[0] <= 30
    assert sessions.status(session.session_id, now=ANCHOR)["last_successful_arrival"] is None
    with sessions.store.role_lock("outcomes"):
        pass


async def test_duplicate_role_is_rejected_and_invalid_clock_fails_before_client(tmp_path):
    sessions, session, child = setup_session(tmp_path)
    with sessions.store.role_lock("ranks"), pytest.raises(RuntimeError):
        await run_research_worker(
            tmp_path, session.session_id, "ranks", clock=lambda: ANCHOR, stop=asyncio.Event()
        )
    with pytest.raises(ValueError):
        await run_research_worker(
            tmp_path,
            session.session_id,
            "ranks",
            clock=lambda: ANCHOR.replace(tzinfo=None),
            stop=asyncio.Event(),
        )


async def test_clock_jumps_do_not_repeat_slots_and_are_durable(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    parent = sessions._child(child.shadow_id)
    from algotrader.infrastructure.scan_archive import LocalScanArchive

    parent = LocalScanArchive(tmp_path).read_scan(session.parent_id)
    wall = [ANCHOR]
    elapsed = [0.0]
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                dict(row, closeTime=int(wall[0].timestamp() * 1000))
                for row in ticker_fixture(parent)
            ],
        )

    monkeypatch.setattr(
        "algotrader.jobs.research_worker.ResearchMarketData",
        lambda budget, metadata: ResearchMarketData(
            budget, metadata, transport=httpx.MockTransport(handler)
        ),
    )
    stop = asyncio.Event()
    iterations = 0

    async def wait(seconds, event):
        nonlocal iterations
        iterations += 1
        elapsed[0] += 1
        if iterations == 1:
            wall[0] = ANCHOR + timedelta(minutes=10)
        elif iterations == 2:
            wall[0] = ANCHOR
        else:
            event.set()

    assert (
        await run_research_worker(
            tmp_path,
            session.session_id,
            "ranks",
            clock=lambda: wall[0],
            monotonic=lambda: elapsed[0],
            wait=wait,
            stop=stop,
        )
        == 0
    )
    assert len(requests) == 2
    assert (
        len(
            [
                event
                for event in sessions.store.events(session.session_id)
                if event.kind == "clock_discontinuity"
            ]
        )
        == 2
    )
    assert (
        sessions.status(session.session_id, now=wall[0])["accounting"]["ranks"]["actual_attempts"]
        == 2
    )


async def test_sigterm_interrupts_idle_wait_and_releases_role_lock(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected HTTP constructor")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    entered = asyncio.Event()

    async def wait(seconds, stop):
        entered.set()
        await stop.wait()

    previous = signal.getsignal(signal.SIGTERM)
    pending = asyncio.create_task(
        run_research_worker(
            tmp_path, session.session_id, "outcomes", clock=lambda: ANCHOR, wait=wait
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
    os.kill(os.getpid(), signal.SIGTERM)
    assert await asyncio.wait_for(pending, 2) == 0
    assert signal.getsignal(signal.SIGTERM) == previous
    with sessions.store.role_lock("outcomes"):
        pass


def test_health_distinguishes_liveness_cooldown_and_success(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("health must stay offline")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    assert worker_health(tmp_path, session.session_id, "ranks", now=ANCHOR)["healthy"] is False
    with sessions.store.role_lock("ranks"):
        sessions.store.append(session.session_id, "ranks", "heartbeat", {}, ANCHOR)
        result = worker_health(tmp_path, session.session_id, "ranks", now=ANCHOR)
        assert result["healthy"] is True and result["collecting"] is False
        assert result["last_successful_arrival"] is None
        assert (
            worker_health(
                tmp_path, session.session_id, "ranks", now=ANCHOR + timedelta(seconds=361)
            )["healthy"]
            is False
        )
        sessions.record_response(
            session.session_id,
            "outcomes",
            {"status_code": 429, "retry_after": "7200", "observed_weight_1m": None},
            at=ANCHOR,
        )
        cooldown = worker_health(tmp_path, session.session_id, "ranks", now=ANCHOR)
        assert cooldown["healthy"] is False and cooldown["collecting"] is False
        assert cooldown["reason"] == "exchange_cooldown"


async def test_sigterm_during_http_persists_snapshot_before_unlock(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "algotrader.jobs.research_worker.ResearchMarketData",
        lambda budget, metadata: ResearchMarketData(
            budget, metadata, transport=httpx.MockTransport(handler)
        ),
    )
    previous = signal.getsignal(signal.SIGTERM)
    pending = asyncio.create_task(
        run_research_worker(tmp_path, session.session_id, "ranks", clock=lambda: ANCHOR)
    )
    await asyncio.wait_for(entered.wait(), 2)
    assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
    os.kill(os.getpid(), signal.SIGTERM)
    assert await asyncio.wait_for(pending, 3) == 0
    assert signal.getsignal(signal.SIGTERM) == previous
    result = sessions.status(session.session_id, now=ANCHOR)
    assert result["rank_slots"][0]["status"] == "failed"
    assert result["rank_slots"][0]["error"] == "CancelledError"
    assert result["last_successful_arrival"] is None
    with sessions.store.role_lock("ranks"):
        pass


async def test_slow_outcomes_do_not_delay_independent_rank_role(tmp_path, monkeypatch):
    sessions, session, child = setup_session(tmp_path)
    from algotrader.infrastructure.scan_archive import LocalScanArchive

    parent = LocalScanArchive(tmp_path).read_scan(session.parent_id)
    now = ANCHOR + timedelta(hours=24, minutes=5)
    outcome_entered = asyncio.Event()
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/fapi/v1/klines":
            outcome_entered.set()
            await asyncio.Event().wait()
        return httpx.Response(
            200,
            json=[
                dict(row, closeTime=int(now.timestamp() * 1000)) for row in ticker_fixture(parent)
            ],
        )

    monkeypatch.setattr(
        "algotrader.jobs.research_worker.ResearchMarketData",
        lambda budget, metadata: ResearchMarketData(
            budget, metadata, transport=httpx.MockTransport(handler)
        ),
    )
    rank_stop, outcome_stop = asyncio.Event(), asyncio.Event()

    async def rank_wait(seconds, stop):
        await asyncio.wait_for(outcome_entered.wait(), 2)
        stop.set()
        outcome_stop.set()

    outcomes = asyncio.create_task(
        run_research_worker(
            tmp_path, session.session_id, "outcomes", clock=lambda: now, stop=outcome_stop
        )
    )
    ranks = asyncio.create_task(
        run_research_worker(
            tmp_path, session.session_id, "ranks", clock=lambda: now, wait=rank_wait, stop=rank_stop
        )
    )
    assert await asyncio.wait_for(asyncio.gather(outcomes, ranks), 5) == [0, 0]
    assert paths.count("/fapi/v1/ticker/24hr") == 1
    assert paths.count("/fapi/v1/klines") == 1
    assert sessions.status(session.session_id, now=now)["rank_slots"][289]["status"] == "complete"


async def test_sigterm_during_commit_waits_for_durable_snapshot(tmp_path, monkeypatch):
    import threading

    from algotrader.infrastructure.rank_archive import LocalRankArchive
    from algotrader.infrastructure.scan_archive import LocalScanArchive

    sessions, session, child = setup_session(tmp_path)
    parent = LocalScanArchive(tmp_path).read_scan(session.parent_id)
    entered, release = threading.Event(), threading.Event()
    original = LocalRankArchive.write_snapshot

    def delayed_commit(archive, record):
        entered.set()
        assert release.wait(3)
        return original(archive, record)

    monkeypatch.setattr(LocalRankArchive, "write_snapshot", delayed_commit)

    async def handler(request):
        return httpx.Response(200, json=ticker_fixture(parent))

    monkeypatch.setattr(
        "algotrader.jobs.research_worker.ResearchMarketData",
        lambda budget, metadata: ResearchMarketData(
            budget, metadata, transport=httpx.MockTransport(handler)
        ),
    )
    pending = asyncio.create_task(
        run_research_worker(tmp_path, session.session_id, "ranks", clock=lambda: ANCHOR)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.05)
        assert not pending.done()
    finally:
        release.set()
    assert await asyncio.wait_for(pending, 3) == 0
    result = sessions.status(session.session_id, now=ANCHOR)
    assert result["rank_slots"][0]["status"] == "complete"
    assert result["last_successful_arrival"] == ANCHOR
