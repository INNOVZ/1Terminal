from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.domain.models import MarketKind, Quote, Venue
from scripts import research_prospective as study
from scripts.replay_contract import ReplayUnavailable
from scripts.research_capture_evidence import execute
from scripts.research_discovery import (
    DiscoveryScreener,
    temporal_features,
    validate_history_overlap,
)
from scripts.research_evidence_contract import verify_bundle
from scripts.research_evidence_stream import read_bundle, write_bundle
from scripts.research_pipeline_comparison import compare_bundle
from tests.conftest import make_instrument, trending_candles
from tests.test_capture_evidence import Macro, public_response


def engine(settings, at=None):
    return DiscoveryScreener(settings, evaluated_at=at or datetime(2026, 11, 11, 4, tzinfo=UTC))


def test_watch_pool_preserves_selection_budget_and_liquidity_requirements(settings):
    instruments = [make_instrument(symbol=f"C{i}USDT", venue=Venue.BINANCE_USDM,
                                   market_kind=MarketKind.FUTURES) for i in range(50)]
    quotes = {i.id.symbol: Quote(i.id, Decimal("99.95"), Decimal("100.05"), Decimal("100"),
                                Decimal("900000")) for i in instruments}
    quotes[instruments[-1].id.symbol] = replace(quotes[instruments[-1].id.symbol],
                                                quote_volume_24h=Decimal("499999"))
    quotes[instruments[-2].id.symbol] = replace(quotes[instruments[-2].id.symbol],
                                                ask=Decimal("101"))
    selected = instruments[:10]
    challenger = engine(settings)
    result = challenger._extend_selection(selected, instruments, quotes, 40)
    assert result[:10] == selected and len(result) == 30
    assert len(challenger.watch_symbols) == 20
    assert len({i.id for i in result}) == len(result)
    assert instruments[-1] not in result and instruments[-2] not in result
    assert challenger._extend_selection(selected, instruments, quotes, 12)[:10] == selected
    assert len(challenger.watch_symbols) == 2
    assert CryptoOpportunityScreener(settings)._extend_selection(selected, instruments, quotes, 40) == selected


def test_watch_order_is_reproducible_and_rotates_with_frozen_four_hour_bucket(settings):
    instruments = [make_instrument(symbol=f"C{i}USDT") for i in range(50)]
    quotes = {i.id.symbol: Quote(i.id, Decimal("1"), Decimal("1"), Decimal("1"),
                                Decimal("900000")) for i in instruments}
    first = engine(settings)
    second = engine(settings, first._evaluated_at + timedelta(hours=4))
    result = first._extend_selection([], instruments, quotes, 40)
    assert result == first._extend_selection([], instruments, quotes, 40)
    assert result != second._extend_selection([], instruments, quotes, 40)


def test_feature_contract_uses_full_history_and_actual_four_hour_volume(settings):
    hourly = trending_candles(count=360)
    four = [replace(c, opened_at=c.opened_at + timedelta(hours=3 * index),
                    closed_at=c.closed_at + timedelta(hours=3 * index + 3))
            for index, c in enumerate(trending_candles(count=40))]
    four[-1] = replace(four[-1], volume=Decimal("2000"))
    snapshot = SimpleNamespace(relative_volume=Decimal("7"))
    control = CryptoOpportunityScreener(settings)
    assert control._hourly_history_limit() == 240
    assert control._accumulation_volume_metrics(hourly, four, snapshot)[3] == 7
    challenger = engine(settings)
    assert challenger._hourly_history_limit() == 360
    assert challenger._accumulation_volume_metrics(hourly, four, snapshot)[3] == Decimal("2000") / Decimal("1050")
    with pytest.raises(ValueError, match="336-hour"):
        challenger._accumulation_volume_metrics(hourly[-239:], four, snapshot)
    with pytest.raises(ValueError, match="336-hour"):
        challenger._accumulation_volume_metrics([*hourly[:-10], *hourly[-9:]], four, snapshot)


def test_readiness_keeps_activation_and_rejections_explicit(settings):
    challenger = engine(settings)
    gates = {f"L{i}": True for i in range(1, 7)}
    gates["L6"] = False
    challenger._note("COINUSDT", "accumulation_gates", "failed", gates=gates, enriched=True)
    observation = challenger.observations()[0]
    assert observation["state"] == "ready_waiting_activation"
    assert observation["actionable"] is False
    challenger._note("COINUSDT", "accumulation_rejection", "crowded_raw_funding", observed=Decimal(".001"))
    assert challenger.observations()[0]["state"] == "structural_rejection"


