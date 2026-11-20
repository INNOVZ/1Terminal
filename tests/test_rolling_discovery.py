from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from algotrader.application.activity_recovery import ActivityRecoveryScreener
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.rolling_discovery import (
    ROLLING_POLICY,
    CachedDiscoveryMarket,
    latest_open,
    rolling_cycle,
)
from algotrader.domain.discovery_selection import activity_watch_selection, scout_selection
from algotrader.domain.models import Candle, Direction, MarketKind, Quote, Venue
from algotrader.infrastructure.rolling_discovery import RollingDiscoveryStore, pack, unpack
from scripts import research_prospective as study
from scripts import research_recovery_study as recovery
from tests.conftest import make_instrument
from tests.test_capture_evidence import Macro
from tests.test_recovery_screener import AT, derivative, recovery_history


def test_activity_watch_and_coverage_are_bounded_and_reproducible():
    instruments = [make_instrument(symbol=f"C{i}USDT") for i in range(100)]
    quotes = {i.id.symbol: Quote(i.id, Decimal(".999"), Decimal("1.001"), Decimal("1"),
              Decimal("900000"), observed_at=AT, high_24h=Decimal("1.1"), low_24h=Decimal(".8"),
              price_change_pct_24h=Decimal(index % 9)) for index, i in enumerate(instruments)}
    first = activity_watch_selection([], instruments, quotes, 20, AT)
    assert len(first) == len({i.id for i in first}) == 20
    assert first == activity_watch_selection([], instruments, quotes, 20, AT)
    assert all(quotes[i.id.symbol].price_change_pct_24h >= 7 for i in first[:12])
    selected = scout_selection(instruments, quotes, {}, AT)
    assert len(selected) == 64
    seen = {i.id.symbol: AT for i in selected}
    later_at = AT + timedelta(minutes=15)
    assert scout_selection(instruments, quotes, seen, later_at) == ()
    fresh = {s: replace(q, observed_at=later_at) for s, q in quotes.items()}
    later = scout_selection(instruments, fresh, seen, later_at)
    assert {i.id for i in instruments} <= {i.id for i in (*selected, *later)}


def test_long_probe_admission_refreshes_without_altering_control(settings):
    inst = make_instrument()
    quote = Quote(inst.id, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("2000000"),
                  observed_at=AT, funding_rate=Decimal(".0004"))
    policy = ActivityRecoveryScreener(settings, evaluated_at=AT, decision_at=AT)
    assert policy._probe_rejection_reason(quote, Direction.LONG)
    assert policy._probe_rejection_reason(replace(quote, funding_rate=Decimal(".0001")), Direction.LONG) is None
    assert policy._probe_rejection_reason(replace(quote, observed_at=AT-timedelta(seconds=601)), Direction.LONG) is None
    assert policy._probe_rejection_reason(quote, Direction.SHORT) is None
    assert CryptoOpportunityScreener(settings)._probe_rejection_reason(quote, Direction.LONG) is None


