"""Deterministic, zero-network evaluation & fakeout audit for `early-recovery-radar-v1`.

Evaluates the actual `CryptoOpportunityScreener` (`discovery_v2`) across three local frozen archives:
1. Frozen 402-frame Nov 15-19 rolling study (`episodes.sqlite` + `verified-price-grid-5m.json` + `report-48h.json`):
   - Arm A: Control (`early_recovery_enabled=False`, `atlas-v3.1-discovery-guards-1`)
   - Arm B: Challenger (`early_recovery_enabled=True`, `family_policy_mode="family_v1"`)
   - Arm B-Inherited: Policy ablation (`early_recovery_enabled=True`, `family_policy_mode="inherited_long"`)
   - Arm Legacy-Recovery: Legacy `PoolRecoveryScreener` (`recovery-v1`)
   - Arm D: Simple positive-momentum reference (`0% < ret_24h <= 9.5%`, liquid, outside Top-10)
2. Full-universe 320-symbol `ScanRecord` captures (`data/research/scans/*`):
   - Verifies exact flag-off parity, 45-slot core shortlist protection, <=75 Stage-2 ceiling,
     <=8 recovery probe cap, overflow accounting, and displaced baseline probes.
3. Out-of-sample Nov 22-23 36-hour Top-Gainer forensic OHLCV archive (`tmp/forensic-replay-20261123/raw/`):
   - Compares Stage-1 structural pre-gainer detection lead times (Arm A vs Arm B).

No network sockets are permitted during execution (`offline()`).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import sqlite3
import statistics
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algotrader.adapters.external.models import MacroSentimentContext  # noqa: E402
from algotrader.application.activity_recovery import (  # noqa: E402
    PoolControlScreener,
    PoolRecoveryScreener,
)
from algotrader.application.crypto_screener import CryptoOpportunityScreener  # noqa: E402
from algotrader.application.rolling_discovery import SnapshotTape  # noqa: E402
from algotrader.application.scan_recording import screening_identity  # noqa: E402
from algotrader.application.scan_replay import (  # noqa: E402
    ReplayMarket,
    ReplayMismatch,
    ReplayTape,
)
from algotrader.config import Settings  # noqa: E402
from algotrader.domain.crypto_screener_models import ScreenerMode  # noqa: E402
from algotrader.domain.early_recovery import (  # noqa: E402
    DEFAULT_EARLY_RECOVERY_POLICY,
    EarlyRecoveryPolicy,
)
from algotrader.domain.models import (  # noqa: E402
    AssetClass,
    Candle,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.scan_observations import ScanDecisionJournal  # noqa: E402
from algotrader.infrastructure.rolling_discovery import unpack  # noqa: E402
from algotrader.infrastructure.scan_archive import LocalScanArchive, canonical_bytes  # noqa: E402
from scripts.replay_atlas_v3_rolling import compute_forward_metrics, load_price_grid  # noqa: E402
from scripts.research_pipeline_comparison import SnapshotCall  # noqa: E402
from scripts.research_verified_replay import offline  # noqa: E402

DEFAULT_OUTPUT = (
    ROOT / "docs" / "security" / "evidence" / "2026-11-24-early-recovery-replay-results.json"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class GracefulCounterfactualTape(ReplayTape):
    """Consumes recorded `ScanRecord` inputs without network fallback; tracks unrecorded Stage-2 probes."""

    def __init__(self, calls: tuple[Any, ...]) -> None:
        super().__init__(tuple(SnapshotCall(c) for c in calls))
        self.missing_requests: list[dict[str, Any]] = []

    def take(self, method: str, arguments: dict[str, Any]) -> Any:
        try:
            return super().take(method, arguments)
        except ReplayMismatch:
            inst = arguments.get("instrument")
            self.missing_requests.append(
                {
                    "method": method,
                    "symbol": inst.id.symbol if inst is not None else None,
                    "interval": arguments.get("interval"),
                }
            )
            raise


def simulate_5m_barriers(
    *,
    sym: str,
    t0_ep: float,
    direction: str,
    execution_mode: str,
    entry: float,
    stop: float,
    t1: float,
    setup_label: str,
    traj: dict[str, list[tuple[float, float]]],
    horizon_hours: int = 48,
) -> dict[str, Any]:
    """Evaluate barrier outcomes on the verified 5m sampled price trajectory.

    Explicitly labeled as 5-minute sampled point-price barrier approximation (not continuous
    tick or intrabar OHLC fills). Conservative stop priority applies if any ambiguity occurs.
    """
    pts = traj.get(sym, [])
    future = [(t, p) for t, p in pts if t0_ep < t <= t0_ep + horizon_hours * 3600]
    if not future or entry <= 0 or stop <= 0 or t1 <= 0:
        return {"status": "unknown_trajectory", "triggered": False, "net_r_approx": None}

    is_short = direction.lower() == "short"
    risk = abs(entry - stop)
    if risk <= 0:
        return {"status": "invalid_risk", "triggered": False, "net_r_approx": None}

    wait_hours = (
        12.0
        if setup_label == "RECOVERY_RECLAIM_LONG"
        else 4.0
        if setup_label == "BREAKDOWN_RETEST_SHORT"
        else 24.0
    )
    wait_deadline = t0_ep + wait_hours * 3600
    is_trigger_mode = execution_mode in {
        "CEILING_BREAKOUT",
        "BREAKOUT_TRIGGER",
        "BREAKDOWN_TRIGGER",
        "BREAKOUT_EXPANSION",
        "BREAKDOWN_EXPANSION",
    }

    active = False
    triggered_at_ep: float | None = None
    cost_r = (entry * 0.0020) / risk  # 20 bps round-trip friction assumption

    for idx, (t, p) in enumerate(future):
        stopped = p >= stop if is_short else p <= stop
        targeted = p <= t1 if is_short else p >= t1
        if not active:
            if t > wait_deadline:
                return {
                    "status": "untriggered_expired",
                    "triggered": False,
                    "net_r_approx": 0.0,
                }
            if stopped:
                return {
                    "status": "invalidated_before_entry",
                    "triggered": False,
                    "event_offset_hours": round((t - t0_ep) / 3600, 2),
                    "net_r_approx": 0.0,
                }
            touched = (
                (p <= entry if is_short else p >= entry)
                if is_trigger_mode
                else (p >= entry if is_short else p <= entry)
            )
            if not touched:
                continue
            # If the very first sample after issuance jumped straight past both entry and target, mark ambiguous
            if idx == 0 and targeted:
                return {
                    "status": "ambiguous_opening_jump",
                    "triggered": True,
                    "net_r_approx": None,
                }
            active = True
            triggered_at_ep = t
            # Check subsequent samples after trigger sample so entry & target aren't conflated at same instant
            continue

        if stopped and targeted:
            return {
                "status": "ambiguous_same_sample",
                "triggered": True,
                "net_r_approx": None,
            }
        if stopped:
            sign = -1.0 if is_short else 1.0
            exit_px = max(stop, p) if is_short else min(stop, p)
            net_r = ((exit_px - entry) * sign) / risk - cost_r
            return {
                "status": "stop_before_target_1",
                "triggered": True,
                "triggered_offset_hours": round(((triggered_at_ep or t) - t0_ep) / 3600, 2),
                "event_offset_hours": round((t - t0_ep) / 3600, 2),
                "net_r_approx": round(net_r, 3),
            }
        if targeted:
            sign = -1.0 if is_short else 1.0
            net_r = ((t1 - entry) * sign) / risk - cost_r
            return {
                "status": "target_1_before_stop",
                "triggered": True,
                "triggered_offset_hours": round(((triggered_at_ep or t) - t0_ep) / 3600, 2),
                "event_offset_hours": round((t - t0_ep) / 3600, 2),
                "net_r_approx": round(net_r, 3),
            }

    if active:
        last_t, last_p = future[-1]
        sign = -1.0 if is_short else 1.0
        mark_r = ((last_p - entry) * sign) / risk - cost_r
        return {
            "status": "triggered_unresolved",
            "triggered": True,
            "triggered_offset_hours": round(((triggered_at_ep or last_t) - t0_ep) / 3600, 2),
            "net_r_approx": round(mark_r, 3),
        }
    return {"status": "untriggered_expired", "triggered": False, "net_r_approx": 0.0}


def _dist_stats(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {
            "known_n": 0,
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "min": None,
            "max": None,
            "positive_hits": 0,
            "positive_pct": None,
        }
    s = sorted(vals)
    n = len(s)
    p25 = s[max(0, int(0.25 * (n - 1)))]
    p75 = s[min(n - 1, int(0.75 * (n - 1)))]
    pos = sum(1 for v in s if v > 0.0)
    return {
        "known_n": n,
        "mean": round(sum(s) / n, 2),
        "median": round(statistics.median(s), 2),
        "p25": round(p25, 2),
        "p75": round(p75, 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
        "positive_hits": pos,
        "positive_pct": round(pos / n * 100.0, 1),
    }


def summarize_arm_population(
    signals: list[dict[str, Any]],
    *,
    total_study_new_top10: int,
) -> dict[str, Any]:
    n = len(signals)
    if n == 0:
        return {
            "unique_symbols": 0,
            "total_study_new_top10_denominator": total_study_new_top10,
            "early_top10_hits": 0,
            "early_top10_recall_pct": 0.0 if total_study_new_top10 else None,
            "early_top10_precision_pct": None,
        }
    r24 = [
        float(s["outcomes"]["ret_24h_pct"])
        for s in signals
        if s["outcomes"].get("ret_24h_pct") is not None
    ]
    r48 = [
        float(s["outcomes"]["ret_48h_pct"])
        for s in signals
        if s["outcomes"].get("ret_48h_pct") is not None
    ]
    mfe24 = [
        float(s["outcomes"]["mfe_24h_pct"])
        for s in signals
        if s["outcomes"].get("mfe_24h_pct") is not None
    ]
    mae24 = [
        float(s["outcomes"]["mae_24h_pct"])
        for s in signals
        if s["outcomes"].get("mae_24h_pct") is not None
    ]
    mfe48 = [
        float(s["outcomes"]["mfe_48h_pct"])
        for s in signals
        if s["outcomes"].get("mfe_48h_pct") is not None
    ]
    mae48 = [
        float(s["outcomes"]["mae_48h_pct"])
        for s in signals
        if s["outcomes"].get("mae_48h_pct") is not None
    ]

    early_hits = [s for s in signals if s["outcomes"].get("early_before_top10")]
    early_within_study = [
        s for s in signals if s["outcomes"].get("early_top10_within_initial_study_48h")
    ]
    already_top10 = [s for s in signals if s["outcomes"].get("already_top10_at_signal")]
    leads = [
        float(s["outcomes"]["top10_lead_hours"])
        for s in early_hits
        if s["outcomes"].get("top10_lead_hours") is not None
    ]

    barrier_counts = Counter(s.get("barrier_5m", {}).get("status", "unknown") for s in signals)
    by_setup = Counter(str(s["opportunity"].get("setup_label")) for s in signals)

    # Sensitivity with single largest positive outlier removed
    r24_no_max = sorted(r24)[:-1] if len(r24) > 1 else []
    r48_no_max = sorted(r48)[:-1] if len(r48) > 1 else []

    unsuccessful_48h_quote = sum(1 for v in r48 if v <= 0.0)
    unsuccessful_barrier = (
        barrier_counts["stop_before_target_1"] + barrier_counts["invalidated_before_entry"]
    )

    return {
        "unique_symbols": n,
        "symbols": [s["symbol"] for s in signals],
        "by_setup_label": dict(sorted(by_setup.items())),
        "already_top10_at_signal_count": len(already_top10),
        "early_top10_hits": len(early_hits),
        "early_top10_symbols": [s["symbol"] for s in early_hits],
        "early_top10_within_initial_study_48h": len(early_within_study),
        "total_study_new_top10_denominator": total_study_new_top10,
        "early_top10_recall_pct": (
            round(len(early_within_study) / total_study_new_top10 * 100.0, 2)
            if total_study_new_top10 > 0
            else None
        ),
        "early_top10_precision_pct": round(len(early_hits) / n * 100.0, 2),
        "lead_time_hours": {
            "count": len(leads),
            "median": round(statistics.median(leads), 2) if leads else None,
            "mean": round(sum(leads) / len(leads), 2) if leads else None,
            "min": round(min(leads), 2) if leads else None,
            "max": round(max(leads), 2) if leads else None,
        },
        "ret_24h_pct": {
            **_dist_stats(r24),
            "unknown_or_censored_n": n - len(r24),
        },
        "ret_48h_pct": {
            **_dist_stats(r48),
            "unknown_or_censored_n": n - len(r48),
        },
        "sampled_excursions_24h_pct": {
            "mfe": _dist_stats(mfe24),
            "mae": _dist_stats(mae24),
        },
        "sampled_excursions_48h_pct": {
            "mfe": _dist_stats(mfe48),
            "mae": _dist_stats(mae48),
        },
        "barrier_simulation_5m_48h": {
            "semantics": "5-minute sampled point-price barrier approximation with 20bps friction; not continuous fills",
            "counts": dict(sorted(barrier_counts.items())),
            "target_1_hits": barrier_counts["target_1_before_stop"],
            "stop_hits": barrier_counts["stop_before_target_1"],
            "invalidated_before_entry": barrier_counts["invalidated_before_entry"],
            "untriggered_expired": barrier_counts["untriggered_expired"],
            "triggered_unresolved": barrier_counts["triggered_unresolved"],
        },
        "unsuccessful_counts": {
            "quote_48h_nonpositive": unsuccessful_48h_quote,
            "barrier_stop_or_preentry_invalidation": unsuccessful_barrier,
        },
        "outlier_removed_sensitivity": {
            "ret_24h_without_largest_winner": _dist_stats(r24_no_max),
            "ret_48h_without_largest_winner": _dist_stats(r48_no_max),
        },
    }


def compute_incremental_tradeoff(
    control_summary: dict[str, Any],
    challenger_summary: dict[str, Any],
    control_signals: list[dict[str, Any]],
    challenger_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    ctrl_syms = {s["symbol"] for s in control_signals}
    added = [s for s in challenger_signals if s["symbol"] not in ctrl_syms]
    removed = [s["symbol"] for s in control_signals if s["symbol"] not in {x["symbol"] for x in challenger_signals}]

    added_early = [s for s in added if s["outcomes"].get("early_before_top10")]
    added_r48 = [
        float(s["outcomes"]["ret_48h_pct"])
        for s in added
        if s["outcomes"].get("ret_48h_pct") is not None
    ]
    added_losers_48h = [
        s["symbol"]
        for s in added
        if s["outcomes"].get("ret_48h_pct") is not None and float(s["outcomes"]["ret_48h_pct"]) <= 0.0
    ]
    added_barrier_stops = [
        s["symbol"]
        for s in added
        if s.get("barrier_5m", {}).get("status") in ("stop_before_target_1", "invalidated_before_entry")
    ]
    added_barrier_targets = [
        s["symbol"]
        for s in added
        if s.get("barrier_5m", {}).get("status") == "target_1_before_stop"
    ]

    delta_early = len(added_early)
    return {
        "added_unique_symbols_count": len(added),
        "added_symbols": [s["symbol"] for s in added],
        "removed_symbols_count": len(removed),
        "removed_symbols": removed,
        "added_early_top10_count": delta_early,
        "added_early_top10_symbols": [s["symbol"] for s in added_early],
        "added_known_48h_count": len(added_r48),
        "added_nonpositive_48h_count": len(added_losers_48h),
        "added_nonpositive_48h_symbols": added_losers_48h,
        "added_barrier_target1_symbols": added_barrier_targets,
        "added_barrier_stop_or_invalidated_symbols": added_barrier_stops,
        "additional_nonpositive_48h_per_additional_early_top10": (
            round(len(added_losers_48h) / delta_early, 2) if delta_early > 0 else None
        ),
        "additional_barrier_stops_per_additional_early_top10": (
            round(len(added_barrier_stops) / delta_early, 2) if delta_early > 0 else None
        ),
        "control_unique_symbols": control_summary.get("unique_symbols", 0),
        "challenger_unique_symbols": challenger_summary.get("unique_symbols", 0),
    }


def _pack_signal(
    *,
    sym: str,
    decision_id: str,
    eval_at: datetime,
    issued_at: datetime,
    opp_dict: dict[str, Any],
    entry_quote: float,
    traj: dict[str, list[tuple[float, float]]],
    initial_top10: set[str],
    first_top10_epoch: dict[str, float],
    first_observed_inside_epoch: dict[str, float],
    initial_study_endpoint: float,
) -> dict[str, Any]:
    t0_ep = issued_at.timestamp()
    outcomes = compute_forward_metrics(
        sym,
        t0_ep,
        entry_quote,
        traj,
        initial_top10,
        first_top10_epoch,
        first_observed_inside_epoch,
    )
    first_inside = first_top10_epoch.get(sym)
    outcomes["early_top10_within_initial_study_48h"] = bool(
        outcomes.get("early_before_top10")
        and first_inside is not None
        and first_inside <= initial_study_endpoint
    )
    targets = [float(t) for t in opp_dict.get("suggested_targets", ()) if t != "N/A"]
    entry_f = float(opp_dict["suggested_entry"])
    stop_f = float(opp_dict["suggested_stop"])
    t1_f = targets[0] if targets else entry_f
    barrier = simulate_5m_barriers(
        sym=sym,
        t0_ep=t0_ep,
        direction=str(opp_dict.get("direction", "long")),
        execution_mode=str(opp_dict.get("execution_mode", "CEILING_BREAKOUT")),
        entry=entry_f,
        stop=stop_f,
        t1=t1_f,
        setup_label=str(opp_dict.get("setup_label", "")),
        traj=traj,
    )
    return {
        "symbol": sym,
        "decision_id": decision_id,
        "evaluation_at": eval_at.isoformat(),
        "issued_at": issued_at.isoformat(),
        "opportunity": {
            "symbol": sym,
            "setup_label": opp_dict.get("setup_label"),
            "direction": opp_dict.get("direction"),
            "execution_mode": opp_dict.get("execution_mode"),
            "score": float(opp_dict["score"]),
            "conviction": float(opp_dict["conviction"]),
            "grade": opp_dict.get("grade"),
            "risk_reward_ratio": float(opp_dict["risk_reward_ratio"]),
            "expected_value_r": float(opp_dict["expected_value_r"]),
            "current_price": float(opp_dict["current_price"]),
            "suggested_entry": entry_f,
            "suggested_stop": stop_f,
            "suggested_targets": targets,
            "checklist_gates": [
                {"title": c["title"], "passed": c["passed"]}
                for c in opp_dict.get("checklist", ())
            ],
            "oi_change_1h_pct": opp_dict.get("oi_change_1h_pct"),
            "oi_change_7d_pct": opp_dict.get("oi_change_7d_pct"),
            "funding_rate_bps": opp_dict.get("funding_rate_bps"),
            "taker_ratio_4h": opp_dict.get("taker_buy_sell_ratio_4h"),
            "research_policy_version": opp_dict.get("research_policy_version"),
        },
        "outcomes": outcomes,
        "barrier_5m": barrier,
    }


def _load_rolling_meta_and_rows() -> tuple[dict[str, Any], Path, Path, list[Any]]:
    cfg_path = ROOT / "docs" / "security" / "atlas_v3_replay_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    db_path = ROOT / cfg["inputs"]["episodes_sqlite_local"]
    price_grid_5m = ROOT / "tmp" / "atlas-v3-review-20261120" / "verified-price-grid-5m.json"
    price_grid_path = (
        price_grid_5m
        if price_grid_5m.exists()
        else ROOT / "tmp" / "fresh-rolling-study-20261115" / "study" / "price_grid_15m.json"
    )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT decision_id, issued_at, payload, sha256 FROM discovery_frames ORDER BY issued_at ASC, decision_id ASC"
        ).fetchall()
    finally:
        conn.close()
    return cfg, db_path, price_grid_path, rows


async def evaluate_rolling_study_archive() -> dict[str, Any]:
    cfg, db_path, price_grid_path, rows = _load_rolling_meta_and_rows()
    report_48h_path = ROOT / cfg["inputs"]["report_48h_local"]

    traj, initial_top10, first_top10_epoch, grid_meta = load_price_grid(
        price_grid_path, report_48h_path
    )
    initial_study_endpoint = (
        datetime.fromisoformat(cfg["study_anchor_utc"]).timestamp() + 48 * 3600
    )
    total_study_new_top10 = sum(
        1 for ep in first_top10_epoch.values() if ep <= initial_study_endpoint
    )

    base_settings_dict = {
        "excluded_base_assets": tuple(cfg["screener_settings"]["excluded_base_assets"]),
        "support_resistance_lookback_bars": int(
            cfg["screener_settings"]["support_resistance_lookback_bars"]
        ),
        "stage2_capacity_rollover_enabled": True,
        "bearish_continuation_enabled": False,
    }
    settings_off = Settings.model_construct(**base_settings_dict, early_recovery_enabled=False)
    settings_on = Settings.model_construct(**base_settings_dict, early_recovery_enabled=True)
    inherited_policy = EarlyRecoveryPolicy(
        version=DEFAULT_EARLY_RECOVERY_POLICY.version,
        family_policy_mode="inherited_long",
    )

    arm_a_first: dict[str, dict[str, Any]] = {}
    arm_b_first: dict[str, dict[str, Any]] = {}
    arm_b_inherited_first: dict[str, dict[str, Any]] = {}
    arm_legacy_rec_first: dict[str, dict[str, Any]] = {}
    arm_d_momentum_first: dict[str, dict[str, Any]] = {}

    arm_a_episodes: dict[tuple[str, str, str], dict[str, Any]] = {}
    arm_b_episodes: dict[tuple[str, str, str], dict[str, Any]] = {}
    arm_b_rejection_reasons: Counter[str] = Counter()
    arm_b_stage1_ready_missing_tape_derivs: set[str] = set()
    arm_b_dedup_events: Counter[str] = Counter()

    frames_evaluated = 0
    total_pool_evals = 0

    for decision_id, _issued_str, payload_blob, sha in rows:
        frame = unpack(payload_blob, sha)
        pool = tuple(frame["pool"])
        if not pool or frame.get("global_input_unavailable"):
            continue
        frames_evaluated += 1
        total_pool_evals += len(pool)
        pool_set = set(pool)
        eval_at: datetime = frame["evaluation_at"]
        issued_at: datetime = frame["issued_at"]
        source_quotes: Any = frame.get("source_quotes", ())
        entry_quotes = {q.instrument_id.symbol: float(q.last) for q in source_quotes}

        ranked_sq = sorted(
            (
                (q.price_change_pct_24h, q.instrument_id.symbol, q)
                for q in source_quotes
                if q.price_change_pct_24h is not None and q.quote_volume_24h >= Decimal("500000")
            ),
            key=lambda x: (x[0], x[2].quote_volume_24h, x[1]),
            reverse=True,
        )
        live_top10 = frozenset(sym for chg, sym, _ in ranked_sq[:10] if chg >= Decimal("10.0"))

        # Arm D: Naive momentum reference (top 1 symbol in pool with 0% < 24h chg <= 9.5%, vol >= $1.2M, spread <= 25bps)
        mom_cands = [
            (chg, q.quote_volume_24h, sym, q)
            for chg, sym, q in ranked_sq
            if sym in pool_set
            and sym not in live_top10
            and Decimal("0.5") <= chg <= Decimal("9.5")
            and q.quote_volume_24h >= Decimal("1200000")
            and q.spread_bps <= Decimal("25")
        ]
        if mom_cands:
            _, _, m_sym, m_q = mom_cands[0]
            if m_sym not in arm_d_momentum_first:
                px = float(m_q.last)
                synth_opp = {
                    "symbol": m_sym,
                    "setup_label": "NAIVE_MOMENTUM_REF",
                    "direction": "long",
                    "execution_mode": "CEILING_BREAKOUT",
                    "score": 70.0,
                    "conviction": 70.0,
                    "grade": "Reference",
                    "risk_reward_ratio": 2.0,
                    "expected_value_r": 0.5,
                    "current_price": px,
                    "suggested_entry": px * 1.002,
                    "suggested_stop": px * 0.95,
                    "suggested_targets": [px * 1.10, px * 1.15],
                    "checklist": [],
                }
                arm_d_momentum_first[m_sym] = _pack_signal(
                    sym=m_sym,
                    decision_id=decision_id,
                    eval_at=eval_at,
                    issued_at=issued_at,
                    opp_dict=synth_opp,
                    entry_quote=px,
                    traj=traj,
                    initial_top10=initial_top10,
                    first_top10_epoch=first_top10_epoch,
                    first_observed_inside_epoch=grid_meta["first_observed_inside_epoch"],
                    initial_study_endpoint=initial_study_endpoint,
                )

        async def _fixed_clock(_inst: Any, _ts: datetime = issued_at) -> datetime:
            return _ts

        # Run Arms A, B, B-Inherited, and Legacy-Recovery on identical recorded frame inputs
        arm_specs: tuple[tuple[str, Any, dict[str, dict[str, Any]]], ...] = (
            ("arm_a", ("control", settings_off, DEFAULT_EARLY_RECOVERY_POLICY), arm_a_first),
            ("arm_b", ("early_recovery", settings_on, DEFAULT_EARLY_RECOVERY_POLICY), arm_b_first),
            ("arm_b_inherited", ("early_recovery", settings_on, inherited_policy), arm_b_inherited_first),
            ("arm_legacy_rec", ("legacy_rec", settings_off, DEFAULT_EARLY_RECOVERY_POLICY), arm_legacy_rec_first),
        )
        for arm_name, (kind, s_obj, pol), first_map in arm_specs:
            market = ReplayMarket(SnapshotTape(frame["inputs"]))
            journal = ScanDecisionJournal()
            if kind == "legacy_rec":
                screener: Any = PoolRecoveryScreener(
                    s_obj,
                    market,
                    evaluated_at=eval_at,
                    decision_at=issued_at,
                    journal=journal,
                )
            else:
                screener = PoolControlScreener(
                    s_obj,
                    market,
                    evaluated_at=eval_at,
                    feature_contract="discovery_v2",
                    journal=journal,
                    early_recovery_policy=pol,
                    recovery_clock=_fixed_clock if s_obj.early_recovery_enabled else None,
                )
            result = await screener.screen(
                market,
                market,
                max_symbols=320,
                target_direction=Direction.LONG,
                enforce_quality=True,
                top_gainer_symbols=live_top10,
            )
            missing = screener._fetch_failures | screener._feature_unavailable
            for opp in result:
                sym = opp.instrument.id.symbol
                if sym not in pool_set or sym in missing:
                    continue
                opp_d = opp.as_dict()
                packed = _pack_signal(
                    sym=sym,
                    decision_id=decision_id,
                    eval_at=eval_at,
                    issued_at=issued_at,
                    opp_dict=opp_d,
                    entry_quote=entry_quotes.get(sym, float(opp.current_price)),
                    traj=traj,
                    initial_top10=initial_top10,
                    first_top10_epoch=first_top10_epoch,
                    first_observed_inside_epoch=grid_meta["first_observed_inside_epoch"],
                    initial_study_endpoint=initial_study_endpoint,
                )
                if sym not in first_map:
                    first_map[sym] = packed
                ep_anchor = str(
                    (opp_d.get("recovery_evidence") or {}).get("plan", {}).get("pivot")
                    or opp_d.get("suggested_entry")
                )
                ep_key = (sym, str(opp_d.get("setup_label")), ep_anchor)
                if arm_name == "arm_a" and ep_key not in arm_a_episodes:
                    arm_a_episodes[ep_key] = packed
                elif arm_name == "arm_b" and ep_key not in arm_b_episodes:
                    arm_b_episodes[ep_key] = packed

            if arm_name == "arm_b":
                decs = journal.snapshot()
                for ev in decs:
                    st = ev.get("stage")
                    if st == "early_recovery_gates" and ev.get("symbol") in pool_set:
                        arm_b_rejection_reasons[
                            f"{'enriched' if ev.get('enriched') else 'stage1'}:{ev.get('reason')}"
                        ] += 1
                    elif st == "deduplication":
                        arm_b_dedup_events[str(ev.get("reason"))] += 1
                for sym_f in screener._fetch_failures:
                    if sym_f in pool_set and sym_f in screener._recovery_ready_scores:
                        arm_b_stage1_ready_missing_tape_derivs.add(sym_f)

    arm_a_list = sorted(arm_a_first.values(), key=lambda x: x["evaluation_at"])
    arm_b_list = sorted(arm_b_first.values(), key=lambda x: x["evaluation_at"])
    arm_bi_list = sorted(arm_b_inherited_first.values(), key=lambda x: x["evaluation_at"])
    arm_leg_list = sorted(arm_legacy_rec_first.values(), key=lambda x: x["evaluation_at"])
    arm_d_list = sorted(arm_d_momentum_first.values(), key=lambda x: x["evaluation_at"])

    sum_a = summarize_arm_population(arm_a_list, total_study_new_top10=total_study_new_top10)
    sum_b = summarize_arm_population(arm_b_list, total_study_new_top10=total_study_new_top10)
    sum_bi = summarize_arm_population(arm_bi_list, total_study_new_top10=total_study_new_top10)
    sum_leg = summarize_arm_population(arm_leg_list, total_study_new_top10=total_study_new_top10)
    sum_d = summarize_arm_population(arm_d_list, total_study_new_top10=total_study_new_top10)

    sum_a["unique_episodes"] = len(arm_a_episodes)
    sum_b["unique_episodes"] = len(arm_b_episodes)

    return {
        "dataset": "nov15_19_rolling_402_frames",
        "evidence_classification": "exploratory_retrospective_counterfactual_on_frozen_study3",
        "study_id": cfg["study_id"],
        "frames_evaluated": frames_evaluated,
        "total_pool_evaluations": total_pool_evals,
        "price_grid_path": str(price_grid_path.relative_to(ROOT)),
        "price_grid_sha256": sha256_file(price_grid_path),
        "episodes_sqlite_sha256": sha256_file(db_path),
        "arms": {
            "arm_a_control_flag_off": sum_a,
            "arm_b_early_recovery_family_v1": sum_b,
            "arm_b_inherited_long_policy_ablation": sum_bi,
            "arm_legacy_recovery_screener_v1": sum_leg,
            "arm_d_naive_momentum_reference": sum_d,
        },
        "incremental_tradeoff_arm_b_vs_arm_a": compute_incremental_tradeoff(
            sum_a, sum_b, arm_a_list, arm_b_list
        ),
        "policy_ablation_family_v1_vs_inherited_long": compute_incremental_tradeoff(
            sum_bi, sum_b, arm_bi_list, arm_b_list
        ),
        "early_recovery_gate_audit_counts": dict(sorted(arm_b_rejection_reasons.items())),
        "deduplication_events": dict(sorted(arm_b_dedup_events.items())),
        "stage1_ready_missing_recorded_derivatives_symbols": sorted(
            arm_b_stage1_ready_missing_tape_derivs
        ),
        "arm_a_signals": arm_a_list,
        "arm_b_signals": arm_b_list,
        "arm_b_inherited_signals": arm_bi_list,
    }


def _list_scan_ids() -> list[str]:
    scans_root = ROOT / "data" / "research" / "scans"
    if not scans_root.exists():
        return []
    return [d.name for d in sorted(scans_root.iterdir()) if d.is_dir()]


async def evaluate_full_universe_scan_archives() -> list[dict[str, Any]]:
    scan_ids = _list_scan_ids()
    if not scan_ids:
        return []
    archive = LocalScanArchive(ROOT / "data" / "research")
    out: list[dict[str, Any]] = []
    for scan_id in scan_ids:
        record = archive.read_scan(scan_id)
        cfg = dict(record.identity["configuration"])
        settings_off = Settings.model_construct(
            **{
                "bearish_continuation_canary_enabled": False,
                "bearish_continuation_enabled": False,
                "early_recovery_enabled": False,
                **cfg,
            }
        )
        settings_on = Settings.model_construct(
            **{
                "bearish_continuation_canary_enabled": False,
                "bearish_continuation_enabled": False,
                "early_recovery_enabled": True,
                **cfg,
            }
        )
        req = dict(record.request)
        feat = req.pop("feature_contract", "legacy")
        deriv_enabled = req.pop("derivatives_enabled", True)

        # Arm A (flag off)
        tape_a = GracefulCounterfactualTape(record.inputs)
        mkt_a = ReplayMarket(tape_a)
        j_a = ScanDecisionJournal()
        scr_a = CryptoOpportunityScreener(
            settings_off,
            mkt_a,
            evaluated_at=record.started_at,
            journal=j_a,
            feature_contract=feat,
        )
        with redirect_stdout(io.StringIO()):
            opps_a = await scr_a.screen(
                mkt_a, derivatives_port=mkt_a if deriv_enabled else None, **req
            )

        # Arm C (flag on, bounded <=8 probe allocation)
        tape_c = GracefulCounterfactualTape(record.inputs)
        mkt_c = ReplayMarket(tape_c)
        j_c = ScanDecisionJournal()

        async def _scan_clock(_inst: Any, _ts: datetime = record.completed_at) -> datetime:
            return _ts

        scr_c = CryptoOpportunityScreener(
            settings_on,
            mkt_c,
            evaluated_at=record.started_at,
            journal=j_c,
            feature_contract=feat,
            recovery_clock=_scan_clock,
        )
        with redirect_stdout(io.StringIO()):
            opps_c = await scr_c.screen(
                mkt_c, derivatives_port=mkt_c if deriv_enabled else None, **req
            )

        decs_a = j_a.snapshot()
        decs_c = j_c.snapshot()
        shortlist_a = [
            d["symbol"]
            for d in decs_a
            if d.get("stage") == "stage2" and d.get("allocation_lane") == "shortlist"
        ]
        shortlist_c = [
            d["symbol"]
            for d in decs_c
            if d.get("stage") == "stage2" and d.get("allocation_lane") == "shortlist"
        ]
        rec_admitted = [
            d["symbol"]
            for d in decs_c
            if d.get("stage") == "recovery_allocation" and d.get("reason") == "admitted_within_cap"
        ]
        rec_overflow = [
            d["symbol"]
            for d in decs_c
            if d.get("stage") == "recovery_allocation" and d.get("reason") == "cap_overflow"
        ]
        displaced = [
            d["symbol"]
            for d in decs_c
            if d.get("stage") == "recovery_allocation"
            and d.get("reason") == "displaced_baseline_probe"
        ]
        missing_deriv_syms = sorted(
            {
                m["symbol"]
                for m in tape_c.missing_requests
                if m.get("method") == "derivatives_snapshot" and m.get("symbol")
            }
        )
        confirmed_rec = [
            o.as_dict()
            for o in opps_c
            if o.setup_label.value == "RECOVERY_RECLAIM_LONG"
            and o.instrument.id.symbol not in missing_deriv_syms
        ]

        out.append(
            {
                "scan_id": scan_id,
                "started_at": record.started_at.isoformat(),
                "completed_at": record.completed_at.isoformat(),
                "feature_contract": feat,
                "flag_off_tape_exact_consumed": not tape_a.missing and not any(tape_a.calls.values()),
                "flag_off_cohort_matches_recorded": canonical_bytes(j_a.cohort)
                == canonical_bytes(record.cohort),
                "core_shortlist_45_identical": shortlist_a == shortlist_c,
                "core_shortlist_count": len(shortlist_c),
                "stage2_total_admitted_arm_a": scr_a.scan_counts.get("stage2_shortlist", 0)
                + scr_a.scan_counts.get("stage2_probes", 0),
                "stage2_total_admitted_arm_c": scr_c.scan_counts.get("stage2_shortlist", 0)
                + scr_c.scan_counts.get("stage2_probes", 0),
                "stage2_ceiling_respected_le_75": (
                    scr_c.scan_counts.get("stage2_shortlist", 0)
                    + scr_c.scan_counts.get("stage2_probes", 0)
                )
                <= 75,
                "recovery_stage1_ready_count": scr_c.scan_counts.get("recovery_stage1_ready", 0),
                "recovery_probe_admitted_count": len(rec_admitted),
                "recovery_probe_cap_respected_le_8": len(rec_admitted) <= 8,
                "recovery_probe_admitted_symbols": rec_admitted,
                "recovery_probe_overflow_symbols": rec_overflow,
                "displaced_baseline_probes": displaced,
                "unrecorded_derivatives_for_new_probes": missing_deriv_syms,
                "arm_a_published_symbols": [o.instrument.id.symbol for o in opps_a],
                "arm_c_published_symbols_on_available_tape": [
                    o.instrument.id.symbol
                    for o in opps_c
                    if o.instrument.id.symbol not in missing_deriv_syms
                ],
                "confirmed_recovery_on_available_tape": [c["symbol"] for c in confirmed_rec],
            }
        )
    return out


def _parse_klines_offline(raw: list[list[Any]]) -> list[Candle]:
    out: list[Candle] = []
    for r in raw:
        out.append(
            Candle(
                opened_at=datetime.fromtimestamp(int(r[0]) / 1000, tz=UTC),
                closed_at=datetime.fromtimestamp(int(r[6]) / 1000, tz=UTC),
                open=Decimal(str(r[1])),
                high=Decimal(str(r[2])),
                low=Decimal(str(r[3])),
                close=Decimal(str(r[4])),
                volume=Decimal(str(r[5])),
                quote_volume=Decimal(str(r[7])),
                taker_buy_volume=Decimal(str(r[9])),
                taker_buy_quote_volume=Decimal(str(r[10])),
                closed=True,
            )
        )
    return out


def _load_nov23_klines() -> tuple[list[Candle], list[tuple[str, list[Candle], list[Candle], list[Candle]]]] | None:
    raw_dir = ROOT / "tmp" / "forensic-replay-20261123" / "raw"
    btc_file = raw_dir / "BTCUSDT_1h.json"
    if not btc_file.exists():
        return None
    btc_all = _parse_klines_offline(json.loads(btc_file.read_text(encoding="utf-8")))[:-1]
    t0_closed = btc_all[-1].closed_at
    items: list[tuple[str, list[Candle], list[Candle], list[Candle]]] = []
    for p1h in sorted(raw_dir.glob("*_1h.json")):
        sym = p1h.name.removesuffix("_1h.json")
        if sym == "BTCUSDT":
            continue
        p4h = raw_dir / f"{sym}_4h.json"
        p1w = raw_dir / f"{sym}_1w.json"
        if not p4h.exists():
            continue
        c1h = [
            c
            for c in _parse_klines_offline(json.loads(p1h.read_text(encoding="utf-8")))[:-1]
            if c.closed_at <= t0_closed
        ]
        c4h = [
            c
            for c in _parse_klines_offline(json.loads(p4h.read_text(encoding="utf-8")))[:-1]
            if c.closed_at <= t0_closed
        ]
        c1w = (
            [
                c
                for c in _parse_klines_offline(json.loads(p1w.read_text(encoding="utf-8")))[:-1]
                if c.closed_at <= t0_closed
            ]
            if p1w.exists()
            else []
        )
        if c1h and c4h:
            items.append((sym, c1h, c4h, c1w))
    return btc_all, items


async def evaluate_nov23_forensic_stage1_archive() -> dict[str, Any]:
    loaded = _load_nov23_klines()
    if loaded is None:
        return {"available": False}
    btc_all, kline_items = loaded
    t0_closed = btc_all[-1].closed_at

    settings_off = Settings.model_construct(early_recovery_enabled=False)
    settings_on = Settings.model_construct(early_recovery_enabled=True)

    symbols_report: dict[str, Any] = {}
    core_caught = 0
    recovery_stage1_caught = 0

    for sym, c1h, c4h, c1w in kline_items:

        inst = Instrument(
            id=InstrumentId(Venue.BINANCE_USDM, sym),
            base_asset=sym.removesuffix("USDT"),
            quote_asset="USDT",
            asset_class=AssetClass.CRYPTO,
            market_kind=MarketKind.FUTURES,
            trading=True,
            price_tick=Decimal("0.00001"),
            quantity_step=Decimal("1"),
            min_quantity=Decimal("1"),
            min_notional=Decimal("5"),
        )

        core_first_offset: int | None = None
        rec_first_offset: int | None = None
        rec_first_gain_to_t0: float | None = None

        for h in range(36, 0, -1):
            from datetime import timedelta

            cutoff = t0_closed - timedelta(hours=h)
            sub_1h = [c for c in c1h if c.closed_at <= cutoff]
            sub_4h = [c for c in c4h if c.closed_at <= cutoff]
            sub_btc = [c for c in btc_all if c.closed_at <= cutoff]
            if len(sub_1h) < 48 or len(sub_4h) < 50:
                continue
            p_cut = sub_1h[-1].close
            vol_24h = sum((c.quote_volume or (c.volume * c.close)) for c in sub_1h[-24:])
            q_sim = Quote(
                inst.id,
                p_cut * Decimal("0.9998"),
                p_cut * Decimal("1.0002"),
                p_cut,
                vol_24h,
                observed_at=cutoff,
            )

            scr_off = CryptoOpportunityScreener(
                settings_off, evaluated_at=cutoff, feature_contract="discovery_v2"
            )
            res_off = await scr_off._evaluate_single(
                inst=inst,
                quote=q_sim,
                market=None,  # type: ignore[arg-type]
                derivatives_port=None,
                btc_candles=sub_btc,
                macro_context=MacroSentimentContext(observed_at=cutoff),
                mode=ScreenerMode.ACCUMULATION,
                target_direction=Direction.LONG,
                preloaded_candles=(sub_1h, sub_4h, c1w),
            )
            if res_off and core_first_offset is None:
                core_first_offset = -h

            scr_on = CryptoOpportunityScreener(
                settings_on, evaluated_at=cutoff, feature_contract="discovery_v2"
            )
            await scr_on._evaluate_single(
                inst=inst,
                quote=q_sim,
                market=None,  # type: ignore[arg-type]
                derivatives_port=None,
                btc_candles=sub_btc,
                macro_context=MacroSentimentContext(observed_at=cutoff),
                mode=ScreenerMode.ACCUMULATION,
                target_direction=Direction.LONG,
                preloaded_candles=(sub_1h, sub_4h, c1w),
            )
            if sym in scr_on._recovery_ready_scores and rec_first_offset is None:
                rec_first_offset = -h
                rec_first_gain_to_t0 = round(
                    float((c1h[-1].close - p_cut) / p_cut * Decimal("100")), 2
                )

        if core_first_offset is not None:
            core_caught += 1
        if rec_first_offset is not None:
            recovery_stage1_caught += 1
        symbols_report[sym] = {
            "core_stage1_first_offset_hours": core_first_offset,
            "early_recovery_stage1_first_offset_hours": rec_first_offset,
            "early_recovery_gain_from_stage1_to_t0_pct": rec_first_gain_to_t0,
        }

    return {
        "available": True,
        "evidence_classification": "out_of_sample_stage1_ohlcv_diagnostic_no_historical_derivatives_archived",
        "t0_closed_at": t0_closed.isoformat(),
        "total_top_gainers_evaluated": len(symbols_report),
        "core_stage1_detected_count": core_caught,
        "early_recovery_stage1_ready_count": recovery_stage1_caught,
        "limitation": "Archive contains raw OHLCV klines without point-in-time historical OI/taker snapshots; Stage-1 (R1-R4) readiness only, never counted as 5/5 confirmed publications.",
        "symbols": symbols_report,
    }


async def run_all() -> dict[str, Any]:
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    rolling = await evaluate_rolling_study_archive()
    scan_archives = await evaluate_full_universe_scan_archives()
    nov23_forensic = await evaluate_nov23_forensic_stage1_archive()

    return {
        "schema": "atlas-early-recovery-evaluation-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "network_enabled": False,
        "performance_claims_permitted": False,
        "default_flag_off_verified": Settings(_env_file=None).early_recovery_enabled is False,
        "identity_flag_off": screening_identity(Settings(_env_file=None, early_recovery_enabled=False))[
            "policy"
        ],
        "identity_flag_on": screening_identity(Settings(_env_file=None, early_recovery_enabled=True))[
            "policy"
        ],
        "predeclared_config_sha256": sha256_file(
            ROOT
            / "docs"
            / "security"
            / "evidence"
            / "2026-11-24-early-recovery-predeclared-config.json"
        ),
        "rolling_study_402_frames": rolling,
        "full_universe_scan_archives": scan_archives,
        "nov23_out_of_sample_stage1_forensic": nov23_forensic,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    with asyncio.Runner() as runner, offline():
        report = runner.run(run_all())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    r = report["rolling_study_402_frames"]
    print("=== ROLLING 402-FRAME COMPARISON (ZERO NETWORK) ===")
    for k, v in r["arms"].items():
        print(
            f"  {k:38s} | uniq={v.get('unique_symbols', 0):2d} "
            f"| early_T10={v.get('early_top10_hits', 0):2d} (study48={v.get('early_top10_within_initial_study_48h', 0):2d}/{v.get('total_study_new_top10_denominator', 0)}) "
            f"| prec={v.get('early_top10_precision_pct')}% "
            f"| +24h={v.get('ret_24h_pct', {}).get('mean')}% (pos={v.get('ret_24h_pct', {}).get('positive_pct')}%) "
            f"| +48h={v.get('ret_48h_pct', {}).get('mean')}% (pos={v.get('ret_48h_pct', {}).get('positive_pct')}%) "
            f"| T1/Stop={v.get('barrier_simulation_5m_48h', {}).get('target_1_hits', 0)}/{v.get('barrier_simulation_5m_48h', {}).get('stop_hits', 0)}"
        )
    print("\n=== INCREMENTAL TRADEOFF (ARM B vs ARM A) ===")
    print(json.dumps(r["incremental_tradeoff_arm_b_vs_arm_a"], indent=2))
    print("\n=== POLICY ABLATION (FAMILY_V1 vs INHERITED_LONG) ===")
    print(json.dumps(r["policy_ablation_family_v1_vs_inherited_long"], indent=2))
    print("\n=== FULL UNIVERSE 320-SYMBOL SCAN ARCHIVES ===")
    for s in report["full_universe_scan_archives"]:
        print(
            f"  scan={s['scan_id'][:8]} | shortlist_identical={s['core_shortlist_45_identical']} ({s['core_shortlist_count']}) "
            f"| stage2_total={s['stage2_total_admitted_arm_c']} (<=75:{s['stage2_ceiling_respected_le_75']}) "
            f"| rec_s1_ready={s['recovery_stage1_ready_count']} | rec_probes={s['recovery_probe_admitted_count']} (<=8:{s['recovery_probe_cap_respected_le_8']}) "
            f"| displaced={len(s['displaced_baseline_probes'])}"
        )
    print("\n=== NOV 23 OUT-OF-SAMPLE TOP-GAINER STAGE-1 FORENSIC ===")
    f23 = report["nov23_out_of_sample_stage1_forensic"]
    print(
        f"  evaluated={f23.get('total_top_gainers_evaluated')} | core_stage1={f23.get('core_stage1_detected_count')} "
        f"| early_recovery_stage1={f23.get('early_recovery_stage1_ready_count')}"
    )


if __name__ == "__main__":
    main()
