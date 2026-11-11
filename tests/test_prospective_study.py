from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from algotrader.domain.models import MarketKind, Venue
from scripts import research_prospective as study
from scripts.replay_contract import ReplayUnavailable
from scripts.research_evidence_contract import verify_bundle
from scripts.research_evidence_stream import write_bundle
from tests.conftest import make_instrument
from tests.test_capture_evidence import bundle_fixture


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    at = datetime(2026, 11, 8, 12, tzinfo=UTC)
    cohort = tuple(
        make_instrument(
            symbol=f"COIN{i}USDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES
        )
        for i in range(12)
    )
    definition = {
        "schema": "atlas-paired-study-v1",
        "study_id": "fixture-study",
        "identity": study.identity(),
        "policy": dict(study.POLICY),
        "issued_at": at,
        "cohort": cohort,
        "arms": {
            "control": {"engine": ("COIN11USDT",), "radar": (), "decisions": ()},
            "funding_carry": {"engine": ("COIN0USDT",), "radar": ("COIN0USDT",), "decisions": ()},
        },
        "momentum_reference": ("COIN1USDT",),
    }
    study.save(root / "definition.json", definition)
    return root, definition, at


def rows(at, *, winner=0, missing=()):
    return [
        {
            "symbol": f"COIN{i}USDT",
            "priceChangePercent": str(50 if i == winner else 12 - i),
            "lastPrice": str(100 + i),
            "quoteVolume": "10000000",
            "closeTime": int(at.timestamp() * 1000),
            "openTime": int((at - timedelta(hours=24)).timestamp() * 1000),
        }
        for i in range(12)
        if i not in missing
    ]


async def snapshot(root, at, *, winner=0, missing=()):
    return await study.tick(
        root,
        enabled=True,
        clock=lambda: at,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=rows(at, winner=winner, missing=missing))
        ),
    )


async def test_prepare_runs_current_control_and_challenger_without_enrollment(tmp_path):
    bundle = await bundle_fixture()
    path = tmp_path / "capture.jsonl"
    write_bundle(path, bundle)
    root = tmp_path / "prospective"
    result = await study.prepare(path, root)
    assert result["status"] == "prepared_not_enrolled"
    assert not (root / "enrollment.json").exists()
    assert study.definition(root)["bundle_sha256"]


async def test_v3_checks_freshness_at_acquisition_v2_still_rejects_end_staleness():
    bundle = await bundle_fixture()
    end = bundle["completed_at"] + timedelta(seconds=225)
    bundle["record"] = replace(bundle["record"], completed_at=end)
    bundle["completed_at"] = end
    await verify_bundle(bundle)
    bundle["schema"] = "atlas-public-evidence-v2"
    with pytest.raises(ReplayUnavailable, match="future_or_stale_input"):
        await verify_bundle(bundle)


async def test_v3_still_bounds_whole_capture_window():
    bundle = await bundle_fixture()
    bundle["completed_at"] += timedelta(seconds=601)
    with pytest.raises(ReplayUnavailable, match="capture_window_exceeds"):
        await verify_bundle(bundle)


