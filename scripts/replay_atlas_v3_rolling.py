"""Deterministic offline replay of the modified Atlas v3.0 screener engine over recorded Study 3 rolling frames."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algotrader.application.rolling_discovery import SnapshotTape, engine  # noqa: E402
from algotrader.application.scan_replay import ReplayMarket  # noqa: E402
from algotrader.config import Settings  # noqa: E402
from algotrader.domain.models import Direction  # noqa: E402
from algotrader.domain.scan_observations import ScanDecisionJournal  # noqa: E402
from algotrader.infrastructure.rolling_discovery import unpack  # noqa: E402

DEFAULT_CONFIG = ROOT / "docs" / "security" / "atlas_v3_replay_config.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_price_grid(
    price_grid_path: Path, report_48h_path: Path
) -> tuple[dict[str, list[tuple[float, float]]], set[str], dict[str, float], dict[str, Any]]:
    from scripts.research_prospective import load

    r48 = load(report_48h_path)

    known_initial_outside = {r["symbol"] for r in r48.get("symbols", []) if r.get("initial_inside") is False}
    initial_top10: set[str] = set()
    first_top10_epoch: dict[str, float] = {}
    first_observed_inside: dict[str, float] = {}
    for sym_rec in r48.get("symbols", []):
        sym = sym_rec["symbol"]
        if sym_rec.get("first_observed_top10_at") is not None:
            first_observed_inside[sym] = sym_rec["first_observed_top10_at"].timestamp()
        if sym_rec.get("initial_inside") is True:
            initial_top10.add(sym)
        elif sym_rec.get("initial_inside") is False and sym_rec.get("first_observed_top10_at") is not None:
            ts = str(sym_rec["first_observed_top10_at"]).replace("Z", "+00:00")
            first_top10_epoch[sym] = datetime.fromisoformat(ts).timestamp()

    with price_grid_path.open("r", encoding="utf-8") as fh:
        grid = json.load(fh)

    traj: dict[str, list[tuple[float, float]]] = {}
    snaps = grid.get("snapshots", [])
    for snap in snaps:
        ts_str = snap.get("arrived_at")
        if not ts_str:
            continue
        t_ep = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).timestamp()
        for sym, price in snap.get("prices", {}).items():
            value = float(price)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("price grid contains invalid price")
            traj.setdefault(sym, []).append((t_ep, value))
        for sym in snap.get("top10", []):
            first_observed_inside[sym] = min(t_ep, first_observed_inside.get(sym, t_ep))
            if sym in known_initial_outside and (sym not in first_top10_epoch or t_ep < first_top10_epoch[sym]):
                first_top10_epoch[sym] = t_ep

    if any(any(b[0] <= a[0] for a, b in zip(points, points[1:], strict=False)) for points in traj.values()):
        raise ValueError("price grid times must be strictly increasing")
    meta = {
        "study_id": r48["study_id"],
        "first_observed_inside_epoch": first_observed_inside,
        "provenance": grid.get("provenance"),
        "total_snapshots": grid.get("total_snapshots"),
        "sampled_snapshots": len(snaps),
        "first_snapshot_at": snaps[0]["arrived_at"] if snaps else None,
        "latest_snapshot_at": snaps[-1]["arrived_at"] if snaps else None,
    }
    return traj, initial_top10, first_top10_epoch, meta


def compute_forward_metrics(
    sym: str,
    t0_ep: float,
    signal_price: float,
    traj: dict[str, list[tuple[float, float]]],
    initial_top10: set[str],
    first_top10_epoch: dict[str, float],
    first_observed_inside_epoch: dict[str, float] | None = None,
) -> dict[str, Any]:
    pts = traj.get(sym, [])
    if not pts or signal_price <= 0:
        return {"available": False}

    # Entry is the contemporaneous source quote supplied by the replay frame.
    # Hourly engine closes and future nearest snapshots are not entry quotes.
    p0 = signal_price
    future = [(t, p) for t, p in pts if t >= t0_ep]
    if not future or not math.isfinite(p0):
        return {"available": False}
    t_end, p_end = future[-1]
    result: dict[str, Any] = {
        "available": True,
        "entry_quote_price": p0,
        "entry_basis": "recorded_frame_source_quote_at_issuance",
        "elapsed_hours_to_latest": round((t_end - t0_ep) / 3600, 6),
        "ret_latest_pct": (p_end / p0 - 1) * 100,
        "mfe_all_pct": (max([p0, *(p for _, p in future)]) / p0 - 1) * 100,
        "initial_top10": sym in initial_top10,
        "became_new_top10": sym in first_top10_epoch,
    }
    for hours in (24, 48, 72):
        target = t0_ep + hours * 3600
        endpoint = next(((t, p) for t, p in future if target <= t <= target + 345), None)
        status = "censored" if target > t_end else "known" if endpoint else "unknown"
        result[f"outcome_{hours}h_status"] = status
        result[f"endpoint_{hours}h_offset_seconds"] = endpoint[0] - target if endpoint else None
        result[f"ret_{hours}h_pct"] = (endpoint[1] / p0 - 1) * 100 if status == "known" else None
        path = [p0, *(p for t, p in future if t <= target)]
        # Incomplete horizons cannot silently contribute to full-horizon summaries.
        result[f"mfe_{hours}h_pct"] = (max(path) / p0 - 1) * 100 if status == "known" else None
        result[f"mae_{hours}h_pct"] = (min(path) / p0 - 1) * 100 if status == "known" else None
    lead_h = (first_top10_epoch[sym] - t0_ep) / 3600 if sym in first_top10_epoch else None
    result["top10_lead_hours"] = lead_h
    result["early_before_top10"] = lead_h is not None and 0 < lead_h <= 48
    prior_inside = (first_observed_inside_epoch or first_top10_epoch).get(sym)
    result["already_top10_at_signal"] = sym in initial_top10 or (prior_inside is not None and prior_inside <= t0_ep)
    return result


def summarize_signals(signals: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(signals)
    if n == 0:
        return {"count": 0}
    r24 = [s["outcomes"]["ret_24h_pct"] for s in signals if s["outcomes"].get("ret_24h_pct") is not None]
    r48 = [s["outcomes"]["ret_48h_pct"] for s in signals if s["outcomes"].get("ret_48h_pct") is not None]
    rlat = [s["outcomes"]["ret_latest_pct"] for s in signals if s["outcomes"].get("ret_latest_pct") is not None]
    mfe48 = [s["outcomes"]["mfe_48h_pct"] for s in signals if s["outcomes"].get("mfe_48h_pct") is not None]
    mae48 = [s["outcomes"]["mae_48h_pct"] for s in signals if s["outcomes"].get("mae_48h_pct") is not None]
    early_t10 = [s["symbol"] for s in signals if s["outcomes"].get("early_before_top10")]

    def rate(vals: list[float], thresh: float = 0.0, strict: bool = True) -> dict[str, Any]:
        if not vals:
            return {"hits": 0, "total": 0, "pct": None}
        hits = sum(1 for v in vals if (v > thresh if strict else v >= thresh))
        return {"hits": hits, "total": len(vals), "pct": round(hits / len(vals) * 100.0, 1)}

    def avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "count": n,
        "outcome_24h_unknown_or_censored": n - len(r24),
        "outcome_48h_unknown_or_censored": n - len(r48),
        "rate_semantics": "positive quote returns, not executed trade win rates",
        "symbols": [s["symbol"] for s in signals],
        "win_rate_24h": rate(r24, 0.0, strict=True),
        "mean_ret_24h_pct": avg(r24),
        "win_rate_48h": rate(r48, 0.0, strict=True),
        "mean_ret_48h_pct": avg(r48),
        "win_rate_latest": rate(rlat, 0.0, strict=True),
        "mean_ret_latest_pct": avg(rlat),
        "hit_mfe_48h_ge_3pct": rate(mfe48, 3.0, strict=False),
        "hit_mfe_48h_ge_5pct": rate(mfe48, 5.0, strict=False),
        "mean_mfe_48h_pct": avg(mfe48),
        "mean_mae_48h_pct": avg(mae48),
        "worst_mae_48h_pct": round(min(mae48), 2) if mae48 else None,
        "early_top10_within_initial_study_48h": sum(
            bool(s["outcomes"].get("early_top10_within_initial_study_48h")) for s in signals
        ),
        "early_top10_count": len(early_t10),
        "early_top10_symbols": early_t10,
    }


def compact_opportunity(opp: dict[str, Any]) -> dict[str, Any]:
    chk = opp.get("checklist_verification")
    chk_count = chk.get("passed_count") if isinstance(chk, dict) else None
    return {
        "symbol": opp.get("symbol") or opp.get("instrument", {}).get("id", {}).get("symbol"),
        "setup_label": opp.get("setup_type") or opp.get("setup_label"),
        "execution_mode": opp.get("execution_mode"),
        "breakout_readiness_status": opp.get("breakout_readiness_status"),
        "score": float(opp["score"]) if opp.get("score") is not None else None,
        "conviction": float(opp["conviction"]) if opp.get("conviction") is not None else None,
        "grade": opp.get("grade"),
        "risk_reward_ratio": float(opp["risk_reward_ratio"]) if opp.get("risk_reward_ratio") is not None else None,
        "expected_value_r": float(opp["expected_value_r"]) if opp.get("expected_value_r") is not None else None,
        "checklist_passed": chk_count,
        "portal_fresh_eligible": len(opp.get("checklist", ())) == 5
        and all(item.get("passed") is True for item in opp.get("checklist", ())),
        "current_price": float(opp["current_price"]) if opp.get("current_price") is not None else None,
        "suggested_entry": float(opp["suggested_entry"]) if opp.get("suggested_entry") is not None else None,
        "suggested_stop": float(opp["suggested_stop"]) if opp.get("suggested_stop") is not None else None,
        "suggested_targets": [float(t) for t in opp.get("suggested_targets", ())],
        "oi_change_7d": float(opp["oi_change_7d"]) if opp.get("oi_change_7d") is not None else None,
        "oi_change_1h": float(opp["oi_change_1h"]) if opp.get("oi_change_1h") is not None else None,
        "funding_rate": float(opp["funding_rate"]) if opp.get("funding_rate") is not None else None,
        "taker_ratio_4h": float(opp["taker_ratio_4h"]) if opp.get("taker_ratio_4h") is not None else None,
        "taker_ratio_1h": float(opp["taker_ratio_1h"]) if opp.get("taker_ratio_1h") is not None else None,
    }


def _rel_config_path(config_path: Path) -> str:
    return str(config_path.resolve().relative_to(ROOT))


async def run_replay(config_path: Path) -> dict[str, Any]:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    with config_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    db_path = ROOT / cfg["inputs"]["episodes_sqlite_local"]
    price_grid_path = ROOT / cfg["inputs"]["price_grid_local"]
    report_48h_path = ROOT / cfg["inputs"]["report_48h_local"]
    output_path = ROOT / cfg["output_json"]

    traj, initial_top10, first_top10_epoch, grid_meta = load_price_grid(price_grid_path, report_48h_path)

    if grid_meta["study_id"] != cfg["study_id"]:
        raise ValueError("report study mismatch")
    provenance = grid_meta.get("provenance")
    if provenance and provenance.get("study_id") != cfg["study_id"]:
        raise ValueError("price grid study mismatch")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT decision_id, issued_at, payload, sha256 FROM discovery_frames ORDER BY issued_at ASC, decision_id ASC"
    ).fetchall()
    conn.close()

    settings_cfg = {
        "excluded_base_assets": tuple(cfg["screener_settings"]["excluded_base_assets"]),
        "support_resistance_lookback_bars": int(cfg["screener_settings"]["support_resistance_lookback_bars"]),
    }

    baseline_challenger_first: dict[str, dict[str, Any]] = {}
    baseline_control_first: dict[str, dict[str, Any]] = {}
    v3_challenger_first: dict[str, dict[str, Any]] = {}
    v3_control_first: dict[str, dict[str, Any]] = {}
    v3_prepub_rejected: dict[str, dict[str, Any]] = {}
    v3_missing_deriv_on_tape: dict[str, dict[str, Any]] = {}
    v3_all_frame_emissions: list[dict[str, Any]] = []

    frames_with_pool = 0
    total_pool_evaluations = 0

    for decision_id, _issued_str, payload_blob, sha in rows:
        frame = unpack(payload_blob, sha)
        if frame["study_id"] != cfg["study_id"] or frame["decision_id"] != decision_id:
            raise ValueError("discovery frame identity mismatch")
        if frame["evaluation_at"] > frame["issued_at"]:
            raise ValueError("frame evaluation must precede issuance")
        pool = tuple(frame["pool"])
        if not pool:
            continue
        frames_with_pool += 1
        total_pool_evaluations += len(pool)
        pool_set = set(pool)
        eval_at: datetime = frame["evaluation_at"]
        issued_at: datetime = frame["issued_at"]
        t0_ep = issued_at.timestamp()
        entry_quotes = {q.instrument_id.symbol: float(q.last) for q in frame.get("source_quotes", ())}

        # 1. Recorded baseline (v2.1) opportunities from the frame
        for arm_key, target_map in (
            ("challenger", baseline_challenger_first),
            ("control", baseline_control_first),
        ):
            for opp in frame[arm_key]["opportunities"]:
                c = compact_opportunity(opp)
                sym = c["symbol"]
                if sym and sym not in target_map:
                    target_map[sym] = {
                        "symbol": sym,
                        "decision_id": decision_id,
                        "evaluation_at": eval_at.isoformat(),
                        "issued_at": issued_at.isoformat(),
                        "opportunity": c,
                        "outcomes": compute_forward_metrics(
                            sym, t0_ep, entry_quotes.get(sym, 0.0), traj, initial_top10, first_top10_epoch,
                            grid_meta["first_observed_inside_epoch"]
                        ),
                    }

        # Compute live 24h Top-10 gainer set strictly from the frame's point-in-time source_quotes
        source_quotes = frame.get("source_quotes", ())
        ranked_sq = sorted(
            (
                (q.price_change_pct_24h, q.instrument_id.symbol)
                for q in source_quotes
                if q.price_change_pct_24h is not None and q.quote_volume_24h >= 500000
            ),
            reverse=True,
        )
        live_top10_at_frame = frozenset(sym for chg, sym in ranked_sq[:10] if chg >= 10.0)

        # 2. Live code execution of modified Atlas v3.0 engine on the exact recorded ReplayMarket tape
        for recovery, target_map in ((True, v3_challenger_first), (False, v3_control_first)):
            market = ReplayMarket(SnapshotTape(frame["inputs"]))
            journal = ScanDecisionJournal()
            screener = engine(
                Settings.model_construct(**settings_cfg),
                market,
                eval_at,
                recovery,
                decision_at=issued_at,
                journal=journal,
            )
            result = await screener.screen(
                market,
                market,
                max_symbols=int(cfg["screen_request"]["max_symbols"]),
                target_direction=Direction.LONG,
                enforce_quality=bool(cfg["screen_request"]["enforce_quality"]),
                top_gainer_symbols=live_top10_at_frame,
            )
            missing = screener._fetch_failures | screener._feature_unavailable
            for o in result:
                sym = o.instrument.id.symbol
                if sym in pool_set and sym not in missing and not frame.get("global_input_unavailable"):
                    c = compact_opportunity(o.as_dict())
                    if recovery:
                        v3_all_frame_emissions.append(
                            {
                                "symbol": sym,
                                "decision_id": decision_id,
                                "evaluation_at": eval_at.isoformat(),
                                "setup_label": c["setup_label"],
                                "score": c["score"],
                                "risk_reward_ratio": c["risk_reward_ratio"],
                            }
                        )
                    if sym not in target_map:
                        target_map[sym] = {
                            "symbol": sym,
                            "decision_id": decision_id,
                            "evaluation_at": eval_at.isoformat(),
                            "issued_at": issued_at.isoformat(),
                            "opportunity": c,
                            "outcomes": compute_forward_metrics(
                                sym, t0_ep, entry_quotes.get(sym, 0.0), traj, initial_top10, first_top10_epoch,
                                grid_meta["first_observed_inside_epoch"]
                            ),
                        }

            if recovery:
                decs = journal.snapshot()
                for d in decs:
                    if d.get("stage") == "pre_publication" and "candidate" in d:
                        sym = d.get("symbol")
                        if sym in pool_set and sym not in v3_prepub_rejected:
                            rejs = sorted(
                                {
                                    str(x.get("reason"))
                                    for x in decs
                                    if x.get("symbol") == sym and x.get("stage") == "publication"
                                }
                            )
                            if rejs:
                                c = compact_opportunity(d["candidate"])
                                v3_prepub_rejected[sym] = {
                                    "symbol": sym,
                                    "decision_id": decision_id,
                                    "evaluation_at": eval_at.isoformat(),
                                    "rejection_reasons": rejs,
                                    "opportunity": c,
                                    "outcomes": compute_forward_metrics(
                                        sym, t0_ep, entry_quotes.get(sym, 0.0), traj, initial_top10, first_top10_epoch,
                            grid_meta["first_observed_inside_epoch"]
                                    ),
                                }
                for sym in screener._fetch_failures:
                    if sym in pool_set and sym not in v3_missing_deriv_on_tape:
                        st1_pass = [
                            d
                            for d in decs
                            if d.get("symbol") == sym
                            and d.get("stage") == "accumulation_gates"
                            and d.get("reason") == "passed"
                        ]
                        if st1_pass:
                            v3_missing_deriv_on_tape[sym] = {
                                "symbol": sym,
                                "decision_id": decision_id,
                                "evaluation_at": eval_at.isoformat(),
                                "archetype": st1_pass[0].get("archetype"),
                            }

    initial_study_endpoint = datetime.fromisoformat(cfg["study_anchor_utc"]).timestamp() + 48 * 3600
    for records in (baseline_challenger_first, baseline_control_first, v3_challenger_first,
                    v3_control_first, v3_prepub_rejected):
        for record in records.values():
            metrics = record["outcomes"]
            first_inside = first_top10_epoch.get(record["symbol"])
            metrics["early_top10_within_initial_study_48h"] = bool(
                metrics.get("early_before_top10") and first_inside is not None
                and first_inside <= initial_study_endpoint
            )

    baseline_ch_list = sorted(baseline_challenger_first.values(), key=lambda x: x["evaluation_at"])
    baseline_ct_list = sorted(baseline_control_first.values(), key=lambda x: x["evaluation_at"])
    v3_ch_list = sorted(v3_challenger_first.values(), key=lambda x: x["evaluation_at"])
    v3_ct_list = sorted(v3_control_first.values(), key=lambda x: x["evaluation_at"])
    v3_ch_excl_t10 = [s for s in v3_ch_list if not s["outcomes"].get("already_top10_at_signal")]
    v3_prepub_only_rejected = sorted(
        [rec for sym, rec in v3_prepub_rejected.items() if sym not in v3_challenger_first],
        key=lambda x: x["evaluation_at"],
    )

    source_files = [
        "src/algotrader/domain/execution_brackets.py",
        "src/algotrader/domain/screening_gates.py",
        "src/algotrader/domain/archetypes.py",
        "src/algotrader/domain/screener_policy.py",
        "src/algotrader/domain/recovery.py",
        "src/algotrader/application/crypto_screener.py",
    ]

    resolved_cfg_rel = _rel_config_path(config_path)
    output = {
        "schema": "atlas-v3-rolling-replay-output-v2",
        "measurement_policy": {
            "entry": "recorded contemporaneous frame source quote at issued_at; not executed fill",
            "endpoint": "first known sample at or after issued_at + horizon, within 345 seconds",
            "excursions": "known sampled prices from issued_at through exact horizon; incomplete horizons excluded",
            "early_top10": "requires initially verified outside and alert before first verified inside",
            "performance_claims_permitted": False,
            "scope": "post-hoc counterfactual on recorded deep pools; no expanded-universe admission replay",
            "excluding_already_top10_view": "post-hoc diagnostic; not an implemented cooldown",
        },
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "config_path": resolved_cfg_rel,
        "config_sha256": sha256_file(config_path),
        "input_digests": {
            "episodes_sqlite_sha256": sha256_file(db_path),
            "price_grid_sha256": sha256_file(price_grid_path),
            "report_48h_sha256": sha256_file(report_48h_path),
            "modified_engine_files_sha256": {p: sha256_file(ROOT / p) for p in source_files},
            "all_source_python_sha256": {str(p.relative_to(ROOT)): sha256_file(p)
                                         for p in sorted((ROOT / "src").rglob("*.py"))},
            "replay_script_sha256": sha256_file(Path(__file__)),
        },
        "replay_scope": {
            "study_id": cfg["study_id"],
            "total_discovery_frames_in_sqlite": len(rows),
            "frames_with_nonempty_pool": frames_with_pool,
            "total_pool_symbol_evaluations": total_pool_evaluations,
            "price_grid": grid_meta,
            "stage1_unlocks_blocked_by_missing_v2_tape_derivatives": list(v3_missing_deriv_on_tape.values()),
        },
        "summary": {
            "baseline_v2_1_challenger": summarize_signals(baseline_ch_list),
            "baseline_v2_1_control": summarize_signals(baseline_ct_list),
            "atlas_v3_0_challenger_exact_engine": summarize_signals(v3_ch_list),
            "atlas_v3_0_challenger_excluding_already_top10": summarize_signals(v3_ch_excl_t10),
            "atlas_v3_0_control_exact_engine": summarize_signals(v3_ct_list),
            "atlas_v3_0_portal_fresh_5_of_5": summarize_signals(
                [s for s in v3_ch_list if s["opportunity"]["portal_fresh_eligible"]]
            ),
            "atlas_v3_0_prepub_rejected_only_count": len(v3_prepub_only_rejected),
        },
        "baseline_v2_1_challenger_signals": baseline_ch_list,
        "atlas_v3_0_challenger_signals": v3_ch_list,
        "atlas_v3_0_control_signals": v3_ct_list,
        "atlas_v3_0_prepub_rejected_signals": v3_prepub_only_rejected,
        "atlas_v3_0_all_frame_emissions_count": len(v3_all_frame_emissions),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Atlas v3.0 rolling replay over recorded Study 3 frames")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    from scripts.research_verified_replay import offline

    # Initialize the loop before socket denial (asyncio creates a local socketpair).
    with asyncio.Runner() as runner, offline():
        out = runner.run(run_replay(args.config))
    print(json.dumps(out["summary"], indent=2))
    print("\n--- ATLAS v3.0 EXACT ENGINE SIGNALS ---")
    for s in out["atlas_v3_0_challenger_signals"]:
        o = s["opportunity"]
        m = s["outcomes"]
        print(
            f"  {s['symbol']:12s} | {s['evaluation_at'][:16]} ({s['decision_id']}) | "
            f"{str(o['setup_label']):24s} | score={o['score']} rr={o['risk_reward_ratio']} chk={o['checklist_passed']} | "
            f"+24h={m.get('ret_24h_pct')}% +48h={m.get('ret_48h_pct')}% MFE48={m.get('mfe_48h_pct')}% MAE48={m.get('mae_48h_pct')}% Latest={m.get('ret_latest_pct')}% | "
            f"early_t10={m.get('early_before_top10')} (lead={m.get('top10_lead_hours')}h) already_t10={m.get('already_top10_at_signal')}"
        )


if __name__ == "__main__":
    main()