class Timeline:
    venue = Venue.BINANCE_USDM
    def __init__(self):
        self.at = AT + timedelta(minutes=1)
        self.instrument = make_instrument(symbol="RECUSDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
        self.btc = make_instrument(symbol="BTCUSDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
        self.cohort = (self.instrument,)
        hourly, four = recovery_history()
        self.hourly = [replace(c, opened_at=c.opened_at+timedelta(hours=4),
                              closed_at=c.closed_at+timedelta(hours=4)) for c in hourly]
        self.four = [replace(c, opened_at=c.opened_at+timedelta(hours=4),
                            closed_at=c.closed_at+timedelta(hours=4)) for c in four]
        self.calls = []
        self.rev = False

    async def instruments(self):
        return (*self.cohort, self.btc)

    async def quotes(self, instruments):
        price = Decimal("100.8") if self.at >= AT+timedelta(hours=4,minutes=15) else Decimal("100.5")
        return [Quote(i.id, price-Decimal(".01"), price+Decimal(".01"), price, Decimal("5000000"),
                      observed_at=self.at, high_24h=Decimal("101"), low_24h=Decimal("98"),
                      price_change_pct_24h=Decimal("1"), funding_rate=Decimal(".00005")) for i in instruments]

    async def candles(self, instrument, interval, limit):
        self.calls.append((instrument.id.symbol, interval, limit))
        if interval == "1w":
            end = latest_open(self.at, "1w")+timedelta(days=7)
            return [Candle(end-timedelta(weeks=n+1),end-timedelta(weeks=n),Decimal("100"),
                           Decimal("101"),Decimal("99"),Decimal("100"),Decimal("100"),Decimal("10000"))
                    for n in reversed(range(limit))]
        if interval == "15m":
            history = []
            for c in self.hourly[-36:]:
                for quarter in range(4):
                    opened = c.opened_at+timedelta(minutes=15*quarter)
                    start = c.open+(c.close-c.open)*Decimal(quarter)/4
                    close = c.open+(c.close-c.open)*Decimal(quarter+1)/4
                    history.append(Candle(opened,opened+timedelta(minutes=15),start,
                        max(start,close,c.high if quarter==3 else close),
                        min(start,close,c.low if quarter==0 else close),close,c.volume/4,c.quote_volume/4))
            for index in range(12):
                opened = AT+timedelta(hours=4,minutes=15*index)
                price = Decimal("100.6")+Decimal(index)*Decimal(".05")
                history.append(Candle(opened,opened+timedelta(minutes=15),price,price+Decimal(".1"),
                                      price-Decimal(".1"),price+Decimal(".05"),Decimal("100"),Decimal("11000")))
        else:
            history = self.hourly if interval == "1h" else self.four
        history = [c for c in history if c.closed_at <= self.at]
        if instrument.id == self.btc.id:
            history = [replace(c, open=Decimal("100"), close=Decimal("100"), high=Decimal("101"), low=Decimal("99")) for c in history]
        if self.rev and history:
            history[-1] = replace(history[-1], close=history[-1].close+Decimal(".01"))
        return history[-limit:]

    async def derivatives_snapshot(self, instrument):
        return replace(derivative(), observed_at=self.at)


@pytest.fixture
async def rolling_study(tmp_path, settings):
    root = tmp_path / "rolling"
    root.mkdir()
    market = Timeline()
    macro = replace(await Macro().get_context(),observed_at=AT)
    definition = {"schema":"atlas-paired-study-v1","study_id":"rolling-fixture","issued_at":AT,
        "identity":study.identity(),"policy":dict(study.POLICY),"cohort":market.cohort,
        "arms":{"control":{"engine":(),"radar":(),"decisions":()}},"momentum_reference":(),
        "recovery":{"policy":dict(recovery.POLICY),"seeds":(),"admissions":(),
            "rolling":{"policy":dict(ROLLING_POLICY),"benchmark":market.btc,"macro":macro,
                "configuration":{"excluded_base_assets":settings.excluded_base_assets,
                                 "support_resistance_lookback_bars":settings.support_resistance_lookback_bars}}}}
    study.save(root/"definition.json",definition)
    enrollment = study.enroll(root,now=AT)
    store = recovery.initialize(root,definition)
    return root, definition, enrollment, market, store


async def test_new_recovery_qualifies_hours_after_issuance_and_survives_restart(rolling_study):
    root, definition, enrollment, market, store = rolling_study
    assert store.episodes() == ()
    early = await rolling_cycle(market,market,store,definition,"initial",clock=lambda:market.at)
    assert early["admissions"] == ()
    market.at = AT+timedelta(hours=4,minutes=1)
    result = await rolling_cycle(market,market,store,definition,"later",clock=lambda:market.at)
    assert result["admissions"] and result["admissions"][0]["status"] == "enrolled"
    episode, = store.episodes()
    assert episode.first_seen == market.at and episode.source_at == market.at
    assert result["control_symbols"] == () and "RECUSDT" in result["challenger_symbols"]
    before = len(market.calls)
    assert await rolling_cycle(market,market,store,definition,"later",clock=lambda:market.at) == result
    assert len(market.calls) == before
    reopened = recovery.initialize(root,definition)
    assert reopened.episodes() == store.episodes()
    market.at = AT+timedelta(hours=4,minutes=31)
    await rolling_cycle(market,market,reopened,definition,"confirm",clock=lambda:market.at)
    confirmed, = reopened.episodes()
    assert confirmed.triggered_at == market.at
    context = recovery.snapshot(root,definition,enrollment["endpoint_24h"])
    assert context[0][confirmed.episode_id].triggered_at == market.at
    assert context[1]["rolling_decisions"]


async def test_incremental_cache_rejects_mutation_and_future_state(rolling_study):
    _, definition, _, market, episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    state = RollingDiscoveryStore(episodes)
    first = CachedDiscoveryMarket(market,market,state,market.cohort,market.btc,market.at)
    candles = await first.candles(market.instrument,"15m",96)
    count = len(market.calls)
    assert await first.candles(market.instrument,"15m",96) == candles
    assert len(market.calls) == count
    market.at += timedelta(minutes=15)
    later = CachedDiscoveryMarket(market,market,state,market.cohort,market.btc,market.at)
    await later.candles(market.instrument,"15m",96)
    assert market.calls[-1][2] < 10
    with pytest.raises(ValueError,match="future cached"):
        await first.candles(market.instrument,"15m",96)
    payload,digest=pack({"a":1})
    with pytest.raises(ValueError):
        unpack(payload+b"bad",digest)


async def test_paired_decision_replay_and_unknown_inputs_are_explicit(rolling_study):
    from algotrader.application.rolling_discovery import verify_discovery_frame
    from scripts.research_verified_replay import offline
    _, definition, _, market, episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    await rolling_cycle(market,market,episodes,definition,"decision",clock=lambda:market.at)
    frame = RollingDiscoveryStore(episodes).frame("decision")
    with offline():
        await verify_discovery_frame(frame,definition["recovery"]["rolling"]["configuration"])
    altered = {**frame,"challenger":{**frame["challenger"],"opportunities":()}}
    with offline(), pytest.raises(ValueError,match="replay mismatch"):
        await verify_discovery_frame(altered,definition["recovery"]["rolling"]["configuration"])


async def test_atomic_capacity_and_decision_conflicts(rolling_study):
    from algotrader.infrastructure.recovery_episodes import episode_values
    _, definition, _, market, episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    await rolling_cycle(market,market,episodes,definition,"first",clock=lambda:market.at)
    state = RollingDiscoveryStore(episodes)
    frame = state.frame("first")
    first, = episodes.episodes()
    other_inst = replace(first.instrument,id=replace(first.instrument.id,symbol="OTHERUSDT"),base_asset="OTHER")
    other = replace(first,episode_id="different-episode",instrument=other_inst)
    source = frame["seeds"][0]
    record = {"state":episode_values(other),"evidence":{"kind":"enrollment","candidate":{**source["evidence"]["candidate"],"symbol":"OTHERUSDT"}}}
    second = {**frame,"decision_id":"second","seeds":(record,)}
    result = state.admit((record,),second,total_capacity=1)
    assert result["admissions"][0]["status"] == "study_episode_capacity"
    assert len(episodes.episodes()) == 1
    assert state.admit((record,),second,total_capacity=1) == result
    with pytest.raises(ValueError,match="decision conflict"):
        state.admit((record,),{**second,"issued_at":market.at+timedelta(seconds=1)},total_capacity=1)


async def test_unknown_flow_blocks_new_qualification(rolling_study):
    _, definition, _, market, episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    async def unavailable(_):
        return None
    market.derivatives_snapshot = unavailable
    result = await rolling_cycle(market,market,episodes,definition,"unknown",clock=lambda:market.at)
    assert result["admissions"] == () and episodes.episodes() == ()


def test_positioning_subgroups_preserve_recovery_flow_policy():
    from algotrader.domain.discovery_selection import positioning_regime
    from algotrader.domain.recovery import recovery_flow_confirmed
    base = derivative()
    negative = replace(base,open_interest_change_1h=Decimal("-.02"),open_interest_change_4h=Decimal("0"),
                       taker_buy_sell_ratio_4h=Decimal("1"))
    assert recovery_flow_confirmed(negative,AT)
    assert positioning_regime(negative,AT) == "oi_contracting"
    assert positioning_regime(base,AT) == "oi_expanding"
    assert positioning_regime(replace(base,open_interest_change_1h=Decimal("0")),AT) == "oi_flat"
    assert positioning_regime(None,AT) == "unknown"


async def test_rolling_capture_prepare_cli_contract(tmp_path):
    from types import SimpleNamespace

    import httpx

    from scripts.research_capture_evidence import execute
    from scripts.research_evidence_stream import write_bundle
    from tests.test_capture_evidence import public_response
    args = SimpleNamespace(max_attempts=200,deadline_seconds=60,max_symbols=320,
        max_supplemental=120,discovery_v2=True,recovery=True,rolling_recovery=True)
    bundle = await execute(args,transport=httpx.MockTransport(public_response),macro_provider=Macro())
    assert bundle["status"] == "captured" and bundle["rolling_policy"] == ROLLING_POLICY
    capture = tmp_path/"capture.jsonl"
    write_bundle(capture,bundle)
    root = tmp_path/"prepared"
    await study.prepare(capture,root)
    definition = study.definition(root)
    assert definition["recovery"]["rolling"]["policy"] == ROLLING_POLICY
    assert definition["recovery"]["rolling"]["benchmark"].id.symbol == "BTCUSDT"
    assert not (root/"enrollment.json").exists()


async def test_same_closed_hour_signal_cannot_be_reenrolled(rolling_study):
    _, definition, _, market, episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    await rolling_cycle(market,market,episodes,definition,"first",clock=lambda:market.at)
    rolling = RollingDiscoveryStore(episodes)
    first = rolling.frame("first")
    original, = episodes.episodes()
    # Simulate an observed invalidation without altering its frozen entry/stop.
    terminal = replace(original,status="invalidated",ended_at=market.at+timedelta(minutes=1),
                       checked_at=market.at+timedelta(minutes=1),revision=1)
    episodes.commit(original,terminal,{"kind":"fixture"})
    seed = first["seeds"][0]
    other = {**seed,"state":{**seed["state"],"episode_id":"same-signal-new-episode"},
             "evidence":{**seed["evidence"],"signal_key":"changed-plan-same-hour"}}
    frame = {**first,"decision_id":"retry","seeds":(other,)}
    result = rolling.admit((other,),frame)
    assert result["admissions"][0]["status"] == "signal_already_observed"
    assert len(episodes.episodes()) == 1


async def test_maximum_scout_batch_and_deep_budget(rolling_study,tmp_path):
    import copy
    root, base, _, market, _ = rolling_study
    cohort = tuple(replace(market.instrument,id=replace(market.instrument.id,symbol=f"C{i}USDT"),base_asset=f"C{i}") for i in range(64))
    definition = copy.deepcopy(base)
    definition["study_id"] = "bounded-batch"
    definition["cohort"] = cohort
    fresh_root = tmp_path/"batch"
    fresh_root.mkdir()
    study.save(fresh_root/"definition.json",definition)
    study.enroll(fresh_root,now=AT)
    episodes = recovery.initialize(fresh_root,definition)
    market.cohort = cohort
    market.at = AT+timedelta(hours=4,minutes=1)
    result = await rolling_cycle(market,market,episodes,definition,"batch",clock=lambda:market.at)
    frame = RollingDiscoveryStore(episodes).frame("batch")
    assert len(result["admissions"]) == 4
    assert frame["scout_counts"]["selected"] == 64
    assert len(frame["scout_inputs"]) == 64
    assert len(frame["pool"]) == 4
    assert len(frame["seeds"]) == 4
    assert len(episodes.episodes()) == 4
    assert len(market.calls) < 90
    assert len(pack(frame)[0]) < 2 * 1024**2


async def test_report_censors_late_alerts_and_groups_positioning(rolling_study):
    root,definition,enrollment,market,episodes = rolling_study
    market.at = AT+timedelta(hours=4,minutes=1)
    await rolling_cycle(market,market,episodes,definition,"qualify",clock=lambda:market.at)
    market.at += timedelta(minutes=30)
    await rolling_cycle(market,market,episodes,definition,"confirm",clock=lambda:market.at)
    context = recovery.snapshot(root,definition,enrollment["endpoint_24h"])
    episode, = episodes.episodes()
    ranks = {"RECUSDT":{"first_observed_top10_at":AT+timedelta(hours=6),"initial_inside":False,
                         "endpoint_price":Decimal("105")}}
    matches = {episode.episode_id:{"at":episode.triggered_at-timedelta(minutes=1),"inside":False}}
    result = recovery.outcome_report(root,context,definition,enrollment,enrollment["endpoint_24h"],ranks,matches)
    assert result["alert_relative_horizons"]["24"]["censored_count"] == 1
    assert result["alert_relative_horizons"]["48"]["censored_count"] == 1
    assert result["positioning_groups"]["oi_expanding"]["confirmed_count"] == 1
    assert result["outcomes"][0]["observed_early_detection"] is True


async def test_concurrent_live_transport_rank_recovery_and_reporting(tmp_path, settings, monkeypatch):
    import algotrader.adapters.binance.base as binance_base_mod
    import algotrader.adapters.binance.futures as binance_futures_mod
    import algotrader.domain.models as models_mod
    import scripts.research_capture_evidence as capture_mod
    from scripts.research_capture_evidence import EvidenceTransport as BaseEvidenceTransport

    recovery_at = AT + timedelta(hours=4, minutes=1)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return recovery_at if tz is None else recovery_at.astimezone(tz)

    monkeypatch.setattr(models_mod, "datetime", FixedDatetime)
    monkeypatch.setattr(binance_base_mod, "datetime", FixedDatetime)
    monkeypatch.setattr(binance_futures_mod, "datetime", FixedDatetime)

    root = tmp_path / "concurrent_live"
    root.mkdir()
    timeline = Timeline()
    quarters = []
    for c in timeline.hourly[-36:]:
        for quarter in range(4):
            opened = c.opened_at + timedelta(minutes=15 * quarter)
            start = c.open + (c.close - c.open) * Decimal(quarter) / 4
            close = c.open + (c.close - c.open) * Decimal(quarter + 1) / 4
            quarters.append(
                Candle(
                    opened,
                    opened + timedelta(minutes=15),
                    start,
                    max(start, close, c.high if quarter == 3 else close),
                    min(start, close, c.low if quarter == 0 else close),
                    close,
                    c.volume / 4,
                    c.quote_volume / 4,
                    taker_buy_volume=c.volume * Decimal("0.15"),
                    taker_buy_quote_volume=c.quote_volume * Decimal("0.15"),
                )
            )

    cohort = tuple(
        replace(timeline.instrument, id=replace(timeline.instrument.id, symbol=f"LIVE{i}USDT"), base_asset=f"LIVE{i}")
        for i in range(6)
    )
    btc = timeline.btc
    macro = replace(await Macro().get_context(), observed_at=AT)
    definition = {
        "schema": "atlas-paired-study-v1",
        "study_id": "concurrent-live-fixture",
        "issued_at": AT,
        "identity": study.identity(),
        "policy": dict(study.POLICY),
        "cohort": cohort,
        "arms": {"control": {"engine": (), "radar": (), "decisions": ()}},
        "momentum_reference": (),
        "recovery": {
            "policy": dict(recovery.POLICY),
            "seeds": (),
            "admissions": (),
            "rolling": {
                "policy": dict(ROLLING_POLICY),
                "benchmark": btc,
                "macro": macro,
                "configuration": {
                    "excluded_base_assets": settings.excluded_base_assets,
                    "support_resistance_lookback_bars": settings.support_resistance_lookback_bars,
                },
            },
        },
    }
    study.save(root / "definition.json", definition)
    enrollment = study.enroll(root, now=AT)
    assert enrollment["endpoint_120h"] == enrollment["anchor"] + timedelta(hours=120)
    recovery.initialize(root, definition)
    for slot, at in ((0, enrollment["anchor"]), (288, enrollment["endpoint_24h"])):
        rows = tuple(
            {
                "symbol": i.id.symbol,
                "priceChangePercent": str(index),
                "lastPrice": "100.5",
                "quoteVolume": "5000000",
                "closeTime": int(at.timestamp() * 1000),
                "openTime": int((at - timedelta(hours=24)).timestamp() * 1000),
            }
            for index, i in enumerate(cohort)
        )
        study.save(
            root / "snapshots" / f"{slot:06d}.json",
            {
                "study_id": definition["study_id"],
                "definition_sha256": enrollment["definition_sha256"],
                "slot": slot,
                "status": "observed",
                "arrived_at": at,
                "rows": rows,
                "normalized": study.normalize_ranks(cohort, rows, at),
            },
        )

    transports_seen = []

    class TrackingTransport(BaseEvidenceTransport):
        def __init__(self, inner, **kwargs):
            super().__init__(inner, **kwargs)
            transports_seen.append(self)

    monkeypatch.setattr(capture_mod, "EvidenceTransport", TrackingTransport)

    all_symbols = tuple(i.id.symbol for i in (*cohort, btc))
    now_ms = int(recovery_at.timestamp() * 1000)
    rank_at = enrollment["endpoint_24h"] + timedelta(minutes=5)
    rank_ms = int(rank_at.timestamp() * 1000)

    def kline_rows(series, interval_ms):
        return [
            [
                int(c.opened_at.timestamp() * 1000),
                str(c.open),
                str(c.high),
                str(c.low),
                str(c.close),
                str(c.volume),
                int(c.opened_at.timestamp() * 1000) + interval_ms - 1,
                str(c.quote_volume),
                1,
                str(c.taker_buy_volume),
                str(c.taker_buy_quote_volume),
                "0",
            ]
            for c in series
        ]

    async def recovery_async_response(request):
        await asyncio.sleep(0.002)
        path, params = request.url.path, request.url.params
        if path.endswith("ticker/24hr"):
            value = [
                {
                    "symbol": s,
                    "quoteVolume": "5000000",
                    "highPrice": "101",
                    "lowPrice": "98",
                    "lastPrice": "100.5",
                    "priceChangePercent": "1" if s == "LIVE0USDT" else "0",
                    "openTime": now_ms - 86_400_000,
                    "closeTime": now_ms,
                }
                for s in all_symbols
            ]
        elif path.endswith("ticker/bookTicker"):
            value = [{"symbol": s, "bidPrice": "100.49", "askPrice": "100.51", "time": now_ms} for s in all_symbols]
        elif path.endswith("premiumIndex"):
            def prem(s):
                return {
                    "symbol": s,
                    "lastFundingRate": "0.00005",
                    "markPrice": "100.5",
                    "indexPrice": "100.5",
                    "time": now_ms,
                    "nextFundingTime": (now_ms // 14_400_000 + 1) * 14_400_000,
                }
            value = prem(params["symbol"]) if "symbol" in params else [prem(s) for s in all_symbols]
        elif path.endswith("klines"):
            interval = params["interval"]
            symbol = params["symbol"]
            limit = int(params["limit"])
            ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1w": 604_800_000}[interval]
            if interval == "1w":
                end = latest_open(recovery_at, "1w") + timedelta(days=7)
                series = [
                    Candle(end - timedelta(weeks=n + 1), end - timedelta(weeks=n), Decimal("100"),
                           Decimal("101"), Decimal("99"), Decimal("100"), Decimal("100"), Decimal("10000"))
                    for n in reversed(range(limit))
                ]
            else:
                series = {"15m": quarters, "1h": timeline.hourly, "4h": timeline.four}[interval][-limit:]
                if symbol != "LIVE0USDT":
                    series = [
                        replace(c, open=Decimal("100"), close=Decimal("100"), high=Decimal("101"),
                                low=Decimal("99"), volume=Decimal("100"), quote_volume=Decimal("10000"))
                        for c in series
                    ]
            value = kline_rows(series, ms)
        elif path.endswith("openInterest"):
            value = {"symbol": params["symbol"], "openInterest": "100000", "time": now_ms}
        elif path.endswith("openInterestHist"):
            latest = now_ms // 3_600_000 * 3_600_000
            value = [
                {"timestamp": latest - (199 - i) * 3_600_000, "sumOpenInterest": str(100 + i)}
                for i in range(200)
            ]
        elif path.endswith("takerlongshortRatio"):
            latest = now_ms // 3_600_000 * 3_600_000 - 3_600_000
            value = [
                {"timestamp": latest - i * 3_600_000, "buyVol": "120", "sellVol": "100"}
                for i in range(26)
            ]
        elif path.endswith("depth"):
            value = {"bids": [["100.49", "10000"]], "asks": [["100.51", "10000"]]}
        else:
            pytest.fail(f"unexpected path {path}")
        return httpx.Response(200, json=value)

    async def rank_async_response(request):
        await asyncio.sleep(0.002)
        value = [
            {
                "symbol": i.id.symbol,
                "priceChangePercent": str(index),
                "lastPrice": "100.5",
                "quoteVolume": "5000000",
                "closeTime": rank_ms,
                "openTime": rank_ms - 86_400_000,
            }
            for index, i in enumerate(cohort)
        ]
        return httpx.Response(200, json=value)

    rank_res, recovery_res, report_res = await asyncio.gather(
        study.tick(root, enabled=True, clock=lambda: rank_at, transport=httpx.MockTransport(rank_async_response)),
        recovery.tick(root, enabled=True, clock=lambda: recovery_at, transport=httpx.MockTransport(recovery_async_response)),
        asyncio.to_thread(study.finalize_report, root, 24, now=enrollment["endpoint_24h"] + timedelta(seconds=45)),
    )
    assert rank_res["status"] == "observed" and rank_res["slot"] == 289
    assert recovery_res["status"] == "observed"
    cycle = study.load(Path(recovery_res["cycles_path"]))
    assert cycle["discovery"]["admissions"][0]["status"] == "enrolled"
    assert report_res["horizon_hours"] == 24
    assert transports_seen and transports_seen[0].max_attempts == 600
    assert transports_seen[0].retain_payloads is False
    assert transports_seen[0].attempts >= 15
    assert all("payload" not in receipt and "payload_sha256" in receipt for receipt in transports_seen[0].receipts)


async def test_extended_120h_followup_resolves_late_alert_horizons_and_separates_coverage(tmp_path, settings):
    root = tmp_path / "rolling_120h"
    root.mkdir()
    base = Timeline()
    hourly_shifted = [
        replace(c, opened_at=c.opened_at + timedelta(hours=43), closed_at=c.closed_at + timedelta(hours=43))
        for c in base.hourly
    ]
    # Shift hour-4 setup to hour 47 (within the 48h accrual window) and extend through +122h.
    early_prefix = [
        replace(
            hourly_shifted[0],
            opened_at=hourly_shifted[0].opened_at - timedelta(hours=43 - i),
            closed_at=hourly_shifted[0].opened_at - timedelta(hours=42 - i),
            open=Decimal("140"),
            high=Decimal("140.2"),
            low=Decimal("139"),
            close=Decimal("140"),
            volume=Decimal("100"),
            quote_volume=Decimal("14000"),
        )
        for i in range(43)
    ]
    combined_hourly = early_prefix + hourly_shifted
    for _step in range(76):
        start = combined_hourly[-1].closed_at
        combined_hourly.append(
            Candle(
                start,
                start + timedelta(hours=1),
                Decimal("100.35"),
                Decimal("100.45"),
                Decimal("100.25"),
                Decimal("100.35"),
                Decimal("100"),
                Decimal("10035"),
                taker_buy_volume=Decimal("55"),
                taker_buy_quote_volume=Decimal("5519"),
            )
        )
    four_shifted = [
        replace(c, opened_at=c.opened_at + timedelta(hours=40), closed_at=c.closed_at + timedelta(hours=40))
        for c in base.four
    ]
    for _step in range(20):
        start = four_shifted[-1].closed_at
        four_shifted.append(
            Candle(
                start,
                start + timedelta(hours=4),
                Decimal("100.35"),
                Decimal("100.45"),
                Decimal("100.25"),
                Decimal("100.35"),
                Decimal("400"),
                Decimal("40140"),
            )
        )

    confirm_bar_open = AT + timedelta(hours=70, minutes=30)
    confirm_bar_close = AT + timedelta(hours=70, minutes=45)

    class ExtendedTimeline(Timeline):
        def __init__(self):
            super().__init__()
            self.hourly = combined_hourly
            self.four = four_shifted
            self.at = AT + timedelta(minutes=1)

        async def quotes(self, instruments):
            price = Decimal("100.65") if self.at >= confirm_bar_close else Decimal("100.5")
            return [
                Quote(
                    i.id,
                    price - Decimal(".01"),
                    price + Decimal(".01"),
                    price,
                    Decimal("5000000"),
                    observed_at=self.at,
                    high_24h=Decimal("101"),
                    low_24h=Decimal("98"),
                    price_change_pct_24h=Decimal("1"),
                    funding_rate=Decimal(".00005"),
                )
                for i in instruments
            ]

        async def candles(self, instrument, interval, limit):
            self.calls.append((instrument.id.symbol, interval, limit))
            if interval == "1w":
                return await super().candles(instrument, interval, limit)
            if interval == "15m":
                history = []
                for c in self.hourly[-160:]:
                    for quarter in range(4):
                        opened = c.opened_at + timedelta(minutes=15 * quarter)
                        if opened == confirm_bar_open:
                            history.append(
                                Candle(
                                    opened,
                                    opened + timedelta(minutes=15),
                                    Decimal("100.45"),
                                    Decimal("100.75"),
                                    Decimal("100.4"),
                                    Decimal("100.65"),
                                    Decimal("200"),
                                    Decimal("20130"),
                                )
                            )
                        else:
                            start = c.open + (c.close - c.open) * Decimal(quarter) / 4
                            close = c.open + (c.close - c.open) * Decimal(quarter + 1) / 4
                            history.append(
                                Candle(
                                    opened,
                                    opened + timedelta(minutes=15),
                                    start,
                                    max(start, close),
                                    min(start, close),
                                    close,
                                    c.volume / 4,
                                    c.quote_volume / 4,
                                )
                            )
            else:
                history = self.hourly if interval == "1h" else self.four
            history = [c for c in history if c.closed_at <= self.at]
            if instrument.id == self.btc.id:
                history = [
                    replace(c, open=Decimal("100"), close=Decimal("100"), high=Decimal("101"), low=Decimal("99"))
                    for c in history
                ]
            return history[-limit:]

    market = ExtendedTimeline()
    macro = replace(await Macro().get_context(), observed_at=AT)
    definition = {
        "schema": "atlas-paired-study-v1",
        "study_id": "rolling-120h-fixture",
        "issued_at": AT,
        "identity": study.identity(),
        "policy": dict(study.POLICY),
        "cohort": market.cohort,
        "arms": {"control": {"engine": (), "radar": (), "decisions": ()}},
        "momentum_reference": (),
        "recovery": {
            "policy": dict(recovery.POLICY),
            "seeds": (),
            "admissions": (),
            "rolling": {
                "policy": dict(ROLLING_POLICY),
                "benchmark": market.btc,
                "macro": macro,
                "configuration": {
                    "excluded_base_assets": settings.excluded_base_assets,
                    "support_resistance_lookback_bars": settings.support_resistance_lookback_bars,
                },
            },
        },
    }
    study.save(root / "definition.json", definition)
    enrollment = study.enroll(root, now=AT)
    assert enrollment["endpoint_120h"] == enrollment["anchor"] + timedelta(hours=120)
    store = recovery.initialize(root, definition)

    # Enroll at hour 47 (within the 48h accrual window).
    market.at = AT + timedelta(hours=47, minutes=1)
    qualify = await rolling_cycle(
        market, market, store, definition, "hour47-qualify",
        clock=lambda: market.at, accrual_until=enrollment["endpoint_48h"],
    )
    assert qualify["admissions"] and qualify["admissions"][0]["status"] == "enrolled"

    # Past the 48h accrual window, scouting must stop while active episodes still refresh.
    market.at = enrollment["endpoint_48h"] + timedelta(minutes=1)
    post_accrual = await rolling_cycle(
        market, market, store, definition, "hour48-followup",
        clock=lambda: market.at, accrual_until=enrollment["endpoint_48h"],
    )
    assert post_accrual["scout_counts"]["selected"] == 0
    assert post_accrual["scout_counts"]["qualified_capacity"] == 0

    # Fast-forward to hour 70.75 (23.75h after enrollment) to confirm within the 24h window.
    market.at = confirm_bar_close + timedelta(seconds=30)
    await rolling_cycle(
        market, market, store, definition, "hour70-confirm",
        clock=lambda: market.at, accrual_until=enrollment["endpoint_48h"],
    )
    episode, = store.episodes()
    assert episode.triggered_at == market.at

    def write_rank_slot(slot, price="105"):
        observed = enrollment["anchor"] + timedelta(minutes=5 * slot)
        rows = (
            {
                "symbol": "RECUSDT",
                "priceChangePercent": "10",
                "lastPrice": price,
                "quoteVolume": "5000000",
                "closeTime": int(observed.timestamp() * 1000),
                "openTime": int((observed - timedelta(hours=24)).timestamp() * 1000),
            },
        )
        study.save(
            root / "snapshots" / f"{slot:06d}.json",
            {
                "study_id": definition["study_id"],
                "definition_sha256": enrollment["definition_sha256"],
                "slot": slot,
                "status": "observed",
                "arrived_at": observed,
                "rows": rows,
                "normalized": study.normalize_ranks(market.cohort, rows, observed),
            },
        )

    cadence, phase = recovery.POLICY["cadence_seconds"], recovery.POLICY["phase_seconds"]
    first_quarter = int(enrollment["anchor"].timestamp()) // cadence
    if first_quarter * cadence + phase < enrollment["anchor"].timestamp():
        first_quarter += 1
    for quarter in range(first_quarter, first_quarter + 480):
        scheduled = datetime.fromtimestamp(quarter * cadence + phase, tz=AT.tzinfo)
        study.save(
            root / "recovery" / "cycles" / f"{quarter}.json",
            {
                "study_id": definition["study_id"],
                "definition_sha256": enrollment["definition_sha256"],
                "scheduled_at": scheduled,
                "requested_at": scheduled,
                "completed_at": scheduled,
                "status": "observed",
                "reason": None,
                "states": (),
            },
        )

    for slot in range(0, 577):
        write_rank_slot(slot, price="100.5" if slot == 0 else "103")
    report_at_48 = study.finalize_report(root, 48, now=enrollment["endpoint_48h"])
    assert report_at_48["expected_slots"] == 577
    assert report_at_48["recorded_slots"] == 577
    assert report_at_48["missing_slots"] == 0
    assert "followup_coverage" not in report_at_48
    assert report_at_48["recovery_outcomes"]["alert_relative_horizons"]["24"]["censored_count"] == 0  # Trigger at 70.75h is post-48h.

    # Trigger happens at AT + 70h45m30s; +24h = 94h45m30s, +48h = 118h45m30s (within 120h).
    for slot in range(577, 1441):
        write_rank_slot(slot, price="108" if slot < 1000 else "112")
    assert study.status(root, now=enrollment["endpoint_120h"])["endpoint_120h_status"] == "observed"
    assert (await study.tick(root, enabled=True, clock=lambda: enrollment["endpoint_120h"] + timedelta(minutes=5)))["status"] == "window_ended"
    assert (await recovery.tick(root, enabled=True, clock=lambda: enrollment["endpoint_120h"] + timedelta(minutes=3)))["status"] == "window_ended"

    report_at_120 = study.finalize_report(root, 120, now=enrollment["endpoint_120h"])
    assert report_at_120["expected_slots"] == 1441
    assert report_at_120["recorded_slots"] == 1441
    assert report_at_120["missing_slots"] == 0
    assert "followup_coverage" not in report_at_120
    horizons_120 = report_at_120["recovery_outcomes"]["alert_relative_horizons"]
    assert horizons_120["24"]["known_count"] == 1 and horizons_120["24"]["censored_count"] == 0
    assert horizons_120["48"]["known_count"] == 1 and horizons_120["48"]["censored_count"] == 0
    row_120 = report_at_120["recovery_outcomes"]["outcomes"][0]
    assert row_120["study_endpoint_return_from_confirmation_pct"] == (Decimal("112") / Decimal("100.65") - 1) * 100
    assert row_120["followup_minutes_to_study_endpoint"] == 2959.5
    assert "followup_minutes_to_followup_endpoint" not in row_120
    assert report_at_120["recovery_outcomes"]["endpoint_known_count"] == 1

    resolved_48 = study.finalize_report(root, 48, now=enrollment["endpoint_120h"])
    assert resolved_48["as_of"] == enrollment["endpoint_120h"]
    assert study.load(root / "report-48h.json")["as_of"] == enrollment["endpoint_120h"]
    assert resolved_48["expected_slots"] == 577
    assert resolved_48["recorded_slots"] == 577
    assert resolved_48["missing_slots"] == 0
    assert resolved_48["observed_slots"] == 577
    assert resolved_48["unobserved_slots"] == 0
    assert resolved_48["collection_status"] == "complete"
    assert resolved_48["followup_coverage"] == {
        "endpoint": enrollment["endpoint_120h"],
        "expected_slots": 864,
        "recorded_slots": 864,
        "missing_slots": 0,
        "observed_slots": 864,
        "missed_slots": 0,
        "failed_slots": 0,
        "absent_slots": 0,
        "unobserved_slots": 0,
    }
    assert resolved_48["recovery_outcomes"]["collection"]["expected_cycles"] == 192
    assert resolved_48["recovery_outcomes"]["followup_collection"]["expected_cycles"] == 288
    row_48 = resolved_48["recovery_outcomes"]["outcomes"][0]
    assert row_48["study_endpoint_return_from_confirmation_pct"] is None
    assert row_48["followup_minutes_to_study_endpoint"] is None
    assert row_48["followup_minutes_to_followup_endpoint"] == 2959.5
    assert resolved_48["recovery_outcomes"]["confirmed_count"] == 1
    assert resolved_48["recovery_outcomes"]["endpoint_known_count"] == 0
    assert resolved_48["recovery_outcomes"]["nonpositive_endpoint_count"] == 0
    assert all(
        group["nonpositive_endpoint_count"] == 0
        for group in resolved_48["recovery_outcomes"]["positioning_groups"].values()
    )
    horizons_48 = resolved_48["recovery_outcomes"]["alert_relative_horizons"]
    assert horizons_48["24"]["known_count"] == 1 and horizons_48["24"]["censored_count"] == 0
    assert horizons_48["48"]["known_count"] == 1 and horizons_48["48"]["censored_count"] == 0

    # A missing follow-up snapshot degrades follow-up coverage without altering main-window counts.
    (root / "snapshots" / "001400.json").unlink()
    degraded_48 = study.report(root, 48, now=enrollment["endpoint_120h"])
    assert degraded_48["expected_slots"] == 577
    assert degraded_48["recorded_slots"] == 577
    assert degraded_48["missing_slots"] == 0
    assert degraded_48["followup_coverage"]["expected_slots"] == 864
    assert degraded_48["followup_coverage"]["recorded_slots"] == 863
    assert degraded_48["followup_coverage"]["missing_slots"] == 1
    assert degraded_48["followup_coverage"]["absent_slots"] == 1
    assert degraded_48["collection_status"] == "degraded"
    assert degraded_48["recovery_outcomes"]["outcomes"][0]["study_endpoint_return_from_confirmation_pct"] is None
    write_rank_slot(1400, price="112")
    (root / "report-48h.json").unlink()
    (root / "report-120h.json").unlink()


async def test_rolling_cycle_queue_fairness_backoff_and_unavailable_data_rotation(rolling_study, tmp_path):
    import copy
    _, base, _, market, _ = rolling_study
    cohort = tuple(
        replace(market.instrument, id=replace(market.instrument.id, symbol=f"Q{i}USDT"), base_asset=f"Q{i}")
        for i in range(7)
    )
    definition = copy.deepcopy(base)
    definition["study_id"] = "queue-fairness-rotation"
    definition["cohort"] = cohort
    fresh_root = tmp_path / "queue_fairness"
    fresh_root.mkdir()
    study.save(fresh_root / "definition.json", definition)
    study.enroll(fresh_root, now=AT)
    episodes = recovery.initialize(fresh_root, definition)
    market.cohort = cohort
    market.at = AT + timedelta(hours=4, minutes=1)

    orig_deriv = market.derivatives_snapshot

    async def cycle1_deriv(inst):
        if inst.id.symbol == "Q0USDT":
            return None  # Data unavailable on cycle 1 for Q0USDT
        d = await orig_deriv(inst)
        return replace(
            d,
            open_interest_change_7d=Decimal("-0.12"),
            open_interest_change_24h=Decimal("-0.05"),
            open_interest_change_4h=Decimal("-0.03"),
            open_interest_change_1h=Decimal("-0.02"),
            taker_buy_sell_ratio=Decimal("0.80"),
            taker_buy_sell_ratio_4h=Decimal("0.80"),
        )

    market.derivatives_snapshot = cycle1_deriv
    c1 = await rolling_cycle(market, market, episodes, definition, "cycle-1", clock=lambda: market.at)
    assert c1["pool"] == ("Q0USDT", "Q1USDT", "Q2USDT", "Q3USDT")
    assert c1["control_symbols"] == () and c1["challenger_symbols"] == ()
    assert c1["unavailable_symbols"] == ("Q0USDT",)
    assert set(c1["evaluated_features"]) == {"Q0USDT", "Q1USDT", "Q2USDT", "Q3USDT"}

    # Cycle 2 (+15m): Q1, Q2, Q3 were rejected with unchanged features and must back off,
    # while Q0 had unavailable data (not a rejection) and Q4, Q5, Q6 are fresh never-evaluated candidates.
    market.at = AT + timedelta(hours=4, minutes=16)
    c2 = await rolling_cycle(market, market, episodes, definition, "cycle-2", clock=lambda: market.at)
    assert set(c2["pool"]) == {"Q0USDT", "Q4USDT", "Q5USDT", "Q6USDT"}
    assert set(c2["pool"]).isdisjoint({"Q1USDT", "Q2USDT", "Q3USDT"})

