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


async def test_v4_accepts_newly_listed_symbol_with_short_4h_and_1w_histories():
    def newly_listed_history(request):
        if request.url.path.endswith("klines"):
            params = dict(request.url.params)
            if params.get("interval") == "4h":
                params["limit"] = "104"
                request = httpx.Request("GET", request.url.copy_with(params=params))
            elif params.get("interval") == "1w":
                params["limit"] = "3"
                request = httpx.Request("GET", request.url.copy_with(params=params))
        return public_response(request)

    args = SimpleNamespace(max_attempts=200, deadline_seconds=60, max_symbols=320,
                           max_supplemental=60, discovery_v2=True)
    bundle = await execute(args, transport=httpx.MockTransport(newly_listed_history), macro_provider=Macro())
    comparison = await compare_bundle(bundle)
    assert comparison["status"] == "complete"
    assert comparison["discovery_v2"]["status"] == "complete"


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


def test_discovery_v2_stealth_oi_and_fresh_oi_inflection_gates():
    from algotrader.domain.crypto_screener_models import SetupLabel
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

    base_inputs = LongGateInputs(
        close=Decimal("105"),
        ema50_4h=Decimal("100"),
        ema20_1h=Decimal("104"),
        ema200_4h=Decimal("98"),
        low_24h=Decimal("101"),
        atr14_4h=Decimal("3"),
        vcr_4h=Decimal("1.05"),
        ema20_4h=Decimal("103"),
        excursion_72h=Decimal("0.12"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55"),
        rsi_4h=Decimal("54"),
        high_30d=Decimal("118"),
        low_30d=Decimal("90"),
        poc_30d=Decimal("101"),
        val_30d=Decimal("95"),
        vah_30d=Decimal("112"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.11"),
        rvol_4h=Decimal("0.65"),
        oi_delta_7d=Decimal("0.15"),
        oi_delta_24h=Decimal("0.025"),
        oi_delta_4h=Decimal("0.004"),
        taker_buy_ratio_4h=Decimal("1.04"),
    )
    passed, details = evaluate_long_gates(base_inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert passed is True and details["L6"] is True

    # Fresh 24h+4h OI inflection after 7d washout with active RVOL/ignition
    inflection_inputs = replace(
        base_inputs,
        rvol_4h=Decimal("1.80"),
        has_kinetic_ignition=True,
        oi_delta_7d=Decimal("-0.04"),
        oi_delta_24h=Decimal("0.030"),
        oi_delta_4h=Decimal("0.015"),
        taker_buy_ratio_4h=Decimal("1.10"),
    )
    passed_inf, details_inf = evaluate_long_gates(inflection_inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert passed_inf is True and details_inf["L6"] is True




@pytest.mark.parametrize(("relative_strength", "return24", "expected"), [
    ("0.1", "2", False), ("1.5", "-1", False), ("1.5", "0", True),
    ("1.5", "9", True), ("1.5", "9.01", False),
])
def test_vvi_thresholds_are_separate_from_exploratory_emerging(relative_strength, return24, expected):
    hourly = trending_candles(count=24)
    btc = [replace(c, open=Decimal("100"), close=Decimal("100")) for c in hourly]
    last = Decimal("100") * (1 + Decimal(return24) / 100)
    prior6 = last / (1 + Decimal(relative_strength) / 100)
    hourly = [replace(c, open=Decimal("100"), close=last,
                      quote_volume=Decimal("150") if i >= 18 else Decimal("100"))
              for i, c in enumerate(hourly)]
    hourly[-6] = replace(hourly[-6], open=prior6)
    result = temporal_features(hourly, btc)
    assert result["vvi_ignition"] is expected


def test_research_challenger_explicitly_uses_production_feature_contract(settings):
    assert engine(settings)._feature_contract == "discovery_v2"


async def test_discovery_replay_consumes_recorded_feature_contract_keyword():
    from scripts.research_discovery import run_discovery

    bundle = await discovery_bundle()
    record = replace(bundle["record"], request={**bundle["record"].request, "feature_contract": "discovery_v2"})
    result = await run_discovery(record, bundle["supplemental"])
    assert result["status"] == "complete"


@pytest.mark.parametrize(("oi7d", "taker", "expected"), [
    ("-0.030", "1.01", True), ("-0.0301", "1.01", False), ("-0.028", "1.00", False),
])
def test_discovery_flow_tolerance_requires_confirmation(oi7d, taker, expected):
    from algotrader.domain.crypto_screener_models import SetupLabel
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

    inputs = LongGateInputs(
        close=Decimal("105"), ema50_4h=Decimal("100"), ema20_1h=Decimal("104"),
        ema200_4h=Decimal("98"), low_24h=Decimal("101"), atr14_4h=Decimal("3"),
        vcr_4h=Decimal("0.85"), ema20_4h=Decimal("103"), excursion_72h=Decimal("0.12"),
        ret_24h=Decimal("0.02"), rsi_1h=Decimal("55"), rsi_4h=Decimal("54"),
        high_30d=Decimal("125"), low_30d=Decimal("90"), poc_30d=Decimal("101"),
        val_30d=Decimal("95"), vah_30d=Decimal("120"), bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.11"), oi_delta_7d=Decimal(oi7d),
        oi_delta_4h=Decimal("-0.001"), taker_buy_ratio_4h=Decimal(taker),
        has_kinetic_ignition=True, discovery_v2=True,
    )
    _, details = evaluate_long_gates(inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert details["L6"] is expected
    _, legacy = evaluate_long_gates(replace(inputs, discovery_v2=False), SetupLabel.VCP_COMPRESSION_LONG)
    assert legacy["L6"] is False
    _, no_ignition = evaluate_long_gates(replace(inputs, has_kinetic_ignition=False,
                                               bb_inside_kc_4h=False, bb_bandwidth_4h=Decimal("0.30")),
                                        SetupLabel.MOMENTUM_EMERGING_LONG)
    assert no_ignition["L6"] is False


def test_discovery_screener_handles_unaligned_temporal_features_without_note_keyword_collision(settings):
    from algotrader.domain.scan_observations import ScanDecisionJournal

    journal = ScanDecisionJournal()
    challenger = DiscoveryScreener(settings, evaluated_at=datetime(2026, 11, 11, 4, tzinfo=UTC), journal=journal)
    hourly = trending_candles(count=24)
    btc = trending_candles(count=23)
    temporal = temporal_features(hourly, btc)
    assert temporal["available"] is False and "reason" in temporal
    temporal_reason = str(temporal.get("reason", "computed"))
    temporal_details = {k: v for k, v in temporal.items() if k != "reason"}
    challenger._note("COINUSDT", "temporal_features", temporal_reason, **temporal_details)
    snap = journal.snapshot()
    assert snap[-1]["stage"] == "temporal_features"
    assert snap[-1]["reason"] == "unaligned_or_missing_closed_history"


def test_below_poc_value_area_bracket_unlocks_promusdt():
    from algotrader.domain.execution_brackets import build_long_bracket

    bracket = build_long_bracket(
        close=Decimal("5.323"),
        ema50_4h=Decimal("5.150"),
        poc_30d=Decimal("5.390"),
        atr14_4h=Decimal("0.075"),
        local_compression_high_4h=Decimal("5.380"),
        swing_low_4h=Decimal("5.120"),
        high_30d=Decimal("6.499"),
        vah_30d=Decimal("5.825"),
        val_30d=Decimal("4.911"),
        tick_size=Decimal("0.001"),
        resistance_absorption_score=Decimal("55.0"),
    )
    assert bracket.asymmetric_rr >= Decimal("2.00")
    assert bracket.t2 > bracket.t1 > bracket.suggested_entry > bracket.invalidation_stop


def test_momentum_emerging_long_passes_l1_and_l3_base_consolidation():
    from algotrader.domain.crypto_screener_models import SetupLabel
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

    inputs = LongGateInputs(
        close=Decimal("1.1203"),
        ema50_4h=Decimal("1.0839"),
        ema20_1h=Decimal("1.1050"),
        ema200_4h=Decimal("1.2272"),
        low_24h=Decimal("1.0500"),
        atr14_4h=Decimal("0.0400"),
        vcr_4h=Decimal("0.92"),
        ema20_4h=Decimal("1.0950"),
        excursion_72h=Decimal("0.18"),
        ret_24h=Decimal("0.04"),
        rsi_1h=Decimal("60.0"),
        rsi_4h=Decimal("58.0"),
        high_30d=Decimal("1.9900"),
        low_30d=Decimal("0.9275"),
        poc_30d=Decimal("1.2781"),
        val_30d=Decimal("1.1931"),
        vah_30d=Decimal("1.5119"),
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=Decimal("0.143"),
        oi_delta_7d=Decimal("0.1909"),
        oi_delta_4h=Decimal("0.020"),
        taker_buy_ratio_4h=Decimal("1.3586"),
        ret_7d=Decimal("-0.0456"),
        has_kinetic_ignition=True,
        discovery_v2=True,
    )
    passed, details = evaluate_long_gates(inputs, SetupLabel.MOMENTUM_EMERGING_LONG)
    assert details["L1"] is True
    assert details["L3"] is True
    assert passed is True


def test_tight_squeeze_1h_taker_passes_l6_and_bleeding_oi_blocked_at_policy():
    from algotrader.domain.crypto_screener_models import (
        ChecklistVerification,
        CryptoMarketRegime,
        CryptoOpportunity,
        CryptoScreenerScoreBreakdown,
        SetupLabel,
    )
    from algotrader.domain.models import Direction
    from algotrader.domain.screener_policy import publication_rejection_reasons
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates
    from tests.conftest import make_instrument

    squeeze_inputs = LongGateInputs(
        close=Decimal("0.11296"),
        ema50_4h=Decimal("0.10735"),
        ema20_1h=Decimal("0.11100"),
        ema200_4h=Decimal("0.10903"),
        low_24h=Decimal("0.10800"),
        atr14_4h=Decimal("0.00200"),
        vcr_4h=Decimal("1.05"),
        ema20_4h=Decimal("0.11000"),
        excursion_72h=Decimal("0.12"),
        ret_24h=Decimal("0.03"),
        rsi_1h=Decimal("60.0"),
        rsi_4h=Decimal("58.0"),
        high_30d=Decimal("0.11441"),
        low_30d=Decimal("0.09506"),
        poc_30d=Decimal("0.10299"),
        val_30d=Decimal("0.10067"),
        vah_30d=Decimal("0.10880"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.048"),
        oi_delta_7d=Decimal("-0.009"),
        oi_delta_4h=Decimal("-0.001"),
        taker_buy_ratio_4h=Decimal("1.58"),
        taker_buy_ratio_1h=Decimal("1.85"),
        has_kinetic_ignition=False,
        discovery_v2=True,
    )
    _, details = evaluate_long_gates(squeeze_inputs, SetupLabel.VCP_COMPRESSION_LONG)
    assert details["L6"] is True

    bleeding_opp = CryptoOpportunity(
        evaluated_at=datetime(2026, 11, 15, 18, 31, tzinfo=UTC),
        instrument=make_instrument(symbol="CAPUSDT"),
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.VCP_COMPRESSION_LONG,
        execution_mode="RETEST_ACCUMULATION",
        score=Decimal("91.2"),
        conviction=Decimal("83.8"),
        grade="A",
        score_breakdown=CryptoScreenerScoreBreakdown(
            Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"),
            Decimal("80"), Decimal("80"), Decimal("1.0"), Decimal("91.2"),
        ),
        current_price=Decimal("1.00"),
        atr14=Decimal("0.03"),
        suggested_entry=Decimal("1.00"),
        suggested_stop=Decimal("0.95"),
        suggested_targets=(Decimal("1.08"), Decimal("1.13"), Decimal("1.20")),
        risk_reward_ratio=Decimal("2.50"),
        expected_value_r=Decimal("0.55"),
        estimated_win_rate_pct=Decimal("60.0"),
        oi_change_7d=Decimal("-0.0134"),
        oi_change_1h=Decimal("-0.0020"),
        funding_rate=Decimal("0.000195"),
        btc_beta=Decimal("1.00"),
        residual_return_24h=Decimal("0.01"),
        taker_ratio=Decimal("1.11"),
        taker_ratio_4h=Decimal("1.11"),
        rationale=("test",),
        macro_narrative="neutral",
        checklist_verification=ChecklistVerification(True, True, True, True, True),
        is_discovery_tier=True,
    )
    rejs = publication_rejection_reasons(bleeding_opp)
    assert "bleeding_7d_oi_without_fresh_inflow" in rejs


def test_active_range_bottom_buyer_absorption_classifies_naorisusdt():
    from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
    from algotrader.domain.crypto_screener_models import SetupLabel

    features = LongArchetypeFeatures(
        close=Decimal("0.02700"),
        ema50_4h=Decimal("0.02850"),
        poc_30d=Decimal("0.03325"),
        val_30d=Decimal("0.02678"),
        vah_30d=Decimal("0.03577"),
        low_30d=Decimal("0.02301"),
        low_24h=Decimal("0.02550"),
        low_4h=Decimal("0.02600"),
        local_swing_high_4h=Decimal("0.02977"),
        atr14_4h=Decimal("0.00138"),
        bb_bandwidth_4h=Decimal("0.221"),
        bb_inside_kc_4h=False,
        vcr_4h=Decimal("1.268"),
        rvol_4h=Decimal("0.53"),
        pos_in_range_30d=Decimal("0.15"),
        range_height_30d=Decimal("0.78"),
        dist_to_ceiling=Decimal("0.68"),
        ret_24h=Decimal("-0.021"),
        ret_7d=Decimal("-0.10"),
        btc_residual_alpha_24h=Decimal("-0.033"),
        macd_hist_4h=Decimal("-0.000129"),
        macd_hist_4h_prev=Decimal("-0.000191"),
        macd_hist_1h=Decimal("0.000041"),
        norm_momentum_1h=Decimal("-0.43"),
        cvd_24h=Decimal("1858886"),
        oi_delta_7d=None,
        oi_delta_24h=None,
        oi_delta_4h=None,
        funding_rate_8h=Decimal("0.00005"),
        taker_buy_ratio_4h=Decimal("1.4713"),
        taker_buy_ratio_1h=None,
        ema20_4h=Decimal("0.02750"),
        ema20_1h=Decimal("0.02650"),
        rvol_1h=Decimal("3.90"),
        low_48h=Decimal("0.02550"),
    )
    assert classify_long_archetype(features) is SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG


def test_publication_policy_vetoes_wait_for_retest_weak_taker_and_live_top10():
    from algotrader.domain.crypto_screener_models import (
        ChecklistVerification,
        CryptoMarketRegime,
        CryptoOpportunity,
        CryptoScreenerScoreBreakdown,
        SetupLabel,
    )
    from algotrader.domain.models import Direction
    from algotrader.domain.screener_policy import publication_rejection_reasons
    from tests.conftest import make_instrument

    base_opp = CryptoOpportunity(
        evaluated_at=datetime(2026, 11, 17, 12, 46, tzinfo=UTC),
        instrument=make_instrument(symbol="LIGHTUSDT"),
        direction=Direction.LONG,
        regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
        setup_label=SetupLabel.COIL_SPRING_LONG,
        execution_mode="WAIT_FOR_RETEST_ONLY",
        score=Decimal("81.2"),
        conviction=Decimal("77.1"),
        grade="A",
        score_breakdown=CryptoScreenerScoreBreakdown(
            Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"),
            Decimal("80"), Decimal("80"), Decimal("1.0"), Decimal("81.2"),
        ),
        current_price=Decimal("1.00"),
        atr14=Decimal("0.03"),
        suggested_entry=Decimal("1.00"),
        suggested_stop=Decimal("0.95"),
        suggested_targets=(Decimal("1.08"), Decimal("1.16"), Decimal("1.24")),
        risk_reward_ratio=Decimal("3.28"),
        expected_value_r=Decimal("0.82"),
        estimated_win_rate_pct=Decimal("60.0"),
        oi_change_7d=Decimal("0.0478"),
        oi_change_1h=Decimal("0.0050"),
        funding_rate=Decimal("0.000245"),
        btc_beta=Decimal("1.00"),
        residual_return_24h=Decimal("0.01"),
        taker_ratio=Decimal("0.828"),
        taker_ratio_4h=Decimal("0.828"),
        rationale=("test",),
        macro_narrative="neutral",
        checklist_verification=ChecklistVerification(True, True, True, True, True),
        is_discovery_tier=True,
    )
    rejs = publication_rejection_reasons(base_opp)
    assert "wait_for_retest_only_non_actionable" in rejs
    assert "insufficient_taker_buyer_absorption" in rejs

    top10_opp = replace(
        base_opp,
        instrument=make_instrument(symbol="SKYAIUSDT"),
        execution_mode="CEILING_BREAKOUT",
        taker_ratio_4h=Decimal("1.14"),
    )
    zero_taker = replace(top10_opp, taker_ratio_4h=Decimal("0"))
    assert "insufficient_taker_buyer_absorption" in publication_rejection_reasons(zero_taker)

    top10_rejs = publication_rejection_reasons(top10_opp, top_gainer_symbols=frozenset({"SKYAIUSDT"}))
    assert "already_in_top10_gainers_exhaustion_guard" in top10_rejs


def test_finding1_wide_excursion_exhaustion_guard_scope_and_stage1_unknown_parity():
    from algotrader.domain.crypto_screener_models import SetupLabel
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

    # SKYAIUSDT Stage-2 profile: excursion_72h = 0.281 > 0.22, oi_delta_24h = -0.030 < -0.015, rvol_4h = 0.85, taker_1h = 0.988
    skyai_stage2 = LongGateInputs(
        close=Decimal("0.03863"),
        ema50_4h=Decimal("0.03650"),
        ema20_1h=Decimal("0.03840"),
        ema200_4h=Decimal("0.03400"),
        low_24h=Decimal("0.03700"),
        atr14_4h=Decimal("0.00120"),
        vcr_4h=Decimal("0.80"),
        ema20_4h=Decimal("0.03800"),
        excursion_72h=Decimal("0.2806"),
        ret_24h=Decimal("-0.0034"),
        rsi_1h=Decimal("54.0"),
        rsi_4h=Decimal("56.3"),
        high_30d=Decimal("0.04262"),
        low_30d=Decimal("0.02567"),
        poc_30d=Decimal("0.03601"),
        val_30d=Decimal("0.03228"),
        vah_30d=Decimal("0.03940"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.11"),
        rvol_4h=Decimal("0.8546"),
        oi_delta_7d=Decimal("0.0284"),
        oi_delta_24h=Decimal("-0.0301"),
        oi_delta_4h=Decimal("0.0150"),
        taker_buy_ratio_4h=Decimal("1.140"),
        taker_buy_ratio_1h=Decimal("0.9877"),
        has_kinetic_ignition=False,
        discovery_v2=True,
    )
    passed_s2, details_s2 = evaluate_long_gates(skyai_stage2, SetupLabel.COIL_SPRING_LONG)
    assert details_s2["L6"] is False and passed_s2 is False

    # Stage-1 parity: when oi_delta_24h and taker_buy_ratio_1h are None (unknown), Stage 1 must NOT fabricate a negative
    skyai_stage1 = replace(
        skyai_stage2,
        oi_delta_7d=None,
        oi_delta_24h=None,
        oi_delta_4h=None,
        taker_buy_ratio_1h=None,
    )
    _, details_s1 = evaluate_long_gates(skyai_stage1, SetupLabel.COIL_SPRING_LONG)
    assert details_s1["L6"] is True

    # OUSDT profile: excursion_72h = 0.251 > 0.22, but oi_delta_24h = +0.058, rvol_4h = 9.49, taker_1h = 1.71 -> passes!
    ousdt_stage2 = replace(
        skyai_stage2,
        excursion_72h=Decimal("0.2506"),
        oi_delta_24h=Decimal("0.0579"),
        rvol_4h=Decimal("9.4947"),
        taker_buy_ratio_1h=Decimal("1.7101"),
    )
    passed_o, details_o = evaluate_long_gates(ousdt_stage2, SetupLabel.VCP_COMPRESSION_LONG)
    assert details_o["L6"] is True and passed_o is True

    # Boundary check: excursion_72h == 0.22 exactly is not > 0.22
    at_boundary = replace(skyai_stage2, excursion_72h=Decimal("0.2200"))
    _, details_b = evaluate_long_gates(at_boundary, SetupLabel.COIL_SPRING_LONG)
    assert details_b["L6"] is True

    # Archetype scope check: BREAKOUT_LONG is not subject to the coil/range exhaustion veto
    _, details_bo = evaluate_long_gates(skyai_stage2, SetupLabel.BREAKOUT_LONG)
    assert details_bo["L6"] is True


def test_finding2_emerging_base_reclaim_rsi_floor_blocks_robo_preserves_predict():
    from algotrader.domain.crypto_screener_models import SetupLabel
    from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates

    # ROBOUSDT profile: submerged under EMA200 (ema50 < ema200*0.95, close < ema200*0.96, close < val_30d), rsi_4h = 45.03 < 52.0
    robo_inputs = LongGateInputs(
        close=Decimal("0.004089"),
        ema50_4h=Decimal("0.004350"),
        ema20_1h=Decimal("0.004020"),
        ema200_4h=Decimal("0.005200"),
        low_24h=Decimal("0.003850"),
        atr14_4h=Decimal("0.000180"),
        vcr_4h=Decimal("1.13"),
        ema20_4h=Decimal("0.004010"),
        excursion_72h=Decimal("0.232"),
        ret_24h=Decimal("0.039"),
        rsi_1h=Decimal("52.0"),
        rsi_4h=Decimal("45.03"),
        high_30d=Decimal("0.006869"),
        low_30d=Decimal("0.003586"),
        poc_30d=Decimal("0.004998"),
        val_30d=Decimal("0.004200"),
        vah_30d=Decimal("0.005720"),
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=Decimal("0.21"),
        rvol_4h=Decimal("1.34"),
        oi_delta_7d=Decimal("0.063"),
        oi_delta_24h=Decimal("0.031"),
        oi_delta_4h=Decimal("0.030"),
        taker_buy_ratio_4h=Decimal("1.393"),
        taker_buy_ratio_1h=Decimal("1.271"),
        ret_7d=Decimal("-0.105"),
        has_kinetic_ignition=True,
        discovery_v2=True,
    )
    passed_robo, details_robo = evaluate_long_gates(robo_inputs, SetupLabel.MOMENTUM_EMERGING_LONG)
    assert details_robo["L1"] is False and passed_robo is False

    # Boundary check at rsi_4h = 52.0 (PREDICTUSDT is 61.01) -> passes L1
    predict_like = replace(robo_inputs, rsi_4h=Decimal("52.0"))
    passed_pred, details_pred = evaluate_long_gates(predict_like, SetupLabel.MOMENTUM_EMERGING_LONG)
    assert details_pred["L1"] is True and passed_pred is True


def test_finding3_checklist_semantics_expose_failed_conviction_factors_honestly():
    from algotrader.domain.crypto_screener_models import ChecklistVerification

    cv = ChecklistVerification(
        value_acceptance=True,
        volatility_structure=True,
        momentum_alignment=False,
        volume_microstructure=True,
        derivatives_positioning=True,
    )
    d = cv.as_dict()
    assert d["passed_count"] == 4
    assert d["failed_factors"] == ["momentum_alignment"]


def test_finding4_rolling_ready_queue_backoff_and_material_new_evidence_bypass():
    from algotrader.application.rolling_discovery import rank_ready_candidates

    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    # 1. Unchanged high-feature rejected candidate must NOT bypass backoff
    unchanged_history = [
        {
            "evaluation_at": now - timedelta(minutes=15),
            "pool": ["UNCHANGED_HIGH_USDT"],
            "control_symbols": [],
            "challenger_symbols": [],
            "scout_observations": [
                {"symbol": "UNCHANGED_HIGH_USDT", "relative_6h_pp": Decimal("5.0"), "turnover_acceleration": Decimal("2.0")},
            ],
        }
    ]
    ready_unchanged = [
        (Decimal("5.0"), Decimal("2.0"), "UNCHANGED_HIGH_USDT"),
        (Decimal("2.0"), Decimal("1.3"), "NEVER_EVALUATED_USDT"),
    ]
    assert [s for _, _, s in rank_ready_candidates(ready_unchanged, prior_summaries=unchanged_history, now=now)] == [
        "NEVER_EVALUATED_USDT",
        "UNCHANGED_HIGH_USDT",
    ]

    # 2. Material feature increase vs last evaluated features bypasses backoff, while unchanged rejection stays backed off,
    #    and data-unavailable evaluation is NOT treated as a rejection
    prior_summaries = [
        {
            "evaluation_at": now - timedelta(minutes=15),
            "pool": ("REPEAT_REJ_USDT", "REPEAT_SURGE_USDT", "DATA_UNAVAIL_USDT"),
            "control_symbols": (),
            "challenger_symbols": (),
            "unavailable_symbols": ("DATA_UNAVAIL_USDT",),
            "scout_observations": (
                {"symbol": "REPEAT_REJ_USDT", "relative_6h_pp": Decimal("4.4"), "turnover_acceleration": Decimal("1.80")},
                {"symbol": "REPEAT_SURGE_USDT", "relative_6h_pp": Decimal("2.2"), "turnover_acceleration": Decimal("1.25")},
                {"symbol": "DATA_UNAVAIL_USDT", "relative_6h_pp": Decimal("2.8"), "turnover_acceleration": Decimal("1.35")},
            ),
        }
    ]
    ready = [
        (Decimal("4.5"), Decimal("1.82"), "REPEAT_REJ_USDT"),    # High features but unchanged (+0.1 < 1.0, +0.02 < 0.25) -> backed off
        (Decimal("1.8"), Decimal("1.20"), "FRESH_NEVER_USDT"),   # Never evaluated -> active tier
        (Decimal("2.8"), Decimal("1.35"), "DATA_UNAVAIL_USDT"),  # Prior eval had unavailable data -> NOT backed off
        (Decimal("4.5"), Decimal("1.90"), "REPEAT_SURGE_USDT"),  # Surged +2.3 pp and +0.65x vs evaluated features -> bypasses backoff
    ]
    ordered = rank_ready_candidates(ready, prior_summaries=prior_summaries, now=now, backoff_minutes=45)
    ordered_syms = [s for _, _, s in ordered]
    assert ordered_syms == ["REPEAT_SURGE_USDT", "DATA_UNAVAIL_USDT", "FRESH_NEVER_USDT", "REPEAT_REJ_USDT"]

    # 3. Waiting-age mechanism promotes long-waiting deferred candidate over newly arrived higher-raw candidate
    aging_history = [
        {
            "evaluation_at": now - timedelta(minutes=45),
            "pool": ("OTHER1_USDT",),
            "control_symbols": (),
            "challenger_symbols": (),
            "qualification_admission": ({"symbol": "WAITING_FRESH_USDT", "reason": "qualification_capacity"},),
        },
        {
            "evaluation_at": now - timedelta(minutes=30),
            "pool": ("OTHER2_USDT",),
            "control_symbols": (),
            "challenger_symbols": (),
            "qualification_admission": ({"symbol": "WAITING_FRESH_USDT", "reason": "qualification_capacity"},),
        },
        {
            "evaluation_at": now - timedelta(minutes=15),
            "pool": ("OTHER3_USDT",),
            "control_symbols": (),
            "challenger_symbols": (),
            "qualification_admission": ({"symbol": "WAITING_FRESH_USDT", "reason": "qualification_capacity"},),
        },
    ]
    ready_aging = [
        (Decimal("3.2"), Decimal("1.40"), "NEW_ARRIVAL_USDT"),
        (Decimal("2.0"), Decimal("1.30"), "WAITING_FRESH_USDT"),  # Waited 45m (3 cycles * 0.75 = +2.25 boost -> 4.25 effective)
    ]
    assert [s for _, _, s in rank_ready_candidates(ready_aging, prior_summaries=aging_history, now=now)] == [
        "WAITING_FRESH_USDT",
        "NEW_ARRIVAL_USDT",
    ]

    # 4. Reserved exploration capacity guarantees at least 1 fresh candidate in top 4 slots even when 4 prior symbols outscore it
    expired_backoff_history = [
        {
            "evaluation_at": now - timedelta(minutes=60),
            "pool": ("PREV1_USDT", "PREV2_USDT", "PREV3_USDT", "PREV4_USDT"),
            "control_symbols": (),
            "challenger_symbols": (),
        }
    ]
    ready_crowded = [
        (Decimal("6.0"), Decimal("2.0"), "PREV1_USDT"),
        (Decimal("5.5"), Decimal("1.9"), "PREV2_USDT"),
        (Decimal("5.0"), Decimal("1.8"), "PREV3_USDT"),
        (Decimal("4.5"), Decimal("1.7"), "PREV4_USDT"),
        (Decimal("1.5"), Decimal("1.2"), "FRESH_EXPLORE_USDT"),
    ]
    top4 = [s for _, _, s in rank_ready_candidates(ready_crowded, prior_summaries=expired_backoff_history, now=now, slots=4)[:4]]
    assert top4 == ["PREV1_USDT", "PREV2_USDT", "PREV3_USDT", "FRESH_EXPLORE_USDT"]


async def test_finding5_stage2_probe_ordinal_class_precedence_and_canonical_tie_break():
    from algotrader.application.crypto_screener import CryptoOpportunityScreener
    from algotrader.config import Settings
    from algotrader.domain.models import Direction, MarketKind, Venue
    from algotrader.domain.scan_observations import ScanDecisionJournal
    from tests.conftest import make_instrument
    from tests.test_crypto_screener import FakeCryptoMarketData, FakeDerivativesData
    from tests.test_scan_replay import Macro

    class TieBreakMarket(FakeCryptoMarketData):
        async def quotes(self, instruments):
            quotes = await super().quotes(instruments)
            return [
                replace(
                    q,
                    funding_rate=Decimal("-0.05")
                    if not q.instrument_id.symbol.startswith("STRUCT")
                    else Decimal("0"),
                )
                for q in quotes
            ]

    class StubScreener(CryptoOpportunityScreener):
        async def _evaluate_single(self, **kw):
            if kw["inst"].id.symbol.startswith("STRUCT"):
                self._stage1_ready_symbols.add(kw["inst"].id.symbol)
            return []

    async def run_for_order(symbol_list: list[str]) -> list[str]:
        insts = [
            make_instrument(symbol=s, venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
            for s in symbol_list
        ]
        market = TieBreakMarket(insts, {}, {})
        journal = ScanDecisionJournal()
        screener = StubScreener(
            Settings.model_construct(stage2_capacity_rollover_enabled=False), Macro(), journal=journal, feature_contract="discovery_v2"
        )
        await screener.screen(
            market,
            FakeDerivativesData({}),
            max_symbols=100,
            target_direction=Direction.LONG,
            enforce_quality=True,
        )
        assert screener.scan_counts["stage2_probes"] == 30
        assert screener.scan_counts["stage2_probe_overflow"] == 2
        return [
            d["symbol"]
            for d in journal.snapshot()
            if d["stage"] == "stage2" and d["reason"] == "admitted"
        ]

    symbols = ["STRUCTUSDT"] + [f"ANOM{i:02}USDT" for i in range(31)]
    admitted_forward = await run_for_order(symbols)
    admitted_reversed = await run_for_order(list(reversed(symbols)))

    assert "STRUCTUSDT" in admitted_forward and "STRUCTUSDT" in admitted_reversed
    assert set(admitted_forward) == set(admitted_reversed)
    assert set(admitted_forward) == {"STRUCTUSDT"} | {f"ANOM{i:02}USDT" for i in range(29)}

