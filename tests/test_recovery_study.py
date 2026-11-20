from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from algotrader.domain.models import MarketKind, Venue
from algotrader.domain.recovery_episodes import RecoveryEpisode
from algotrader.infrastructure.recovery_episodes import episode_values
from scripts import research_prospective as study
from scripts import research_recovery_study as recovery
from scripts.research_capture_evidence import execute
from scripts.research_evidence_contract import verify_bundle
from scripts.research_evidence_stream import write_bundle
from tests.conftest import make_instrument
from tests.test_capture_evidence import Macro, public_response
from tests.test_recovery_episodes import bars_for, quote_for
from tests.test_recovery_episodes import candidate as candidate
from tests.test_recovery_screener import AT, derivative


@pytest.fixture
async def prepared_recovery(candidate, tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    candidate = replace(candidate, instrument=replace(candidate.instrument,
                        id=replace(candidate.instrument.id, venue=Venue.BINANCE_USDM),
                        market_kind=MarketKind.FUTURES))
    episode = RecoveryEpisode.enroll(candidate, AT)
    record = {"state": episode_values(episode), "evidence": {"kind": "enrollment", "candidate": candidate.as_dict()}}
    definition = {"schema": "atlas-paired-study-v1", "study_id": "recovery-fixture", "identity": study.identity(),
                  "policy": dict(study.POLICY), "issued_at": AT,
                  "cohort": (candidate.instrument, *(make_instrument(symbol=f"COIN{i}USDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES) for i in range(11))),
                  "arms": {"control": {"engine": (), "radar": (), "decisions": ()}},
                  "momentum_reference": ("COIN0USDT",),
                  "recovery": {"policy": dict(recovery.POLICY), "seeds": (record,),
                               "admissions": ({"symbol": "RECUSDT", "status": "enrolled"},)}}
    study.save(root / "definition.json", definition)
    enrollment = study.enroll(root, now=AT)
    return root, definition, enrollment, episode


def rank_rows(definition, at, winner=False):
    return [{"symbol": inst.id.symbol, "priceChangePercent": str(50 if i == 0 and winner else i),
             "lastPrice": "105" if i == 0 and winner else "100", "quoteVolume": "5000000",
             "closeTime": int(at.timestamp() * 1000),
             "openTime": int((at - timedelta(hours=24)).timestamp() * 1000)}
            for i, inst in enumerate(definition["cohort"])]


async def anchor(prepared):
    root, definition, enrollment, _ = prepared
    at = enrollment["anchor"]
    return await study.tick(root, enabled=True, clock=lambda: at,
                            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rank_rows(definition, at))))


class Market:
    def __init__(self, episode, at):
        self.episode, self.at = episode, at
        self.calls = []
        self.fail = False

    async def candles(self, instrument, interval, limit):
        self.calls.append(("candles", interval, limit))
        if self.fail:
            raise TimeoutError()
        count = int((self.at - AT).total_seconds() // 900)
        return bars_for(self.episode, count)[-limit:]

    async def quotes(self, instruments):
        return [quote_for(self.episode, self.at)]

    async def derivatives_snapshot(self, instrument):
        return replace(derivative(), observed_at=self.at)


async def test_opt_in_capture_prepare_normal_entry_points(tmp_path):
    args = SimpleNamespace(max_attempts=200, deadline_seconds=60, max_symbols=320,
                           max_supplemental=120, discovery_v2=True, recovery=True)
    bundle = await execute(args, transport=httpx.MockTransport(public_response), macro_provider=Macro())
    assert bundle["status"] == "captured" and bundle["recovery_policy"] == recovery.POLICY
    path = tmp_path / "capture.jsonl"
    write_bundle(path, bundle)
    root = tmp_path / "normal-study"
    await study.prepare(path, root)
    definition = study.definition(root)
    assert definition["recovery"]["policy"] == recovery.POLICY
    assert "recovery" in definition["arms"]
    assert not (root / "enrollment.json").exists()
    bundle["recovery_policy"] = {**recovery.POLICY, "capacity": 1000}
    with pytest.raises(ValueError, match="invalid_recovery_capture_policy"):
        await verify_bundle(bundle)


async def test_no_network_before_opt_in_and_valid_anchor(prepared_recovery):
    root, _, enrollment, episode = prepared_recovery
    market = Market(episode, AT + timedelta(minutes=16))
    assert (await recovery.tick(root, enabled=False))["status"] == "disabled"
    assert (await recovery.tick(root, enabled=True, clock=lambda: enrollment["anchor"],
                                market=market, derivatives=market))["status"] == "awaiting_valid_anchor"
    assert not market.calls and not (root / "recovery" / "episodes.sqlite").exists()


async def test_enroll_confirm_restart_and_24h_48h_reports(prepared_recovery):
    root, definition, enrollment, episode = prepared_recovery
    assert (await anchor(prepared_recovery))["observation_started"]
    at = AT + timedelta(minutes=16)
    market = Market(episode, at)
    result = await recovery.tick(root, enabled=True, clock=lambda: at, market=market, derivatives=market)
    assert result["status"] == "observed"
    store = recovery.initialize(root, definition)
    assert store.episodes()[0].triggered_at == at
    assert (await recovery.tick(root, enabled=True, clock=lambda: at, market=market, derivatives=market))["status"] == "already_recorded"
    assert study.health(root, now=at)["recovery"]["status"] == "collecting"
    # Continue through a simulated restart and observe the full remaining candle path.
    for elapsed in (timedelta(hours=24, minutes=16), timedelta(hours=48, minutes=1)):
        market.at = AT + elapsed
        await recovery.tick(root, enabled=True, clock=lambda: market.at, market=market, derivatives=market)
    assert recovery.initialize(root, definition).episodes()[0].status == "expired"
    # Complete immutable independent rank samples, with membership changing after confirmation.
    for slot in range(1, 577):
        observed = enrollment["anchor"] + timedelta(minutes=5 * slot)
        rows = tuple(rank_rows(definition, observed, winner=slot >= 3))
        study.save(root / "snapshots" / f"{slot:06d}.json", {
            "study_id": definition["study_id"], "definition_sha256": enrollment["definition_sha256"],
            "slot": slot, "status": "observed", "arrived_at": observed, "rows": rows,
            "normalized": study.normalize_ranks(definition["cohort"], rows, observed)})
    first = study.finalize_report(root, 24, now=enrollment["endpoint_48h"])
    second = study.finalize_report(root, 48, now=enrollment["endpoint_48h"])
    assert first["recovery_outcomes"]["confirmed_count"] == second["recovery_outcomes"]["confirmed_count"] == 1
    row = first["recovery_outcomes"]["outcomes"][0]
    assert row["observed_early_detection"] is True and row["observed_lead_minutes"] == 4
    assert row["status"] == "triggered"  # Later expiry cannot leak into the 24h report.
    assert second["recovery_outcomes"]["outcomes"][0]["status"] == "expired"
    assert first["recovery_outcomes"]["endpoint_known_count"] == 1
    assert first["recovery_outcomes"]["collection"]["absent"] > 0
    assert first["performance_claims_permitted"] is False


async def test_wrong_study_binding_and_seed_cannot_mix(prepared_recovery):
    root, definition, _, episode = prepared_recovery
    recovery.initialize(root, definition)
    other = {**definition, "study_id": "other-study"}
    with pytest.raises(ValueError, match="another study"):
        recovery.initialize(root, other)
    with pytest.raises(ValueError, match="another study"):
        recovery.snapshot(root, other, AT + timedelta(hours=24))
    altered = {**definition, "recovery": {**definition["recovery"], "policy": {"version": "bogus"}}}
    with pytest.raises(ValueError, match="policy mismatch"):
        recovery.validate_arm(altered)


async def test_failed_candle_capture_has_separate_degraded_health(prepared_recovery):
    root, definition, enrollment, episode = prepared_recovery
    await anchor(prepared_recovery)
    at = AT + timedelta(minutes=16)
    market = Market(episode, at)
    market.fail = True
    await recovery.tick(root, enabled=True, clock=lambda: at, market=market, derivatives=market)
    assert study.health(root, now=at)["recovery"]["status"] == "degraded"
    assert study.load(root / "snapshots" / "000000.json")["status"] == "observed"
    assert recovery.initialize(root, definition).episodes()[0].triggered_at is None


async def test_exchange_cooldown_shared_with_rank_sampler(prepared_recovery):
    root, _, _, _ = prepared_recovery
    await anchor(prepared_recovery)
    at = AT + timedelta(minutes=16)
    requests = []
    def limited(request):
        requests.append(request)
        return httpx.Response(418, headers={"Retry-After": "3600"}, json={"code": -1003})
    result = await recovery.tick(root, enabled=True, clock=lambda: at, transport=httpx.MockTransport(limited))
    assert result["status"] == "failed" and len(requests) == 1
    market_time = AT + timedelta(minutes=31)
    assert (await recovery.tick(root, enabled=True, clock=lambda: market_time,
                                transport=httpx.MockTransport(limited)))["status"] == "exchange_cooldown"
    assert (await study.tick(root, enabled=True, clock=lambda: market_time,
                             transport=httpx.MockTransport(limited)))["status"] == "exchange_cooldown"
    assert len(requests) == 1


async def test_slow_recovery_io_does_not_hold_rank_sampling_lock(prepared_recovery):
    import asyncio
    root, definition, enrollment, episode = prepared_recovery
    await anchor(prepared_recovery)
    at = AT + timedelta(minutes=16)
    market = Market(episode, at)
    entered, release = asyncio.Event(), asyncio.Event()
    normal = market.candles

    async def slow(instrument, interval, limit):
        entered.set()
        await release.wait()
        return await normal(instrument, interval, limit)

    market.candles = slow
    job = asyncio.create_task(recovery.tick(root, enabled=True, clock=lambda: at,
                                            market=market, derivatives=market))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        rank_at = enrollment["anchor"] + timedelta(minutes=15)
        result = await asyncio.wait_for(study.tick(root, enabled=True, clock=lambda: rank_at,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rank_rows(definition, rank_at)))), 1)
        assert result["status"] == "observed"
    finally:
        release.set()
        await job


async def test_normal_worker_launches_and_cancels_recovery_task(prepared_recovery, monkeypatch):
    import asyncio
    root, _, _, _ = prepared_recovery
    original_sleep = asyncio.sleep
    started, cancelled = asyncio.Event(), asyncio.Event()
    samples = []

    async def recovery_job(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def rank_tick(*args, **kwargs):
        samples.append(1)
        await original_sleep(0)
        return {"status": "window_ended" if len(samples) == 3 else "observed"}

    async def fast_sleep(_):
        await original_sleep(0)

    monkeypatch.setattr(recovery, "run", recovery_job)
    monkeypatch.setattr(study, "tick", rank_tick)
    monkeypatch.setattr(study.asyncio, "sleep", fast_sleep)
    monkeypatch.setenv("ATLAS_RESEARCH_ENABLED", "true")
    result = await study.main_async(SimpleNamespace(action="run", study=root, execute_public_collection=True))
    assert result["status"] == "window_ended" and len(samples) == 3
    assert started.is_set() and cancelled.is_set()


@pytest.mark.parametrize("case", ["unknown", "stale", "already_inside", "late", "early"])
async def test_rank_matching_keeps_late_and_uncertain_confirmations_explicit(prepared_recovery, case):
    root, definition, enrollment, episode = prepared_recovery
    await anchor(prepared_recovery)
    at = AT + timedelta(minutes=16)
    market = Market(episode, at)
    await recovery.tick(root, enabled=True, clock=lambda: at, market=market, derivatives=market)
    context = recovery.snapshot(root, definition, enrollment["endpoint_24h"])
    first = AT + timedelta(minutes=20 if case != "late" else 10)
    states = {"RECUSDT": {"first_observed_top10_at": first, "initial_inside": case == "already_inside",
                          "endpoint_price": episode.entry - 1}}
    matches = {episode.episode_id: {"at": at - timedelta(minutes=10 if case == "stale" else 1),
                                  "inside": None if case == "unknown" else False}}
    result = recovery.outcome_report(root, context, definition, enrollment, enrollment["endpoint_24h"], states, matches)
    row = result["outcomes"][0]
    assert row["observed_early_detection"] == (True if case == "early" else False if case in ("late", "already_inside") else None)
    assert result["nonpositive_endpoint_count"] == 1
    assert result["endpoint_known_count"] == 1
    assert result["performance_claims_permitted"] is False


async def test_recovery_worker_retries_transient_database_failure(prepared_recovery, monkeypatch):
    import sqlite3
    root, definition, enrollment, _ = prepared_recovery
    calls = []
    sleeps = []

    async def transient(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"status": "window_ended"}

    async def pause(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(recovery, "tick", transient)
    result = await recovery.run(root, validated_session=(definition, enrollment), clock=lambda: AT, sleep=pause)
    assert result["status"] == "window_ended" and len(calls) == 2 and sleeps == [5]
    assert study.load(root / "recovery" / "progress.json")["latest"]["reason"] == "OperationalError"
