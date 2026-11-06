"""Real discovery-to-report acceptance; only public HTTP responses and UTC are controlled."""

from collections import Counter
from datetime import datetime, timedelta

import httpx
import pytest

from algotrader.adapters.binance.research import ResearchMarketData
from algotrader.application.rank_collector import RankCollector
from algotrader.application.research_outcome_worker import ResearchOutcomeWorker
from algotrader.application.research_rank_worker import ResearchRankWorker
from algotrader.application.research_sessions import ResearchSessions
from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.rank_archive import LocalRankArchive, digest
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_shadow_acceptance import AcceptanceMarket, capture_parent
from tests.test_shadow_discovery import NOW, ticker_fixture


async def test_real_child_session_restart_to_two_frozen_reports(settings, monkeypatch, tmp_path):
    # Catches repeated acquisition after a reserved/committed crash, horizon drift,
    # reduced-cohort ranking, or retrying a previously complete symbol path.
    parent = await capture_parent(settings, monkeypatch, limit=1)
    policy = ShadowPolicy(max_tokens=2)
    child = await ShadowRunner(AcceptanceMarket(policy), clock=lambda: NOW).run(parent, policy)
    assert [item["symbol"] for item in child.results if item["qualified"]] == ["T10USDT"]
    assert any(
        item["symbol"] == "T19USDT" and item["reason"] == "no_setup" for item in child.journal
    )
    parent_before = digest(parent)
    LocalScanArchive(tmp_path).write_scan(parent)
    LocalShadowArchive(tmp_path).write_run(child)
    service = ResearchSessions(tmp_path)
    session = service.start(child.shadow_id, now=child.completed_at)
    now = [session.anchor + timedelta(minutes=5)]
    requests = []

    class ProviderClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]

    monkeypatch.setattr("algotrader.adapters.binance.base.datetime", ProviderClock)

    async def respond(request):
        requests.append((request.url.path, dict(request.url.params)))
        assert request.method == "GET" and request.url.host == "fapi.binance.com"
        assert "X-MBX-APIKEY" not in request.headers and "signature" not in request.url.params
        if request.url.path == "/fapi/v1/ticker/24hr":
            rows = [
                dict(row, closeTime=int(now[0].timestamp() * 1000))
                for row in ticker_fixture(parent)
            ]
            return httpx.Response(200, json=rows)
        assert request.url.path == "/fapi/v1/klines" and request.url.params["interval"] == "1m"
        if request.url.params["symbol"] == "T19USDT":
            return httpx.Response(200, json=[])  # unavailable stays unknown, never a zero return
        start, end = int(request.url.params["startTime"]), int(request.url.params["endTime"])
        rows = [
            [opened, "100", "112", "95", "110", "10", opened + 59999, "1000", 1, "5", "500", "0"]
            for opened in range(start, min(end + 1, start + 1000 * 60000), 60000)
        ]
        return httpx.Response(200, json=rows)

    def market(budget, metadata):
        return ResearchMarketData(budget, metadata, transport=httpx.MockTransport(respond))

    def rank_worker(service):
        collector = RankCollector(
            tmp_path,
            RankPolicy(),
            clock=lambda: now[0],
            market_factory=lambda budget: market(budget, {}),
        )
        return ResearchRankWorker(service, collector), collector

    # Death immediately after reservation: restart must retain unknown, not reissue.
    service.store.reserve_rank(session.session_id, 1, now[0])
    service = ResearchSessions(tmp_path)
    rank, collector = rank_worker(service)
    result = await rank.tick(session.session_id, now=now[0])
    assert result["rank_slots"][1]["kind"] == "rank_interrupted" and requests == []

    # Death after snapshot publication but before journal linkage: recover exactly that ID.
    now[0] = session.anchor + timedelta(minutes=10)
    reserved = service.store.reserve_rank(session.session_id, 2, now[0])
    await collector.collect(session.parent_id, snapshot_id=reserved["snapshot_id"])
    service = ResearchSessions(tmp_path)
    rank, _ = rank_worker(service)
    result = await rank.tick(session.session_id, now=now[0])
    assert result["rank_slots"][2]["snapshot_id"] == reserved["snapshot_id"]
    assert len(requests) == 1
    now[0] = session.anchor + timedelta(minutes=20)
    result = await rank.tick(session.session_id, now=now[0])
    assert result["rank_slots"][3]["kind"] == "rank_missed"
    assert len(requests) == 2

    for horizon in (24, 48):
        now[0] = session.anchor + timedelta(hours=horizon, seconds=60)
        worker = ResearchOutcomeWorker(service, market, ShadowBenchmark(tmp_path, RankPolicy()))
        await worker.tick(session.session_id, now=now[0])
        now[0] += timedelta(minutes=5)
        await worker.tick(session.session_id, now=now[0])
        if horizon == 48:
            now[0] += timedelta(minutes=5)
            await worker.tick(session.session_id, now=now[0])
        before = Counter(params["symbol"] for path, params in requests if path.endswith("klines"))
        # Both horizons' complete paths must be cached; only unavailable evidence may retry.
        assert before["T0USDT"] == (2 if horizon == 24 else 5)
        now[0] = session.anchor + timedelta(hours=horizon + 12)
        original = service.store.append

        def die_after_report(identifier, role, kind, details, at, append=original):
            if kind == "report_completed":
                raise OSError("injected death after immutable report publication")
            return append(identifier, role, kind, details, at)

        with monkeypatch.context() as patch:
            patch.setattr(service.store, "append", die_after_report)
            with pytest.raises(OSError, match="injected death"):
                await worker.tick(session.session_id, now=now[0])
        reservation = service.store.report_reservation(session.session_id, horizon)
        saved = LocalRankArchive(tmp_path).read_report(parent.scan_id, reservation["report_id"])
        service = ResearchSessions(tmp_path)
        worker = ResearchOutcomeWorker(service, market, ShadowBenchmark(tmp_path, RankPolicy()))
        request_count = len(requests)
        result = await worker.tick(session.session_id, now=now[0] + timedelta(seconds=1))
        assert len(requests) == request_count
        assert result["reports"][horizon] == saved.report_id
        assert LocalRankArchive(tmp_path).read_report(parent.scan_id, saved.report_id) == saved
        assert saved.manifest == reservation["inputs"]
        assert saved.payload["original_cohort_size"] == 20
        assert saved.payload["forward_cohort"]["full_coverage"] is False
        assert saved.payload["forward_cohort"]["ranks"] == {}
        assert saved.payload["outcomes"]["T19USDT"]["status"] != "complete"
        assert saved.payload["outcomes"]["T10USDT"]["status"] == "complete"
    assert result["state"] == "complete" and set(result["reports"]) == {24, 48}
    assert service.store.read(session.session_id).anchor == session.anchor
    assert digest(LocalScanArchive(tmp_path).read_scan(parent.scan_id)) == parent_before
    assert LocalShadowArchive(tmp_path).read_run(child.shadow_id) == child