def test_enrollment_has_new_future_fixed_anchor_and_is_idempotent(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    assert e["anchor"] == at + timedelta(minutes=5)
    assert e["endpoint_48h"] == e["anchor"] + timedelta(hours=48)
    assert study.enroll(root, now=at + timedelta(seconds=1)) == e


def test_enrollment_refuses_old_decisions_and_overwrite(prepared):
    root, _, at = prepared
    with pytest.raises(ValueError, match="outside enrollment"):
        study.enroll(root, now=at + timedelta(seconds=601))
    with pytest.raises(FileExistsError):
        study.save(root / "definition.json", {})


async def test_disabled_tick_has_no_network_or_enrollment_requirement(tmp_path):
    def denied(_):
        pytest.fail("disabled worker used network")

    assert await study.tick(tmp_path, enabled=False, transport=httpx.MockTransport(denied)) == {
        "status": "disabled",
        "http_attempts": 0,
    }


async def test_first_snapshot_records_all_cohort_members_including_non_candidates(prepared):
    root, d, at = prepared
    e = study.enroll(root, now=at)
    result = await snapshot(root, e["anchor"])
    assert result["observation_started"] is True
    stored = study.load(root / "snapshots/000000.json")
    assert len(stored["normalized"]["members"]) == len(d["cohort"])
    assert result["http_attempts"] == 1
    assert (await snapshot(root, e["anchor"]))["status"] == "already_recorded"


async def test_late_anchor_never_moves_or_backfills(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    result = await snapshot(root, e["anchor"] + timedelta(seconds=31))
    assert result["status"] == "missed" and result["http_attempts"] == 0
    assert (await snapshot(root, e["anchor"] + timedelta(minutes=5)))[
        "status"
    ] == "anchor_failed_study_invalid"
    assert study.load(root / "enrollment.json")["anchor"] == e["anchor"]


async def test_missing_anchor_does_not_start_at_a_later_slot(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    assert (await snapshot(root, e["anchor"] + timedelta(minutes=10)))[
        "status"
    ] == "anchor_missing_study_invalid"


async def test_missing_symbol_is_preserved_unknown_not_removed(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"], missing=(11,))
    stored = study.load(root / "snapshots/000000.json")
    member = next(m for m in stored["normalized"]["members"] if m["symbol"] == "COIN11USDT")
    assert member["price"] is None and member["inside"] is None
    assert len(stored["normalized"]["members"]) == 12


async def test_gap_report_keeps_unknowns_and_reports_missed_gainers(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"], winner=1)
    await snapshot(root, e["endpoint_24h"], winner=11)
    result = study.report(root, 24, now=e["endpoint_24h"] + timedelta(seconds=45))
    assert result["missing_slots"] == 287
    assert result["arms"]["funding_carry_radar"]["missed_observed_gainers"]
    member = next(r for r in result["symbols"] if r["symbol"] == "COIN11USDT")
    assert member["unknown_samples"] == 287
    assert result["performance_claims_permitted"] is False
    assert member["endpoint_return_pct"] == 0


def test_source_changes_and_corrupt_envelope_fail_closed(prepared, monkeypatch):
    root, _, _ = prepared
    monkeypatch.setattr(study, "identity", lambda: {})
    with pytest.raises(ValueError, match="mismatch"):
        study.definition(root)


async def test_throttle_performs_only_one_wire_attempt(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    attempts = []

    def throttle(request):
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"}, json={"code": -1003})

    result = await study.tick(
        root, enabled=True, clock=lambda: e["anchor"], transport=httpx.MockTransport(throttle)
    )
    assert result["status"] == "failed" and len(attempts) == 1


def test_rank_bounds_certify_only_when_missing_competitors_cannot_change_membership(prepared):
    root, d, at = prepared
    normalized = study.normalize_ranks(d["cohort"], tuple(rows(at, missing=(0,))), at)
    members = {r["symbol"]: r for r in normalized["members"]}
    assert normalized["bounded"] and not normalized["complete"]
    assert members["COIN11USDT"]["inside"] is False  # rank 11..12
    assert members["COIN9USDT"]["inside"] is True  # rank 9..10
    assert members["COIN10USDT"]["inside"] is None  # rank 10..11
    assert members["COIN0USDT"]["price"] is None


def test_rank_ties_have_competition_ranks_and_invalid_rows_stay_unknown(prepared):
    _, d, at = prepared
    raw = rows(at)
    for r in raw:
        r["priceChangePercent"] = "1"
    raw[1]["lastPrice"] = "NaN"
    raw[2]["closeTime"] = int((at - timedelta(seconds=61)).timestamp() * 1000)
    normalized = study.normalize_ranks(d["cohort"], tuple(raw), at)
    assert normalized["members"][0]["rank_min"] == 1
    assert normalized["members"][0]["rank_max"] == 3
    assert normalized["members"][1]["inside"] is None
    assert normalized["members"][2]["inside"] is None


async def test_cooldown_survives_worker_restart_without_a_retry(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    first = e["anchor"] + timedelta(minutes=5)
    await study.tick(
        root,
        enabled=True,
        clock=lambda: first,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "900"}, json={})
        ),
    )

    def denied(_):
        pytest.fail("cooldown did not survive restart")

    result = await study.tick(
        root,
        enabled=True,
        clock=lambda: first + timedelta(minutes=5),
        transport=httpx.MockTransport(denied),
    )
    assert result["status"] == "exchange_cooldown" and result["http_attempts"] == 0


def test_concurrent_worker_lock_and_corrupted_data_are_rejected(prepared):
    root, _, at = prepared
    with study.locked(root), pytest.raises(BlockingIOError), study.locked(root):
        pass
    p = root / "definition.json"
    p.write_text(p.read_text().replace("fixture-study", "changed-study"))
    with pytest.raises(ValueError, match="checksum"):
        study.definition(root)


async def test_status_does_not_call_failed_endpoint_success(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    await snapshot(root, e["endpoint_24h"] + timedelta(seconds=31))
    result = study.status(root, now=e["endpoint_24h"] + timedelta(seconds=32))
    assert result["endpoint_24h_status"] == "missed"
    assert result["status"] == "degraded"


async def test_sampler_never_computes_reports(prepared, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    root, _, at = prepared
    study.enroll(root, now=at)
    monkeypatch.setenv("ATLAS_RESEARCH_ENABLED", "true")
    monkeypatch.setattr(
        study,
        "tick",
        AsyncMock(
            side_effect=[
                {"status": "already_recorded"},
                {"status": "already_recorded"},
                {"status": "window_ended"},
            ]
        ),
    )
    monkeypatch.setattr(study.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(study, "report", lambda *a, **k: pytest.fail("sampler computed report"))
    args = SimpleNamespace(action="run", study=root, execute_public_collection=True)
    assert (await study.main_async(args))["status"] == "window_ended"


async def test_report_worker_finalizes_each_horizon_once(prepared, monkeypatch):
    from types import SimpleNamespace

    root, _, at = prepared
    study.enroll(root, now=at)
    monkeypatch.setenv("ATLAS_RESEARCH_ENABLED", "true")
    calls = []
    monkeypatch.setattr(study, "finalize_report", lambda root, horizon, **k: calls.append(horizon))
    args = SimpleNamespace(action="reports", study=root, execute_public_collection=True)
    assert (await study.main_async(args))["status"] == "reports_complete"
    assert calls == [24, 48]


async def test_slow_reporting_cannot_hold_sampler_lock(prepared, monkeypatch):
    import asyncio
    import threading

    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    entered, release = threading.Event(), threading.Event()

    def slow_report(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"definition_sha256": e["definition_sha256"], "horizon_hours": 24}

    monkeypatch.setattr(study, "report", slow_report)
    job = asyncio.create_task(asyncio.to_thread(study.finalize_report, root, 24, now=at))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await snapshot(root, e["anchor"] + timedelta(minutes=5)))["status"] == "observed"
    finally:
        release.set()
        await job
    assert study.finalize_report(root, 24, now=at)["horizon_hours"] == 24


async def test_health_uses_only_small_progress_and_enrollment(prepared, monkeypatch):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    original = study.load
    reads = []

    def bounded(path):
        reads.append(path.name)
        assert path.name in ("progress.json", "enrollment.json")
        return original(path)

    monkeypatch.setattr(study, "load", bounded)
    assert study.health(root, now=e["anchor"] + timedelta(seconds=1))["status"] == "collecting"
    assert reads == ["progress.json", "enrollment.json"]


async def test_report_separates_missed_failed_and_absent_records(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    await snapshot(root, e["anchor"] + timedelta(minutes=5, seconds=31))
    await study.tick(root, enabled=True, clock=lambda: e["anchor"] + timedelta(minutes=10),
                     transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    result = study.report(root, 24, now=e["endpoint_24h"])
    assert result["collection_status"] == "degraded"
    assert result["observed_slots"] == 1
    assert result["missed_slots"] == result["failed_slots"] == 1
    assert result["absent_slots"] == result["missing_slots"] == 286
    assert result["unobserved_slots"] == 288


async def test_progress_recovers_after_snapshot_publication_crash(prepared):
    root, _, at = prepared
    e = study.enroll(root, now=at)
    await snapshot(root, e["anchor"])
    (root / "progress.json").unlink()
    assert (await snapshot(root, e["anchor"]))["status"] == "already_recorded"
    assert study.health(root, now=e["anchor"])["status"] == "collecting"


async def test_storage_failure_prevents_http(prepared, monkeypatch):
    root, _, at = prepared
    e = study.enroll(root, now=at)

    def blocked(_):
        raise ValueError("storage blocked")

    def denied(_):
        pytest.fail("storage guard allowed HTTP")

    monkeypatch.setattr(study, "storage_ok", blocked)
    with pytest.raises(ValueError, match="storage blocked"):
        await study.tick(
            root, enabled=True, clock=lambda: e["anchor"], transport=httpx.MockTransport(denied)
        )