def test_temporal_features_require_aligned_complete_closed_windows():
    hourly = trending_candles(count=24)
    btc = trending_candles(count=24)
    result = temporal_features(hourly, btc)
    assert result["available"] and Decimal(result["btc_relative_6h_pp"]) == 0
    assert result["emerging"] is False
    assert temporal_features(hourly, btc[:-1])["available"] is False
    assert temporal_features([*hourly[:-10], *hourly[-9:]], btc)["available"] is False


def test_extended_history_cannot_add_post_control_closed_bars():
    from algotrader.domain.scan_observations import RecordedCall

    instrument = make_instrument()
    candles = trending_candles(count=360)
    arguments = {"instrument": instrument, "interval": "1h", "limit": 240}
    original = RecordedCall("candles", 0, arguments, candles[-240:])
    extended = RecordedCall("candles", 0, {**arguments, "limit": 360}, candles)
    record = SimpleNamespace(inputs=(original,), started_at=candles[-1].closed_at)
    validate_history_overlap(record, (extended,))
    late = replace(extended, result=[*candles, replace(candles[-1], closed_at=record.started_at + timedelta(hours=1))])
    with pytest.raises(ReplayUnavailable, match="after_control_cutoff"):
        validate_history_overlap(record, (late,))


async def discovery_bundle():
    args = SimpleNamespace(max_attempts=200, deadline_seconds=60, max_symbols=320,
                           max_supplemental=60, discovery_v2=True)
    return await execute(args, transport=httpx.MockTransport(public_response), macro_provider=Macro())


async def test_v4_capture_replay_prepare_and_report_normal_entry_points(tmp_path):
    bundle = await discovery_bundle()
    assert bundle["status"] == "captured"
    assert bundle["schema"] == "atlas-public-evidence-v4"
    assert any(c.method == "candles" and c.arguments["limit"] == 360 for c in bundle["supplemental"])
    path = tmp_path / "capture.jsonl"
    write_bundle(path, bundle)
    frozen = read_bundle(path)
    comparison = await compare_bundle(frozen)
    assert comparison["status"] == "complete"
    assert comparison["discovery_v2"]["status"] == "complete"
    assert comparison["control"]["candidate_results"]["opportunities"] == bundle["record"].opportunities
    root = tmp_path / "study"
    await study.prepare(path, root)
    definition = study.definition(root)
    assert definition["comparison_sha256"] == study.semantic_digest(
        study.load(root / "comparison.json", max_bytes=128 * 1024**2))
    assert definition["capture_request"]["max_symbols"] == 320
    arm = definition["arms"]["discovery_v2"]
    assert set(arm["research_views"]) == {"watch_selection", "readiness", "emerging"}
    enrollment = study.enroll(root, now=definition["issued_at"])
    async def tick(at):
        return await study.tick(root, enabled=True, clock=lambda: at, transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[{"symbol": i.id.symbol, "priceChangePercent": "1",
                "lastPrice": "100", "quoteVolume": "10000000", "closeTime": int(at.timestamp()*1000),
                "openTime": int((at-timedelta(hours=24)).timestamp()*1000)} for i in definition["cohort"]])))
    await tick(enrollment["anchor"])
    await tick(enrollment["endpoint_24h"])
    report = study.report(root, 24, now=enrollment["endpoint_24h"])
    assert "discovery_v2_emerging" in report["arms"]
    assert report["performance_claims_permitted"] is False


def test_hot_definition_compacts_large_diagnostics_and_binds_original_event(tmp_path):
    event = {"symbol": "COINUSDT", "stage": "accumulation_gates", "reason": "failed",
             "gates": {"L6": False}, "inputs": {"close": "1", "large": "x" * 20000}}
    source = {"candidate_results": {"opportunities": (), "decisions": (event,) * 1000}}
    compact = study.long_arms(source)
    path = tmp_path / "definition.json"
    study.save(path, {"arms": {"control": compact}})
    assert path.stat().st_size < 1024**2
    restored = study.load(path)["arms"]["control"]["decisions"][0]
    assert restored["full_event_sha256"] == study.semantic_digest(event)
    assert restored["decisive_values"]["close"] == "1"
    assert "inputs" not in restored


