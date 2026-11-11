"""Explicitly enrolled, single-cohort prospective engine comparison. Public data only.

No production imports this CLI. Prepare is offline; enrollment reserves a future anchor.
Only explicit enabled ticks acquire data. Missing slots are never retrospectively filled.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

from algotrader.application.scan_recording import screening_identity
from algotrader.domain.rank_observations import RankPolicy, validate_cohort
from algotrader.domain.scan_observations import decode_value, encode_value
from scripts.replay_contract import ReplayUnavailable, timestamp
from scripts.research_capture_evidence import EvidenceTransport
from scripts.research_evidence_stream import read_bundle, semantic_digest
from scripts.research_pipeline_comparison import compare_bundle, tool_identity
from scripts.research_rank_contract import normalize_ranks
from scripts.research_verified_replay import offline, read_json, write_report

POLICY = {
    "version": "paired-prospective-v2",
    "input_contract": "atlas-public-evidence-v3",
    "discovery_input_contract": "atlas-public-evidence-v4",
    "challenger": "quote-funding-carry-v1-raw-rate-unchanged-budgets",
    "rank_contract": "paired-rank-bounds-v3",
    "interval_seconds": 300,
    "lateness_seconds": 30,
    "request_deadline_seconds": 15,
    "horizons_hours": (24, 48),
    "max_enrollment_delay_seconds": 600,
    "reference_size": 10,
    "radar_fresh_limit": 16,
    "false_positive_definition": "nonpositive_sampled_endpoint_return; unknown excluded",
    "early_detection_definition": "issued_before_first_observed_top10_and_outside_at_anchor",
    "miss_definition": "observed_top10_symbol_absent_from_frozen_long_arm",
    "price_path_definition": "five_minute_samples_not_intrabar_extremes_or_executed_returns",
    "universe_definition": "frozen_original_eligible_canonical_usdm_cohort",
    "min_free_bytes": 2 * 1024**3,
    "max_study_bytes": 2 * 1024**3,
    "report_execution": "independent_worker_separate_lock",
    "coverage_semantics": "observed_missed_failed_absent_v2",
    "max_transition_context_age_seconds": 8 * 3600,
}


def identity():
    return {
        "screening": screening_identity(),
        "tools": tool_identity(),
        "runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rank_normalizer": hashlib.sha256(
            Path(normalize_ranks.__code__.co_filename).read_bytes()
        ).hexdigest(),
        "policy": POLICY,
    }


def save(path: Path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_report(
        path,
        {
            "schema": "atlas-paired-envelope-v1",
            "sha256": semantic_digest(payload),
            "payload": encode_value(payload),
        },
    )


def load(path: Path, *, max_bytes=16 * 1024**2):
    if not 0 < max_bytes <= 128 * 1024**2 or path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError("invalid study record path/size")
    row = read_json(path)
    if set(row) != {"schema", "sha256", "payload"} or row["schema"] != "atlas-paired-envelope-v1":
        raise ValueError("invalid study envelope")
    payload = decode_value(row["payload"])
    if semantic_digest(payload) != row["sha256"]:
        raise ValueError("study checksum mismatch")
    return payload


@contextmanager
def locked(root, name=".worker.lock"):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("study root must be a real directory")
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def definition(root):
    result = load(root / "definition.json")
    if result["identity"] != identity() or result["policy"] != POLICY:
        raise ValueError("study source/runtime/policy mismatch")
    validate_cohort(result["cohort"], RankPolicy())
    return result


def long_arms(results):
    published = results["candidate_results"]["opportunities"]
    radar = [
        o
        for o in published
        if len(o.get("checklist", ())) == 5 and all(i.get("passed") is True for i in o["checklist"])
    ][: POLICY["radar_fresh_limit"]]
    return {
        "engine": tuple(o["symbol"] for o in published if o["direction"] == "long"),
        "radar": tuple(o["symbol"] for o in radar if o["direction"] == "long"),
        "publications": published,
        "radar_exclusions": tuple(
            {
                "symbol": o["symbol"],
                "reason": "radar_limit"
                if len(o.get("checklist", ())) == 5
                and all(i.get("passed") is True for i in o["checklist"])
                else "initial_radar_checklist_not_5_of_5",
            }
            for o in published
            if o not in radar
        ),
        "decisions": tuple(compact_decision(e) for e in results["candidate_results"]["decisions"]),
    }


def compact_decision(event):
    """Keep decisive fields in the hot definition and bind full companion evidence."""
    heavy = {"inputs", "features", "candidate"} & event.keys()
    if not heavy:
        return event
    result = {key: value for key, value in event.items() if key not in heavy}
    metrics = ("close", "ema50_4h", "poc_30d", "val_30d", "rvol_4h", "vcr_4h",
               "oi_delta_7d", "oi_delta_4h", "oi_delta_1h", "taker_buy_ratio_4h",
               "taker_buy_ratio_1h", "funding_rate_8h", "ret_24h", "bb_bandwidth_4h")
    values = {**event.get("features", {}), **event.get("inputs", {})}
    result["decisive_values"] = {key: values[key] for key in metrics if key in values}
    result["full_event_sha256"] = semantic_digest(event)
    return result


async def prepare(bundle_path, root, *, clock=lambda: datetime.now(UTC), previous_study=None):
    if root.exists():
        raise ValueError("study destination already exists")
    with offline():
        bundle = read_bundle(bundle_path)
        if bundle.get("synthetic_capacity_only"):
            raise ValueError("synthetic capacity evidence cannot enroll a live study")
        if bundle["schema"] not in (POLICY["input_contract"], POLICY["discovery_input_contract"]):
            raise ValueError("new enrollment requires acquisition-timed v3/v4 evidence")
        if bundle["schema"] == POLICY["discovery_input_contract"] and bundle["record"].request.get("max_symbols") != 320:
            raise ValueError("discovery enrollment requires explicit 320-symbol capture budget")
        comparison = await compare_bundle(bundle)
    if comparison["status"] != "complete":
        raise ValueError("both verified comparison arms must complete")
    record = bundle["record"]
    validate_cohort(record.cohort, RankPolicy())
    cutoff = timestamp(clock())
    if not 0 <= (cutoff - timestamp(bundle["completed_at"])).total_seconds() <= 600:
        raise ValueError("capture too old for prospective issuance")
    quotes = {
        q.instrument_id.symbol: q
        for call in record.inputs
        if call.method == "quotes"
        for q in call.result
    }
    cohort_symbols = {i.id.symbol for i in record.cohort}
    # Fixed public momentum benchmark; no future gainers or outcomes enter selection.
    reference = sorted(
        (
            q
            for s, q in quotes.items()
            if s in cohort_symbols and q.price_change_pct_24h is not None
        ),
        key=lambda q: (-q.price_change_pct_24h, q.instrument_id.symbol),
    )[: POLICY["reference_size"]]
    frozen = {
        "schema": "atlas-paired-study-v1",
        "study_id": str(uuid4()),
        "scan_id": record.scan_id,
        "identity": identity(),
        "policy": dict(POLICY),
        "issued_at": cutoff,
        "capture_started_at": record.started_at,
        "evidence_completed_at": bundle["completed_at"],
        "bundle_sha256": comparison["bundle_sha256"],
        "comparison_sha256": semantic_digest(comparison),
        "input_contract": bundle["schema"],
        "capture_request": dict(record.request),
        "actual_control_counts": dict(record.counts),
        "cohort": record.cohort,
        "arms": {
            "control": long_arms(comparison["control"]),
            "funding_carry": long_arms(comparison["quote_funding_carry"]),
        },
        "momentum_reference": tuple(q.instrument_id.symbol for q in reference),
        "performance_claims_permitted": False,
    }
    if comparison.get("discovery_v2") is not None:
        result = comparison["discovery_v2"]
        arm = long_arms(result)
        observations = tuple({**row, "evidence": compact_decision(row["evidence"])}
                             for row in result["candidate_results"]["discovery_observations"])
        arm["source_policy"] = result["policy"]
        arm["feature_unavailable_symbols"] = result["candidate_results"]["feature_unavailable_symbols"]
        arm["discovery_observations"] = observations
        arm["research_views"] = {
            "watch_selection": result["candidate_results"]["watch_symbols"],
            "readiness": tuple(r["symbol"] for r in observations if r["state"] in (
                "readiness_activation_unverified", "ready_waiting_activation")),
            "emerging": tuple(r["symbol"] for r in observations
                              if r["temporal_features"].get("emerging") is True),
        }
        frozen["arms"]["discovery_v2"] = arm
        if previous_study is not None:
            previous = definition(previous_study)
            context, transitions = transition_context(previous, frozen)
            frozen["previous_study_context"] = context
            arm["research_views"].update(transitions)
    elif previous_study is not None:
        raise ValueError("transition context requires discovery-v2 evidence")
    for arm in frozen["arms"].values():
        if not set((*arm["engine"], *arm["radar"])) <= cohort_symbols:
            raise ValueError("candidate outside frozen cohort")
        if any(not set(symbols) <= cohort_symbols for symbols in arm.get("research_views", {}).values()):
            raise ValueError("research observation outside frozen cohort")
    root.mkdir(mode=0o700)
    save(root / "comparison.json", comparison)
    save(root / "definition.json", frozen)
    return {
        "status": "prepared_not_enrolled",
        "study_id": frozen["study_id"],
        "cohort_size": len(record.cohort),
        "issued_at": cutoff.isoformat(),
    }


def transition_context(previous, current):
    """Use only an already-issued definition, with no access to its future outcomes."""
    age = (current["capture_started_at"] - previous["issued_at"]).total_seconds()
    if not 0 < age <= POLICY["max_transition_context_age_seconds"]:
        raise ValueError("previous discovery decision outside transition window")
    prior_arm = previous["arms"].get("discovery_v2")
    arm = current["arms"]["discovery_v2"]
    if prior_arm is None or prior_arm["source_policy"] != arm["source_policy"]:
        raise ValueError("transition discovery policy mismatch")
    prior = {r["symbol"]: r for r in prior_arm["discovery_observations"]}
    transitions, activations = [], []
    for row in arm["discovery_observations"]:
        before = prior.get(row["symbol"], {})
        old = before.get("temporal_features", {})
        new = row["temporal_features"]
        if old.get("available") and new.get("available"):
            old_cutoff, new_cutoff = timestamp(old["last_closed_at"]), timestamp(new["last_closed_at"])
            if not old_cutoff <= previous["issued_at"] < current["capture_started_at"] or new_cutoff > current["capture_started_at"]:
                raise ValueError("transition features outside decision chronology")
            if old_cutoff < new_cutoff and old.get("emerging") is False and new.get("emerging") is True:
                transitions.append(row["symbol"])
        if before.get("state") in ("readiness_activation_unverified", "ready_waiting_activation") and row["state"] == "activated_gates_passed":
            activations.append(row["symbol"])
    return {"study_id": previous["study_id"], "definition_sha256": semantic_digest(previous),
            "issued_at": previous["issued_at"]}, {
        "relative_strength_transition": tuple(sorted(transitions)),
        "readiness_to_activation": tuple(sorted(activations)),
    }


def enroll(root, *, now):
    with locked(root):
        d = definition(root)
        now = timestamp(now)
        if (
            not 0
            <= (now - d["issued_at"]).total_seconds()
            <= POLICY["max_enrollment_delay_seconds"]
        ):
            raise ValueError("prepared decision is outside enrollment window")
        if (root / "enrollment.json").exists():
            return load(root / "enrollment.json")
        # Future fixed boundary: first-snapshot failure never moves this anchor.
        epoch = (int(now.timestamp()) // 300 + 1) * 300
        anchor = datetime.fromtimestamp(epoch, UTC)
        record = {
            "study_id": d["study_id"],
            "definition_sha256": semantic_digest(d),
            "enrolled_at": now,
            "anchor": anchor,
            "endpoint_24h": anchor + timedelta(hours=24),
            "endpoint_48h": anchor + timedelta(hours=48),
        }
        save(root / "enrollment.json", record)
        write_progress(root, record, latest=None, now=now)
        return record


def write_progress(root, enrollment, *, latest, now):
    """Replace a small health record atomically, independently of immutable snapshots."""
    path = root / "progress.json"
    if path.is_symlink():
        raise ValueError("progress symlink refused")
    payload = {
        "schema": "atlas-paired-progress-v1",
        "study_id": enrollment["study_id"],
        "definition_sha256": enrollment["definition_sha256"],
        "anchor": enrollment["anchor"],
        "endpoint_48h": enrollment["endpoint_48h"],
        "updated_at": now,
        "latest": {
            key: latest.get(key)
            for key in ("slot", "status", "requested_at", "arrived_at", "cooldown_until")
        } if latest is not None else None,
    }
    envelope = {"schema": "atlas-paired-envelope-v1", "sha256": semantic_digest(payload),
                "payload": encode_value(payload)}
    with tempfile.NamedTemporaryFile(dir=root, prefix=".progress-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(envelope, allow_nan=False).encode())
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def health(root, *, now):
    """Health reads bounded metadata, never the definition or raw market snapshots."""
    path = root / "progress.json"
    if path.is_symlink() or path.stat().st_size > 16 * 1024:
        raise ValueError("invalid progress record")
    progress = load(path)
    enrollment = load(root / "enrollment.json")
    for key in ("study_id", "definition_sha256", "anchor", "endpoint_48h"):
        if progress[key] != enrollment[key]:
            raise ValueError("progress enrollment mismatch")
    latest = progress["latest"]
    state = "awaiting_first_snapshot"
    if now < progress["updated_at"]:
        state = "degraded"
    elif latest is None:
        if now > enrollment["anchor"] + timedelta(seconds=45):
            state = "invalid_anchor"
    elif latest["slot"] == 0 and latest["status"] != "observed":
        state = "invalid_anchor"
    elif now > enrollment["endpoint_48h"] + timedelta(seconds=45):
        state = "window_ended"
    elif latest["status"] != "observed" or (
        now - (latest["arrived_at"] or latest["requested_at"])
    ).total_seconds() > 600:
        state = "degraded"
    else:
        state = "collecting"
    return {"status": state, "study_id": progress["study_id"], "latest": latest,
            "health_source": "bounded_progress_record"}


def session(root):
    d, e = definition(root), load(root / "enrollment.json")
    if e["study_id"] != d["study_id"] or e["definition_sha256"] != semantic_digest(d):
        raise ValueError("enrollment definition mismatch")
    if not d["issued_at"] <= e["enrolled_at"] < e["anchor"]:
        raise ValueError("invalid issuance/anchor chronology")
    return d, e


def storage_ok(root):
    total = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in (*folders, *files):
            p = Path(directory) / name
            if p.is_symlink():
                raise ValueError("study symlink refused")
            if p.is_file():
                total += p.stat().st_size
    if total > POLICY["max_study_bytes"] or shutil.disk_usage(root).free < POLICY["min_free_bytes"]:
        raise ValueError("study storage budget unavailable")


async def tick(root, *, enabled, clock=lambda: datetime.now(UTC), transport=None,
               validated_session=None):
    if not enabled:
        return {"status": "disabled", "http_attempts": 0}
    with locked(root):
        d, e = validated_session if validated_session is not None else session(root)
        now = timestamp(clock())
        seconds = (now - e["anchor"]).total_seconds()
        if seconds < 0:
            return {"status": "waiting_for_anchor", "http_attempts": 0}
        slot = int(seconds // 300)
        if slot > 576:
            return {"status": "window_ended", "http_attempts": 0}
        scheduled = e["anchor"] + timedelta(seconds=slot * 300)
        path = root / "snapshots" / f"{slot:06d}.json"
        if path.exists():
            # Repair a crash between immutable snapshot publication and progress update.
            progress = load(root / "progress.json") if (root / "progress.json").exists() else None
            if progress is None or progress["latest"] is None or progress["latest"]["slot"] < slot:
                write_progress(root, e, latest=load(path), now=now)
            return {"status": "already_recorded", "slot": slot, "http_attempts": 0}
        previous = sorted((root / "snapshots").glob("*.json"))
        if previous:
            last = load(previous[-1])
            if last.get("cooldown_until") and now < last["cooldown_until"]:
                return {"status": "exchange_cooldown", "http_attempts": 0}
        if slot and not (root / "snapshots/000000.json").exists():
            return {"status": "anchor_missing_study_invalid", "http_attempts": 0}
        if slot and load(root / "snapshots/000000.json")["status"] != "observed":
            return {"status": "anchor_failed_study_invalid", "http_attempts": 0}
        storage_ok(root)
        result = {
            "study_id": d["study_id"],
            "definition_sha256": e["definition_sha256"],
            "slot": slot,
            "scheduled_at": scheduled,
            "requested_at": now,
            "arrived_at": None,
            "status": "missed",
            "reason": "slot_late",
            "rows": (),
            "normalized": {},
            "http_attempts": 0,
        }
        if (now - scheduled).total_seconds() <= POLICY["lateness_seconds"]:
            budget = EvidenceTransport(
                transport or httpx.AsyncHTTPTransport(),
                attempts=1,
                seconds=15,
                byte_limit=8 * 1024**2,
            )
            try:
                async with asyncio.timeout(15):
                    async with httpx.AsyncClient(
                        transport=budget, timeout=10, trust_env=False, follow_redirects=False
                    ) as client:
                        response = await client.get("https://fapi.binance.com/fapi/v1/ticker/24hr")
                        response.raise_for_status()
                        rows = tuple(response.json())
                arrived = timestamp(clock())
                if not now <= arrived <= scheduled + timedelta(seconds=45):
                    raise ValueError("snapshot clock/lateness violation")
                normalized = normalize_ranks(d["cohort"], rows, arrived, RankPolicy())
                result.update(
                    status="observed",
                    reason=None,
                    rows=rows,
                    normalized=normalized,
                    arrived_at=arrived,
                )
                if slot == 0 and not (normalized.get("complete") or normalized.get("bounded")):
                    result.update(status="failed", reason="anchor_has_no_certified_rank_bounds")
            except Exception as exc:
                result.update(status="failed", reason=type(exc).__name__)
            finally:
                result["http_attempts"] = budget.attempts
                result["receipts"] = tuple(budget.receipts)
                if budget.stopped in ("exchange_cooldown", "exchange_weight_ceiling"):
                    row = budget.receipts[-1]
                    default = 3600 if row.get("status") == 418 else 60
                    try:
                        retry = float(row.get("retry_after") or default)
                        if not 0 <= retry <= 10**10:
                            raise ValueError("invalid retry")
                        if retry > 10**9:
                            retry = max(0, retry - now.timestamp())
                    except (ValueError, TypeError):
                        retry = default
                    result["cooldown_until"] = now + timedelta(seconds=max(default, retry))
                await budget.aclose()
        save(path, result)
        write_progress(root, e, latest=result, now=timestamp(clock()))
        return {
            "status": result["status"],
            "slot": slot,
            "http_attempts": result["http_attempts"],
            "anchor": e["anchor"].isoformat(),
            "observation_started": slot == 0 and result["status"] == "observed",
        }


def status(root, *, now):
    d = definition(root)
    if not (root / "enrollment.json").exists():
        return {"status": "prepared_not_enrolled", "study_id": d["study_id"]}
    _, e = session(root)
    files = sorted((root / "snapshots").glob("*.json"))
    baseline = load(files[0]) if files and files[0].name == "000000.json" else None
    live = baseline is not None and baseline["status"] == "observed"
    state = (
        "awaiting_first_snapshot"
        if now <= e["anchor"] + timedelta(seconds=45)
        else "invalid_anchor"
    )
    if live:
        latest = load(files[-1])
        state = "window_ended" if now > e["endpoint_48h"] + timedelta(seconds=45) else "collecting"
        if state == "collecting" and latest["status"] != "observed":
            state = "degraded"
        if (
            state == "collecting"
            and (now - (latest["arrived_at"] or latest["requested_at"])).total_seconds() > 600
        ):
            state = "degraded"
    return {
        "status": state,
        "study_id": d["study_id"],
        "enrollment": e,
        "observation_started": live,
        "recorded_slots": len(files),
        "recorded_slots_definition": "immutable_snapshot_files_including_failure_records",
        "endpoint_24h_status": load(root / "snapshots/000288.json")["status"]
        if (root / "snapshots/000288.json").exists()
        else "missing",
        "endpoint_48h_status": load(root / "snapshots/000576.json")["status"]
        if (root / "snapshots/000576.json").exists()
        else "missing",
    }


def report(root, horizon, *, now):
    d, e = session(root)
    endpoint = e[f"endpoint_{horizon}h"]
    if now < endpoint:
        raise ValueError("outcome horizon not reached")
    expected = horizon * 12 + 1
    paths = {int(p.stem): p for p in (root / "snapshots").glob("*.json") if int(p.stem) < expected}
    if 0 not in paths or load(paths[0])["status"] != "observed":
        raise ValueError("valid anchor observation required")
    symbols = {i.id.symbol for i in d["cohort"]}
    states = {
        s: {
            "symbol": s,
            "known_samples": 0,
            "unknown_samples": 0,
            "initial_inside": None,
            "first_observed_top10_at": None,
            "anchor_price": None,
            "endpoint_price": None,
            "sampled_max_return_pct": None,
            "sampled_min_return_pct": None,
        }
        for s in symbols
    }
    coverage = Counter()
    for slot in range(expected):
        sample = load(paths[slot]) if slot in paths else None
        coverage[sample["status"] if sample is not None else "absent"] += 1
        members = {}
        if sample is not None:
            if sample["status"] not in ("observed", "missed", "failed"):
                raise ValueError("unknown snapshot status")
            if sample["study_id"] != d["study_id"] or sample["definition_sha256"] != e["definition_sha256"] or sample["slot"] != slot:
                raise ValueError("cross-study snapshot")
            if sample["status"] == "observed":
                normalized = normalize_ranks(
                    d["cohort"], sample["rows"], sample["arrived_at"], RankPolicy()
                )
                if semantic_digest(normalized) != semantic_digest(sample["normalized"]):
                    raise ValueError("snapshot normalization mismatch")
                members = {m["symbol"]: m for m in normalized["members"]}
        for symbol, state in states.items():
            member = members.get(symbol, {})
            inside = member.get("inside")
            if inside is None:
                state["unknown_samples"] += 1
            else:
                state["known_samples"] += 1
            if slot == 0:
                state["initial_inside"] = inside
                state["anchor_price"] = member.get("price")
                state["anchor_price_at"] = member.get("cutoff")
            if inside is True and state["first_observed_top10_at"] is None:
                state["first_observed_top10_at"] = sample["arrived_at"]
            price, initial = member.get("price"), state["anchor_price"]
            if price is not None and initial is not None and initial > 0:
                change = (price / initial - 1) * 100
                state["sampled_max_return_pct"] = max(
                    change,
                    state["sampled_max_return_pct"]
                    if state["sampled_max_return_pct"] is not None
                    else change,
                )
                state["sampled_min_return_pct"] = min(
                    change,
                    state["sampled_min_return_pct"]
                    if state["sampled_min_return_pct"] is not None
                    else change,
                )
                if slot == expected - 1:
                    state["endpoint_price"] = price
                    state["endpoint_price_at"] = member.get("cutoff")
                    state["endpoint_return_pct"] = change
    for row in states.values():
        observed = row["first_observed_top10_at"]
        row["time_to_first_observed_top10_minutes"] = (
            (observed - d["issued_at"]).total_seconds() / 60
            if observed is not None and row["initial_inside"] is False
            else None
        )
    arms = {
        f"{name}_{view}": arm[view]
        for name, arm in d["arms"].items()
        for view in ("engine", "radar")
    }
    for name, arm in d["arms"].items():
        for view, candidates in arm.get("research_views", {}).items():
            arms[f"{name}_{view}"] = candidates
    arms["momentum_reference"] = d["momentum_reference"]
    summaries = {}
    for name, candidates in arms.items():
        rows = [states[s] for s in candidates]
        summaries[name] = {
            "candidate_count": len(rows),
            "already_top10_at_anchor": tuple(
                r["symbol"] for r in rows if r["initial_inside"] is True
            ),
            "missed_new_observed_gainers": tuple(
                sorted(
                    s
                    for s, r in states.items()
                    if r["initial_inside"] is False
                    and r["first_observed_top10_at"] is not None
                    and s not in candidates
                )
            ),
            "observed_top10_count": sum(r["first_observed_top10_at"] is not None for r in rows),
            "observed_early_detection_count": sum(
                r["initial_inside"] is False and r["first_observed_top10_at"] is not None
                for r in rows
            ),
            "eligible_for_observed_early_detection_count": sum(
                r["initial_inside"] is False for r in rows
            ),
            "initial_rank_unknown_count": sum(r["initial_inside"] is None for r in rows),
            "endpoint_unknown_count": sum("endpoint_return_pct" not in r for r in rows),
            "endpoint_known_count": sum("endpoint_return_pct" in r for r in rows),
            "nonpositive_endpoint_count": sum(
                r.get("endpoint_return_pct", Decimal("1")) <= 0 for r in rows
            ),
            "missed_observed_gainers": tuple(
                sorted(
                    s
                    for s, r in states.items()
                    if r["first_observed_top10_at"] is not None and s not in candidates
                )
            ),
        }
    return {
        "schema": "atlas-paired-report-v2",
        "study_id": d["study_id"],
        "definition_sha256": semantic_digest(d),
        "horizon_hours": horizon,
        "collection_status": "degraded"
        if coverage["observed"] != expected
        else "bounded"
        if any(r["unknown_samples"] for r in states.values())
        else "complete",
        "selection_and_rejection_diagnostics": {
            name: arm["decisions"] for name, arm in d["arms"].items()
        },
        "research_observation_definitions": {
            "watch_selection": "evaluation_coverage_reference",
            "readiness": "structural_watch_observation_activation_unconfirmed",
            "emerging": "exploratory_relative_strength_and_turnover_observation",
            "relative_strength_transition": "emerging_false_to_true_between_preissued_definitions",
            "readiness_to_activation": "existing_gates_activation_after_prior_structural_readiness",
        },
        "feature_unavailable_symbols": {
            name: arm.get("feature_unavailable_symbols", {}) for name, arm in d["arms"].items()
        },
        "as_of": now,
        "expected_slots": expected,
        "recorded_slots": len(paths),
        "missing_slots": expected - len(paths),
        "coverage_semantics": POLICY["coverage_semantics"],
        "observed_slots": coverage["observed"],
        "missed_slots": coverage["missed"],
        "failed_slots": coverage["failed"],
        "absent_slots": coverage["absent"],
        "unobserved_slots": expected - coverage["observed"],
        "arms": summaries,
        "symbols": tuple(states[s] for s in sorted(states)),
        "performance_claims_permitted": False,
        "limitations": (
            "sampled observations; unobserved intrabar events are unknown",
            "fixed eligible cohort, not all Binance spot/futures listings",
            "first observed rank is not exact first entry; missing data is not a failed forecast",
        ),
    }


async def main_async(args):
    root = args.study
    if args.action == "prepare":
        if args.bundle is None:
            raise ValueError("prepare requires bundle")
        return await prepare(args.bundle, root, previous_study=getattr(args, "previous_study", None))
    if args.action == "enroll":
        if not args.confirm_enrollment:
            raise ValueError("explicit enrollment confirmation required")
        return enroll(root, now=datetime.now(UTC))
    if args.action == "health":
        return health(root, now=datetime.now(UTC))
    if args.action == "status":
        return status(root, now=datetime.now(UTC))
    if args.action == "report":
        finalize_report(root, args.horizon, now=datetime.now(UTC))
        return {"status": "report_saved", "horizon": args.horizon}
    enabled = args.execute_public_collection and os.environ.get("ATLAS_RESEARCH_ENABLED") == "true"
    if not enabled:
        return {"status": "disabled", "http_attempts": 0}
    if args.action == "tick":
        return await tick(root, enabled=True)
    if args.action == "reports":
        return await run_reports(root)
    signature = session_signature(root)
    validated = session(root)
    if not (root / "progress.json").exists():
        paths = sorted((root / "snapshots").glob("*.json"))
        write_progress(root, validated[1], latest=load(paths[-1]) if paths else None,
                       now=datetime.now(UTC))
    while True:
        if session_signature(root) != signature:
            raise ValueError("study definition/enrollment changed during collection")
        result = await tick(root, enabled=True, validated_session=validated)
        print(encode_value(result), flush=True)
        if result["status"] in (
            "window_ended",
            "anchor_missing_study_invalid",
            "anchor_failed_study_invalid",
        ):
            return result
        # Single bounded job, wakes every 5s; never schedules new candidate scans/cohorts.
        await asyncio.sleep(5)


def session_signature(root):
    return tuple((p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                 for p in (root / "definition.json", root / "enrollment.json"))


def finalize_report(root, horizon, *, now):
    """Reporting uses a dedicated lock and never owns the acquisition lock."""
    with locked(root, ".report.lock"):
        path = root / f"report-{horizon}h.json"
        if path.exists():
            result = load(path)
            _, enrollment = session(root)
            if result["definition_sha256"] != enrollment["definition_sha256"] or result["horizon_hours"] != horizon:
                raise ValueError("existing report belongs to another study/horizon")
            return result
        result = report(root, horizon, now=now)
        save(path, result)
        return result


async def run_reports(root):
    _, enrollment = session(root)
    completed = set()
    while len(completed) < 2:
        now = datetime.now(UTC)
        for horizon in (24, 48):
            if horizon not in completed and now >= enrollment[f"endpoint_{horizon}h"] + timedelta(seconds=45):
                await asyncio.to_thread(finalize_report, root, horizon, now=now)
                completed.add(horizon)
        if len(completed) < 2:
            await asyncio.sleep(5)
    return {"status": "reports_complete", "horizons": (24, 48)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare", "enroll", "tick", "run", "reports", "status", "health", "report")
    )
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--previous-study", type=Path)
    parser.add_argument("--horizon", type=int, choices=(24, 48), default=48)
    parser.add_argument("--confirm-enrollment", action="store_true")
    parser.add_argument("--execute-public-collection", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(main_async(args))
        print(encode_value(result))
        if args.action == "health" and result["status"] not in (
            "awaiting_first_snapshot",
            "collecting",
            "window_ended",
        ):
            return 3
        return 0
    except (OSError, ValueError, KeyError, TypeError, ReplayUnavailable) as exc:
        print(
            {
                "status": "unavailable",
                "reason": str(exc)
                if isinstance(exc, (ValueError, ReplayUnavailable))
                else type(exc).__name__,
            }
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
