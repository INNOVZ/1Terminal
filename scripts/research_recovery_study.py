"""Opt-in frozen recovery arm, bounded observation and matched-rank reporting."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from algotrader.application.recovery_episodes import RecoveryEpisodeService
from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.application.scan_recording import supported_screening_configuration
from algotrader.application.scan_replay import ReplayMarket
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION
from algotrader.domain.recovery_episodes import (
    EPISODE_POLICY,
    INTERVAL,
    RecoveryEpisode,
    boundary,
)
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.infrastructure.recovery_episodes import (
    RecoveryEpisodeStore,
    episode_values,
    read_recovery_snapshot,
    serialize,
)
from scripts.replay_contract import ReplayUnavailable

POLICY = {
    "version": "prospective-recovery-v1",
    "candidate_policy": RECOVERY_POLICY_VERSION,
    "episode_policy": EPISODE_POLICY,
    "capacity": 30,
    "cadence_seconds": 900,
    "phase_seconds": 60,
    "cycle_lateness_seconds": 30,
    "selection": "frozen_initial_recovery_candidates_in_engine_order",
    "confirmation_window_hours": 24,
    "tracking_window_hours": 48,
    "early_detection": "confirmed_before_first_observed_top10_with_recent_certified_outside_rank",
    "rank_match_max_age_seconds": 345,
    "dynamic_false_positive": "nonpositive_study_endpoint_return_from_observed_confirmation_quote",
}


async def run_recovery(record, supplemental, *, decision_at, rolling=False):
    from scripts.research_discovery import validate_history_overlap
    from scripts.research_pipeline_comparison import AuditedTape

    validate_history_overlap(record, supplemental)
    tape = AuditedTape((*record.inputs, *supplemental))
    market = ReplayMarket(tape)
    journal = ScanDecisionJournal()
    config = record.identity["configuration"]
    if not supported_screening_configuration(config):
        raise ReplayUnavailable("unsupported_screener_configuration")
    if rolling:
        from algotrader.application.activity_recovery import ActivityRecoveryScreener
        cls = ActivityRecoveryScreener
    else:
        cls = RecoveryScreener
    engine = cls(Settings.model_construct(**config), market,
                              evaluated_at=record.started_at, decision_at=decision_at, journal=journal)
    request = dict(record.request)
    enabled = request.pop("derivatives_enabled")
    request.pop("feature_contract", None)
    opportunities = await engine.screen(market, market if enabled else None, **request)
    if tape.missing or engine._fetch_failures:
        return {"status": "unavailable", "reason": "recovery_inputs_incomplete", "candidate_results": None}
    seeds = []
    for opportunity in opportunities:
        if opportunity.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG:
            seeds.append({"state": episode_values(RecoveryEpisode.enroll(opportunity, decision_at)),
                          "evidence": {"kind": "enrollment", "candidate": opportunity.as_dict()}})
    return {"status": "complete", "policy": dict(POLICY), "candidate_results": {
        "opportunities": tuple(o.as_dict() for o in opportunities),
        "counts": dict(engine.scan_counts), "decisions": journal.snapshot(),
        "feature_unavailable_symbols": tuple(sorted(engine._feature_unavailable)),
        "cohort_symbols": tuple(i.id.symbol for i in journal.cohort),
        "recovery_seeds": tuple(seeds),
    }}


def freeze_arm(result, issued_at):
    records, admissions = [], []
    for record in result["candidate_results"]["recovery_seeds"]:
        seed = RecoveryEpisode(**record["state"])
        if not 0 <= (issued_at - seed.source_at).total_seconds() <= 600:
            raise ValueError("recovery qualification too old at issuance")
        admitted = len(records) < POLICY["capacity"]
        admissions.append({"symbol": seed.instrument.id.symbol,
                           "status": "enrolled" if admitted else "pool_capacity"})
        if admitted:
            seed = replace(seed, first_seen=issued_at, qualified_at=issued_at, checked_at=issued_at,
                           last_bar_open=boundary(issued_at) - INTERVAL
                           if boundary(issued_at) == issued_at else boundary(issued_at),
                           initial_interval_verified=boundary(issued_at) == issued_at)
            records.append({"state": episode_values(seed), "evidence": record["evidence"]})
    return {"policy": dict(POLICY), "seeds": tuple(records), "admissions": tuple(admissions)}


def validate_arm(definition):
    arm = definition.get("recovery")
    if arm is None:
        return
    if arm["policy"] != POLICY or len(arm["seeds"]) > POLICY["capacity"]:
        raise ValueError("recovery study policy mismatch")
    if "rolling" in arm:
        from algotrader.application.rolling_discovery import ROLLING_POLICY
        if arm["rolling"]["policy"] != ROLLING_POLICY:
            raise ValueError("rolling discovery study policy mismatch")
        if arm["rolling"]["benchmark"].id.symbol != "BTCUSDT":
            raise ValueError("invalid rolling benchmark")
        if arm["rolling"]["macro"].observed_at > definition["issued_at"]:
            raise ValueError("rolling macro context is from after issuance")
        if not supported_screening_configuration(arm["rolling"]["configuration"]):
            raise ValueError("unsupported rolling configuration")
    symbols = {i.id for i in definition["cohort"]}
    seen = set()
    for record in arm["seeds"]:
        seed = RecoveryEpisode(**record["state"])
        if (seed.instrument.id not in symbols or seed.instrument.id in seen
                or seed.first_seen != definition["issued_at"] or seed.revision != 0
                or seed.status != "waiting" or seed.triggered_at is not None):
            raise ValueError("invalid frozen recovery enrollment")
        seen.add(seed.instrument.id)


def initialize(root, definition, *, definition_sha256=None):
    from scripts.research_prospective import semantic_digest

    validate_arm(definition)
    store = RecoveryEpisodeStore(root / "recovery" / "episodes.sqlite", capacity=POLICY["capacity"])
    store.bind({"study_id": definition["study_id"], "definition_sha256": definition_sha256 or semantic_digest(definition)})
    store.enroll_records(definition["recovery"]["seeds"])
    expected = {r["state"]["episode_id"] for r in definition["recovery"]["seeds"]}
    if "rolling" in definition["recovery"]:
        from algotrader.infrastructure.rolling_discovery import RollingDiscoveryStore
        for summary in RollingDiscoveryStore(store).summaries():
            expected.update(row["episode_id"] for row in summary["admissions"] if row["status"] == "enrolled")
    if {e.episode_id for e in store.episodes()} != expected:
        raise ValueError("recovery database contains unregistered episodes")
    return store


async def tick(root, *, enabled, clock=lambda: datetime.now(UTC), market=None, derivatives=None,
               validated_session=None, transport=None):
    from scripts import research_prospective as study

    if not enabled:
        return {"status": "disabled", "http_attempts": 0}
    with study.locked(root, ".recovery.lock"):
        definition, enrollment = validated_session if validated_session is not None else study.session(root)
        if "recovery" not in definition:
            return {"status": "not_configured"}
        now = clock()
        if now < enrollment["anchor"]:
            return {"status": "waiting_for_anchor"}
        if now > study.collection_end(enrollment) + timedelta(seconds=120):
            return {"status": "window_ended"}
        anchor = root / "snapshots" / "000000.json"
        if not anchor.exists() or study.load(anchor)["status"] != "observed":
            return {"status": "awaiting_valid_anchor"}
        for progress_path in (root / "progress.json", root / "recovery" / "progress.json"):
            if progress_path.exists():
                progress = study.load(progress_path)
                latest = progress.get("latest") or {}
                if latest.get("cooldown_until") and now < latest["cooldown_until"]:
                    return {"status": "exchange_cooldown"}
        quarter = int(now.timestamp()) // POLICY["cadence_seconds"]
        scheduled = datetime.fromtimestamp(quarter * POLICY["cadence_seconds"] + POLICY["phase_seconds"], UTC)
        if now < scheduled:
            return {"status": "waiting_for_recovery_phase"}
        path = root / "recovery" / "cycles" / f"{quarter}.json"
        if path.exists():
            return {"status": "already_recorded"}
        study.storage_ok(root)
        store = initialize(root, definition, definition_sha256=enrollment["definition_sha256"])
        record = {"study_id": definition["study_id"], "definition_sha256": enrollment["definition_sha256"],
                  "scheduled_at": scheduled, "requested_at": now, "completed_at": None,
                  "status": "missed", "reason": "cycle_late", "states": ()}
        if (now - scheduled).total_seconds() <= POLICY["cycle_lateness_seconds"]:
            client = None
            budget = None
            try:
                if market is None:
                    from algotrader.adapters.binance.client import BinanceHttpClient
                    from algotrader.adapters.binance.futures import BinanceUsdmAdapter
                    from scripts.research_capture_evidence import EvidenceTransport
                    budget = EvidenceTransport(transport or httpx.AsyncHTTPTransport(), attempts=600,
                                               seconds=360, byte_limit=32 * 1024**2, retain_payloads=False)
                    client = BinanceHttpClient("https://fapi.binance.com", transport=budget, max_rate_limit_retries=0,
                                               max_transport_retries=0)
                    market = derivatives = BinanceUsdmAdapter(client)
                if derivatives is None:
                    raise ValueError("recovery derivatives port is required")
                if "rolling" in definition["recovery"]:
                    from algotrader.application.rolling_discovery import rolling_cycle
                    discovery = await rolling_cycle(market, derivatives, store, definition,
                                                    f"rolling-{quarter}", clock=clock,
                                                    accrual_until=enrollment["endpoint_48h"])
                    record["discovery"] = discovery
                    coverage = discovery["scout_counts"]
                    record["discovery_unavailable"] = (coverage.get("selected", 0) > 0 and coverage.get("available", 0) == 0
                                                       or coverage.get("deep_input_unavailable", False))
                    states = tuple(s for s in store.episodes() if s.ended_at is None or s.checked_at >= now)
                else:
                    states = await RecoveryEpisodeService(store).refresh(market, derivatives, clock=clock)
                record.update(status="observed", reason=None,
                              states=tuple({"episode_id": s.episode_id, "status": s.status,
                                            "coverage_complete": s.coverage_complete} for s in states))
            except Exception as exc:
                record.update(status="failed", reason=type(exc).__name__)
            finally:
                if client is not None:
                    await client.close()
                if budget is not None:
                    record["http_attempts"] = budget.attempts
                    record["response_bytes"] = budget.bytes
                    record["receipts"] = tuple({key: value for key, value in receipt.items() if key != "payload"}
                                               for receipt in budget.receipts)
                    if budget.stopped:
                        record.update(status="failed", reason=budget.stopped)
                    if budget.stopped in ("exchange_cooldown", "exchange_weight_ceiling"):
                        last = budget.receipts[-1]
                        default = 3600 if last.get("status") == 418 else 60
                        try:
                            retry = float(last.get("retry_after") or default)
                            if not 0 <= retry <= 10**10:
                                raise ValueError("invalid retry")
                            if retry > 10**9:
                                retry = max(0, retry - now.timestamp())
                        except (ValueError, TypeError):
                            retry = default
                        record["cooldown_until"] = now + timedelta(seconds=max(default, retry))
            record["completed_at"] = clock()
        study.save(path, record)
        study.write_recovery_progress(root, enrollment, record, now=clock())
        return {"status": record["status"], "cycles_path": str(path)}


async def run(root, *, validated_session, clock=lambda: datetime.now(UTC), sleep=asyncio.sleep):
    from scripts import research_prospective as study

    signature = study.session_signature(root)
    while True:
        if study.session_signature(root) != signature:
            raise ValueError("study changed during recovery collection")
        try:
            result = await tick(root, enabled=True, clock=clock, validated_session=validated_session)
        except Exception as exc:
            # Isolate recovery failures so the rank sampler continues on schedule.
            try:
                study.write_recovery_progress(root, validated_session[1],
                                              {"status": "failed", "reason": type(exc).__name__}, now=clock())
            except (OSError, ValueError) as progress_error:
                print({"component": "recovery", "status": "progress_unavailable",
                       "reason": type(progress_error).__name__}, flush=True)
            result = {"status": "failed"}
        if result["status"] in ("window_ended", "not_configured"):
            return result
        await sleep(5)


def snapshot(root, definition, endpoint):
    from scripts.research_prospective import semantic_digest
    from scripts.research_recovery_timeline import replay

    path = root / "recovery" / "episodes.sqlite"
    if not path.exists():
        return None
    episodes, records = read_recovery_snapshot(path, binding={
        "study_id": definition["study_id"], "definition_sha256": semantic_digest(definition)})
    replay(episodes, records)
    seeds = {r["state"]["episode_id"]: r for r in definition["recovery"]["seeds"]}
    actual = {r["state"]["episode_id"]: r for r in records if r["evidence"]["kind"] == "enrollment"
              and "discovery_decision_id" not in r["evidence"]}
    if actual != seeds:
        raise ValueError("recovery enrollment differs from frozen study")
    discovery_summaries = ()
    if "rolling" in definition["recovery"]:
        from algotrader.infrastructure.rolling_discovery import (
            read_rolling_summaries,
        )
        discovery_summaries = read_rolling_summaries(path)
        indexed = {row["decision_id"]: row for row in discovery_summaries}
        by_decision = {}
        for record in records:
            decision = record["evidence"].get("discovery_decision_id")
            if record["evidence"]["kind"] == "enrollment" and decision is not None:
                by_decision.setdefault(decision, []).append(record)
        for decision, enrolled in by_decision.items():
            if decision not in indexed:
                raise ValueError("unregistered rolling enrollment")
            summary = indexed[decision]
            if (summary["study_id"] != definition["study_id"]
                    or summary["policy"] != definition["recovery"]["rolling"]["policy"]):
                raise ValueError("rolling frame binding mismatch")
            admitted_ids = {row["episode_id"] for row in summary["admissions"] if row["status"] == "enrolled"}
            if admitted_ids != {r["state"]["episode_id"] for r in enrolled}:
                raise ValueError("rolling admission count mismatch")
            for record in enrolled:
                key = record["state"]["episode_id"]
                original = {**record, "evidence": {k: v for k, v in record["evidence"].items() if k != "discovery_decision_id"}}
                if hashlib.sha256(serialize(original).encode()).hexdigest() != summary["seed_proofs"].get(key):
                    raise ValueError("rolling enrollment evidence mismatch")
                if record["state"]["instrument"].id not in {i.id for i in definition["cohort"]}:
                    raise ValueError("rolling episode outside frozen cohort")
    filtered = tuple(r for r in records if r["state"]["checked_at"] <= endpoint)
    latest = {r["state"]["episode_id"]: RecoveryEpisode(**r["state"]) for r in filtered}
    result = replay(tuple(latest.values()), filtered)
    result["unavailable_quote_observations"] = sum(
        r["evidence"]["kind"] == "refresh" and r["evidence"]["quote"] is None for r in filtered)
    result["unconfirmed_missing_flow_observations"] = sum(
        r["evidence"]["kind"] == "refresh" and r["state"]["triggered_at"] is None
        and r["evidence"]["derivatives"] is None for r in filtered)
    result["rolling_decisions"] = tuple(row for row in discovery_summaries if row["issued_at"] <= endpoint)
    return latest, result


def outcome_report(root, context, definition, enrollment, endpoint, rank_states, confirmation_ranks,
                   *, confirmation_outcomes=None, control_matches=None, followup_endpoint=None):
    if context is None:
        return {"status": "unavailable", "reason": "recovery_database_missing",
                "performance_claims_permitted": False}
    latest, result = context
    confirmation_outcomes = confirmation_outcomes or {}
    control_matches = control_matches or {}
    horizon_end = followup_endpoint if followup_endpoint is not None and followup_endpoint > endpoint else endpoint
    result["study_id"] = definition["study_id"]
    result["admissions"] = definition["recovery"]["admissions"]
    result["collection"] = collection_coverage(root=root,
                                               definition=definition, enrollment=enrollment, endpoint=endpoint)
    followup_collection = (
        collection_coverage(root=root, definition=definition, enrollment=enrollment,
                            endpoint=horizon_end, start=endpoint)
        if horizon_end > endpoint
        else None
    )
    if followup_collection is not None:
        result["followup_collection"] = followup_collection
    result["status"] = (
        "degraded"
        if result["collection"]["status"] != "complete"
        or (followup_collection is not None and followup_collection["status"] != "complete")
        else "bounded"
        if result["unavailable_quote_observations"]
        or result["unconfirmed_missing_flow_observations"]
        or result["collection"]["discovery_unknown_cycles"]
        or (followup_collection is not None and followup_collection["discovery_unknown_cycles"])
        else "complete"
    )
    for row in result["outcomes"]:
        episode = latest[row["episode_id"]]
        state = rank_states[row["symbol"]]
        first = state["first_observed_top10_at"]
        trigger = episode.triggered_at
        matched = confirmation_ranks.get(episode.episode_id)
        recent = matched is not None and trigger is not None and 0 <= (trigger - matched["at"]).total_seconds() <= POLICY["rank_match_max_age_seconds"]
        early = None
        if trigger is not None and first is not None:
            if first <= trigger or state["initial_inside"] is True:
                early = False
            elif state["initial_inside"] is False and recent and matched["inside"] is False:
                early = True
        endpoint_at = state.get("endpoint_price_at") or endpoint
        confirmed_in_main_window = (
            trigger is not None and trigger <= endpoint and trigger <= endpoint_at
        )
        row.update(first_observed_top10_at=first.isoformat() if first else None,
                   confirmation_rank_known=bool(recent and matched["inside"] is not None),
                   observed_early_detection=early,
                   observed_lead_minutes=(first - trigger).total_seconds() / 60 if early is True else None,
                   study_endpoint_return_from_confirmation_pct=(
                       (state["endpoint_price"] / episode.trigger_price - 1) * 100
                       if confirmed_in_main_window
                       and episode.trigger_price
                       and state["endpoint_price"] is not None
                       else None
                   ))
        row["followup_minutes_to_study_endpoint"] = (
            (endpoint - trigger).total_seconds() / 60
            if confirmed_in_main_window
            else None
        )
        if horizon_end > endpoint:
            row["followup_minutes_to_followup_endpoint"] = (
                (horizon_end - trigger).total_seconds() / 60
                if trigger is not None and trigger <= horizon_end
                else None
            )
        for hours in (24, 48):
            outcome = confirmation_outcomes.get((episode.episode_id, hours))
            row[f"return_{hours}h_after_confirmation_pct"] = ((outcome["price"]/episode.trigger_price-1)*100
                if outcome is not None and episode.trigger_price else None)
            row[f"outcome_{hours}h_state"] = ("unconfirmed" if trigger is None else "censored"
                if trigger+timedelta(hours=hours) > horizon_end else "observed" if outcome else "unknown")
    control = {}
    for decision in result.get("rolling_decisions", ()):
        for symbol in decision["control_symbols"]:
            control.setdefault(symbol, decision["issued_at"])
    control_rows = []
    for symbol, issued in control.items():
        first = rank_states[symbol]["first_observed_top10_at"]
        match = control_matches.get(symbol)
        recent = match is not None and 0 <= (issued-match["at"]).total_seconds() <= POLICY["rank_match_max_age_seconds"]
        early = None
        if first is not None:
            if first <= issued or rank_states[symbol]["initial_inside"] is True:
                early = False
            elif rank_states[symbol]["initial_inside"] is False and recent and match["inside"] is False:
                early = True
        control_rows.append({"symbol":symbol, "issued_at":issued, "observed_early_detection":early})
    result["matched_pool_control"] = {"scope":"published_core_qualification_on_the_same_evaluated_pool",
                                      "symbols":tuple(control_rows), "candidate_count":len(control_rows),
                                      "observed_early_detection_count":sum(r["observed_early_detection"] is True for r in control_rows)}
    regimes = {episode_id: regime for decision in result.get("rolling_decisions", ())
               for episode_id, regime in decision["positioning"].items()}
    for row in result["outcomes"]:
        row["positioning_regime_at_qualification"] = regimes.get(row["episode_id"], "unknown")
    confirmed = [r for r in result["outcomes"] if r["triggered_at"] is not None]
    known = [r for r in confirmed if r["study_endpoint_return_from_confirmation_pct"] is not None]
    detected = {r["symbol"] for r in confirmed if r["observed_early_detection"] is True}
    result.update(confirmed_count=len(confirmed), confirmed_symbol_count=len({r["symbol"] for r in confirmed}),
                  endpoint_known_count=len(known),
                  nonpositive_endpoint_count=sum(r["study_endpoint_return_from_confirmation_pct"] <= 0 for r in known),
                  observed_early_detection_count=len(detected),
                  late_or_already_top10_confirmation_count=sum(r["observed_early_detection"] is False for r in confirmed),
                  unverified_early_timing_count=sum(r["observed_early_detection"] is None for r in confirmed),
                  missed_or_unverified_new_observed_gainers=tuple(sorted(s for s, r in rank_states.items()
                      if r["initial_inside"] is False and r["first_observed_top10_at"] is not None and s not in detected)))
    result["alert_relative_horizons"] = {
        str(hours): {"known_count": sum(r[f"outcome_{hours}h_state"] == "observed" for r in confirmed),
                     "nonpositive_count": sum(r[f"return_{hours}h_after_confirmation_pct"] is not None
                                              and r[f"return_{hours}h_after_confirmation_pct"] <= 0 for r in confirmed),
                     "censored_count": sum(r[f"outcome_{hours}h_state"] == "censored" for r in confirmed),
                     "unknown_count": sum(r[f"outcome_{hours}h_state"] == "unknown" for r in confirmed)}
        for hours in (24,48)}
    result["positioning_groups"] = {
        name: {"confirmed_count": sum(r["positioning_regime_at_qualification"] == name for r in confirmed),
               "nonpositive_endpoint_count": sum(r["positioning_regime_at_qualification"] == name
                  and r["study_endpoint_return_from_confirmation_pct"] is not None
                  and r["study_endpoint_return_from_confirmation_pct"] <= 0 for r in confirmed)}
        for name in ("oi_expanding","oi_flat","oi_contracting","unknown")}
    return result


def collection_coverage(*, root, definition, enrollment, endpoint, start=None):
    from scripts import research_prospective as study

    cadence, phase = POLICY["cadence_seconds"], POLICY["phase_seconds"]
    if start is None:
        first = int(enrollment["anchor"].timestamp()) // cadence
        if first * cadence + phase < enrollment["anchor"].timestamp():
            first += 1
    else:
        first = int((start.timestamp() - phase) // cadence) + 1
    last = int((endpoint.timestamp() - phase) // cadence)
    counts = {"observed": 0, "missed": 0, "failed": 0, "absent": 0, "incomplete_data": 0, "discovery_unknown_cycles": 0}
    for quarter in range(first, last + 1):
        path = root / "recovery" / "cycles" / f"{quarter}.json"
        if not path.exists():
            counts["absent"] += 1
            continue
        row = study.load(path)
        if (row["study_id"] != definition["study_id"]
                or row["definition_sha256"] != enrollment["definition_sha256"]
                or row["scheduled_at"].timestamp() != quarter * cadence + phase
                or row["status"] not in ("observed", "missed", "failed")):
            raise ValueError("invalid recovery cycle binding")
        counts[row["status"]] += 1
        if any(not state["coverage_complete"] for state in row["states"]) or row.get("discovery_unavailable"):
            counts["incomplete_data"] += 1
        discovery = row.get("discovery", {}).get("scout_counts", {})
        if discovery.get("unknown", 0) or discovery.get("deep_input_unavailable"):
            counts["discovery_unknown_cycles"] += 1
    return {"status": "degraded" if any(counts[k] for k in ("missed", "failed", "absent", "incomplete_data")) else "complete",
            "expected_cycles": max(0, last - first + 1), **counts}
