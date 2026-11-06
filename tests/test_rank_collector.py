import asyncio
import json
import threading
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.rank_collector import RankCollector
from algotrader.cli import main
from algotrader.domain.rank_observations import RankPolicy
from algotrader.infrastructure.rank_archive import LocalRankArchive
from algotrader.infrastructure.scan_archive import LocalScanArchive
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


async def test_optional_snapshot_id_is_validated_and_existing_snapshot_is_reused(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json=ticker_fixture(parent))

    service = collector(tmp_path, handler)
    with pytest.raises(ValueError):
        await service.collect(parent.scan_id, snapshot_id="../unsafe")
    assert not seen
    identifier = str(uuid4())
    first = await service.collect(parent.scan_id, snapshot_id=identifier)
    assert first.snapshot_id == identifier
    assert await service.collect(parent.scan_id, snapshot_id=identifier) == first
    assert len(seen) == 1
    assert (await service.collect(parent.scan_id)).snapshot_id != identifier


def collector(tmp_path, handler, policy=None):
    return RankCollector(
        tmp_path,
        policy or RankPolicy(),
        clock=lambda: NOW,
        market_factory=lambda budget: ShadowMarketData(
            budget, transport=httpx.MockTransport(handler)
        ),
    )


async def test_old_parent_collects_once_without_core_writes(tmp_path):
    parent = parent_fixture()
    parent = replace(
        parent,
        started_at=NOW - timedelta(days=2, seconds=20),
        completed_at=NOW - timedelta(days=2),
        identity={"old": "source"},
    )
    scans = LocalScanArchive(tmp_path)
    scans.write_scan(parent)
    latest = tmp_path / "latest.json"
    latest.write_bytes(b"untouched")
    before = (tmp_path / "scans" / parent.scan_id / "payload.json").read_bytes()
    seen = []

    async def handler(request):
        seen.append((request.method, request.url.path, request.url.query))
        return httpx.Response(
            200, json=ticker_fixture(parent), headers={"x-mbx-used-weight-1m": "40"}
        )

    result = await collector(tmp_path, handler).collect(parent.scan_id)
    assert result.status == "complete"
    assert seen == [("GET", "/fapi/v1/ticker/24hr", b"")]
    assert result.accounting["attempts"] == 1
    assert result.accounting["observed_weight_1m"] == 40
    assert (tmp_path / "scans" / parent.scan_id / "payload.json").read_bytes() == before
    assert latest.read_bytes() == b"untouched"
    assert LocalRankArchive(tmp_path).read_snapshot(parent.scan_id, result.snapshot_id) == result


async def test_actual_retries_stop_at_three_and_failed_attempt_is_saved(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)
    seen = []

    async def handler(request):
        seen.append(request)
        raise httpx.ConnectError("offline", request=request)

    result = await collector(tmp_path, handler).collect(parent.scan_id)
    assert result.status == "failed"
    assert len(seen) == result.accounting["attempts"] == 3
    assert LocalRankArchive(tmp_path).snapshots(parent.scan_id)[0].status == "failed"


@pytest.mark.parametrize("response,status", [({}, "failed"), ([], "partial")])
async def test_malformed_or_missing_snapshot_is_not_complete(tmp_path, response, status):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)

    async def handler(request):
        return httpx.Response(200, json=response)

    assert (await collector(tmp_path, handler).collect(parent.scan_id)).status == status


async def test_timeout_and_external_cancellation_are_archived(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)

    async def hanging(request):
        await asyncio.Event().wait()

    result = await collector(tmp_path, hanging, RankPolicy(deadline_seconds=1)).collect(
        parent.scan_id
    )
    assert result.status == "failed"

    async def cancelled(request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await collector(tmp_path, cancelled).collect(parent.scan_id)
    assert len(LocalRankArchive(tmp_path).snapshots(parent.scan_id)) == 2
    assert all(r.status == "failed" for r in LocalRankArchive(tmp_path).snapshots(parent.scan_id))


async def test_invalid_parent_rejected_before_market_creation(tmp_path):
    parent = replace(parent_fixture(), status="failed")
    LocalScanArchive(tmp_path).write_scan(parent)

    def forbidden(budget):
        raise AssertionError("network constructed")

    with pytest.raises(ValueError):
        await RankCollector(tmp_path, RankPolicy(), market_factory=forbidden).collect(
            parent.scan_id
        )


async def test_concurrent_collectors_do_not_share_budget(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)

    async def handler(request):
        return httpx.Response(200, json=ticker_fixture(parent))

    results = await asyncio.gather(
        *(collector(tmp_path, handler).collect(parent.scan_id) for _ in range(2))
    )
    assert len({r.snapshot_id for r in results}) == 2
    assert [r.accounting["attempts"] for r in results] == [1, 1]


def test_snapshot_cli_reports_archive_identity_and_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "atlas",
            "snapshot-ranks",
            "--scan-id",
            parent_fixture().scan_id,
            "--archive-dir",
            str(tmp_path),
        ],
    )
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


async def test_cleanup_is_bounded_and_cancellation_during_cleanup_is_archived(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)
    entered = asyncio.Event()

    class SlowClose:
        snapshot_started_at = snapshot_completed_at = NOW

        async def decision_tickers(self):
            return ticker_fixture(parent)

        async def close(self):
            entered.set()
            await asyncio.Event().wait()

    service = RankCollector(
        tmp_path,
        RankPolicy(deadline_seconds=1),
        clock=lambda: NOW,
        market_factory=lambda budget: SlowClose(),
    )
    task = asyncio.create_task(service.collect(parent.scan_id))
    await entered.wait()
    done, _ = await asyncio.wait({task}, timeout=1.5)
    if not done:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert done, "cleanup exceeded the one-second invocation budget"
    assert task.result().status == "failed"
    entered.clear()
    task = asyncio.create_task(service.collect(parent.scan_id))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = LocalRankArchive(tmp_path).snapshots(parent.scan_id)
    assert len(saved) == 2 and all(r.status == "failed" for r in saved)


async def test_cancellation_during_persistence_waits_for_durable_record(tmp_path, monkeypatch):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = LocalRankArchive.write_snapshot

    def slow_write(self, record):
        entered.set()
        release.wait(timeout=3)
        result = original(self, record)
        finished.set()
        return result

    monkeypatch.setattr(LocalRankArchive, "write_snapshot", slow_write)

    async def handler(request):
        return httpx.Response(200, json=ticker_fixture(parent))

    task = asyncio.create_task(collector(tmp_path, handler).collect(parent.scan_id))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    waiting_for_commit = not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 2)
    assert waiting_for_commit, "cancellation escaped before durable publication"
    assert len(LocalRankArchive(tmp_path).snapshots(parent.scan_id)) == 1


async def test_cancel_in_cleanup_persists_failed_record_before_propagating(tmp_path):
    parent = parent_fixture()
    LocalScanArchive(tmp_path).write_scan(parent)
    entered = asyncio.Event()

    class CancelClose:
        snapshot_started_at = snapshot_completed_at = NOW

        async def decision_tickers(self):
            return ticker_fixture(parent)

        async def close(self):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        RankCollector(
            tmp_path, RankPolicy(), clock=lambda: NOW, market_factory=lambda budget: CancelClose()
        ).collect(parent.scan_id)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    records = LocalRankArchive(tmp_path).snapshots(parent.scan_id)
    assert len(records) == 1
    assert records[0].status == "failed" and records[0].error == "CancelledError"