async def test_v4_fails_closed_on_policy_and_missing_supplemental_evidence():
    from scripts.research_discovery import POLICY

    bundle = await discovery_bundle()
    bundle["research_policy"] = {**bundle["research_policy"], "watch_limit": 999}
    with pytest.raises(ReplayUnavailable, match="invalid_discovery_capture_policy"):
        await verify_bundle(bundle)
    bundle["research_policy"] = dict(POLICY)
    bundle["supplemental"] = bundle["supplemental"][1:]
    with pytest.raises(ReplayUnavailable, match="incomplete_or_duplicate_evidence_frames"):
        await verify_bundle(bundle)


async def test_valid_short_history_stays_explicitly_unavailable_per_symbol():
    def short_history(request):
        if request.url.path.endswith("klines") and request.url.params.get("limit") == "360":
            params = dict(request.url.params)
            params["limit"] = "300"
            request = httpx.Request("GET", request.url.copy_with(params=params))
        return public_response(request)

    args = SimpleNamespace(max_attempts=200, deadline_seconds=60, max_symbols=320,
                           max_supplemental=60, discovery_v2=True)
    bundle = await execute(args, transport=httpx.MockTransport(short_history), macro_provider=Macro())
    comparison = await compare_bundle(bundle)
    assert comparison["status"] == "complete"
    result = comparison["discovery_v2"]["candidate_results"]
    assert len(result["feature_unavailable_symbols"]) == 2
    assert all(r["state"] == "feature_unavailable" for r in result["discovery_observations"])


def test_v4_cli_requires_full_explicit_budget_before_any_network(tmp_path):
    result = subprocess.run([sys.executable, "-m", "scripts.research_capture_evidence",
        "--execute-public-capture", "--discovery-v2", "--max-symbols", "48",
        "--output", str(tmp_path / "capture.jsonl")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "320-symbol ceiling" in result.stderr


def test_transitions_use_only_preissued_definitions_and_known_prior_states():
    at = datetime(2026, 11, 11, 4, tzinfo=UTC)
    old = {"symbol": "COINUSDT", "state": "ready_waiting_activation", "temporal_features": {
        "available": True, "emerging": False, "last_closed_at": (at-timedelta(minutes=1)).isoformat()}}
    new = {"symbol": "COINUSDT", "state": "activated_gates_passed", "temporal_features": {
        "available": True, "emerging": True, "last_closed_at": (at+timedelta(hours=3)).isoformat()}}
    def arm(row):
        return {"source_policy": {"version": "fixture"}, "discovery_observations": (row,)}
    previous = {"study_id": "prior", "issued_at": at, "arms": {"discovery_v2": arm(old)}}
    current = {"capture_started_at": at+timedelta(hours=4), "arms": {"discovery_v2": arm(new)}}
    context, views = study.transition_context(previous, current)
    assert context["study_id"] == "prior"
    assert views["relative_strength_transition"] == views["readiness_to_activation"] == ("COINUSDT",)
    previous["issued_at"] = at + timedelta(hours=5)
    with pytest.raises(ValueError, match="transition window"):
        study.transition_context(previous, current)


def test_live_discovery_v2_contract_carries_quote_funding_and_temporal_features(settings):
    from algotrader.domain.discovery_features import discovery_temporal_features

    screener = CryptoOpportunityScreener(settings, feature_contract="discovery_v2")
    inst = make_instrument(symbol="TESTUSDT")
    quote = Quote(
        inst.id,
        Decimal("99.95"),
        Decimal("100.05"),
        Decimal("100"),
        Decimal("2000000"),
        funding_rate=Decimal("-0.00045"),
    )
    deriv = SimpleNamespace(funding_rate=Decimal("-0.00020"))
    assert screener._accumulation_funding_rate(quote, None) == Decimal("-0.00045")
    assert screener._accumulation_funding_rate(quote, deriv) == Decimal("-0.00020")
    assert CryptoOpportunityScreener(settings)._accumulation_funding_rate(quote, None) is None

    hourly = trending_candles(count=24)
    btc = trending_candles(count=24)
    assert discovery_temporal_features(hourly, btc) == temporal_features(hourly, btc)

