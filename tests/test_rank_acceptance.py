from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import httpx

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.rank_collector import RankCollector
from algotrader.application.scan_replay import replay_scan
from algotrader.application.shadow_benchmark import ShadowBenchmark
from algotrader.application.shadow_observer import ShadowObserver
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.rank_observations import RankPolicy
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.rank_archive import LocalRankArchive
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_shadow_acceptance import AcceptanceMarket, capture_parent
from tests.test_shadow_discovery import NOW


async def test_real_child_rank_collection_to_offline_report(settings, monkeypatch, tmp_path):
    parent = await capture_parent(settings, monkeypatch, limit=1)

    class FullTickerMarket(AcceptanceMarket):
        async def decision_tickers(self):
            rows = await super().decision_tickers()
            return [
                dict(
                    r,
                    quoteVolume="1500000",
                    openTime=int((NOW - timedelta(days=1)).timestamp() * 1000),
                )
                for r in rows
            ]

    policy = ShadowPolicy(max_tokens=2)
    market = FullTickerMarket(policy)
    child = await ShadowRunner(market, clock=lambda: NOW).run(parent, policy)
    assert [r["symbol"] for r in child.results if r["qualified"]] == ["T10USDT"]
    assert any(e["symbol"] == "T19USDT" and e["reason"] == "no_setup" for e in child.journal)
    scans = LocalScanArchive(tmp_path)
    scans.write_scan(parent)
    shadows = LocalShadowArchive(tmp_path)
    shadows.write_run(child)
    parent_bytes = (tmp_path / "scans" / parent.scan_id / "payload.json").read_bytes()
    child_bytes = (tmp_path / "shadow" / child.shadow_id / "payload.json").read_bytes()
    latest = tmp_path / "screener_latest.json"
    latest.write_bytes(b"published-desk-unchanged")
    anchor = outcome_anchor(child.completed_at)
    for minute in (5, 10):
        stamp = anchor + timedelta(minutes=minute)
        rows = await market.decision_tickers()
        for row in rows:
            row["closeTime"] = int(stamp.timestamp() * 1000)
        if minute == 10:
            rows[10]["priceChangePercent"] = "100"

        async def handler(request, captured_rows=rows):
            return httpx.Response(200, json=captured_rows)

        result = await RankCollector(
            tmp_path,
            RankPolicy(),
            clock=lambda captured_stamp=stamp: captured_stamp,
            market_factory=lambda budget: ShadowMarketData(
                budget, transport=httpx.MockTransport(handler)
            ),
        ).collect(parent.scan_id)
        assert result.status == "complete"

    class History:
        async def minute_candles_between(self, instrument, start, end):
            shift = start - ANCHOR
            scale = Decimal("0.1022") if instrument.id.symbol == "T10USDT" else Decimal("1")
            return [
                replace(
                    bar,
                    opened_at=bar.opened_at + shift,
                    closed_at=bar.closed_at + shift,
                    open=bar.open * scale,
                    high=bar.high * scale,
                    low=bar.low * scale,
                    close=bar.close * scale,
                )
                for bar in minute_path()
            ]

    await ShadowObserver(shadows, History(), pace_seconds=0).observe(
        child.shadow_id, 24, 20, now=anchor + timedelta(hours=25)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("offline reporting constructed network")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    service = ShadowBenchmark(tmp_path, RankPolicy())
    report = service.build(child.shadow_id, 24, as_of=anchor + timedelta(hours=26))
    repeat = service.build(child.shadow_id, 24, as_of=report.as_of)
    assert report.payload == repeat.payload and report.manifest == repeat.manifest
    assert report.payload["initial"]["requested_at"] == child.timing["snapshot_requested_at"]
    assert report.payload["original_cohort_size"] == 20
    assert report.payload["forward_cohort"]["full_coverage"] is True
    assert report.payload["arms"]["child_longs"]["membership_counts"]["observed_membership"] == 1
    member = report.payload["timeline"]["members"]["T10USDT"]
    assert member["first_observed_membership"]["lag_from_child_seconds"] == 600
    assert report.payload["timeline"]["sampling"]["sufficient"] is False
    assert len(LocalRankArchive(tmp_path).snapshots(parent.scan_id)) == 2
    assert (tmp_path / "scans" / parent.scan_id / "payload.json").read_bytes() == parent_bytes
    assert (tmp_path / "shadow" / child.shadow_id / "payload.json").read_bytes() == child_bytes
    assert latest.read_bytes() == b"published-desk-unchanged"
    # Real nonempty production-quality core remains replayable after all research work.
    baseline = await capture_parent(settings, monkeypatch, limit=20)
    assert baseline.opportunities
    assert (await replay_scan(baseline)).matches
