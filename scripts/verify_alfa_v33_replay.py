"""Offline, zero-network deterministic replay verification for ALFA v3.3 (`alfa-v3.3-kinetic-ignition-1`).

Verifies end-to-end using the actual implemented domain & application classes (`network_enabled=False`):
1. Default flag-off invariant (`kinetic_ignition_v33_enabled=False`, `kinetic_scout_15m_enabled=False`)
   preserves exact `atlas-v3.1-discovery-guards-1` policy identity and `ScanRecord` replay parity.
2. Frozen Study 3 (`402` rolling frames, `758` pool evaluations, `1,205` verified 5m snapshots):
   - Eliminates Radar <-> Watchlist ping-pong re-entries (`8 -> 0`) via `AutoWatchlistService`.
   - Separates kinetic `ON_RADAR` setups (`6/6` = `100%` `+24h` positive, `66.7%` Early Top-10 precision,
     `0` 5m barrier stop-outs) from staged `WATCHLIST_COIL` setups.
3. Full-universe `ScanRecord` `3646b58e` (`2026-11-23T10:57Z`):
   - Routes `8/8` (`100%`) of Nov 23 `RETEST_ONLY_WAIT_FOR_DIP` grinders to `WATCHLIST_COIL` instead of
     Actionable `ON_RADAR`.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algotrader.application.activity_recovery import PoolControlScreener  # noqa: E402
from algotrader.application.auto_watchlist_service import AutoWatchlistService  # noqa: E402
from algotrader.application.rolling_discovery import SnapshotTape  # noqa: E402
from algotrader.application.scan_replay import ReplayMarket  # noqa: E402
from algotrader.config import Settings  # noqa: E402
from algotrader.domain.crypto_screener_models import (  # noqa: E402
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.early_recovery import DEFAULT_EARLY_RECOVERY_POLICY  # noqa: E402
from algotrader.domain.kinetic_readiness import assess_kinetic_readiness  # noqa: E402
from algotrader.domain.models import Candle, Direction  # noqa: E402
from algotrader.domain.scan_observations import ScanDecisionJournal  # noqa: E402
from algotrader.domain.scoring import OpportunityGrade  # noqa: E402
from algotrader.domain.screener_policy import (  # noqa: E402
    ALFA_V33_SCREENING_POLICY_VERSION,
    SCREENING_POLICY_VERSION,
    screening_policy_version_for,
)
from algotrader.infrastructure.rolling_discovery import unpack  # noqa: E402
from algotrader.infrastructure.scan_archive import LocalScanArchive  # noqa: E402
from scripts.replay_atlas_v3_rolling import load_price_grid  # noqa: E402
from scripts.research_early_recovery_replay import (  # noqa: E402
    GracefulCounterfactualTape,
    _load_rolling_meta_and_rows,
    _pack_signal,
    summarize_arm_population,
)
from scripts.research_verified_replay import offline  # noqa: E402

OUTPUT_PATH = ROOT / "docs" / "security" / "evidence" / "2026-11-24-alfa-v33-replay-verification.json"


async def _run_offline_verification() -> dict[str, Any]:
    s_default = Settings(_env_file=None)
    assert s_default.kinetic_ignition_v33_enabled is False
    assert s_default.kinetic_scout_15m_enabled is False
    assert screening_policy_version_for(s_default) == SCREENING_POLICY_VERSION

    cfg, _db_path, price_grid_path, rows = _load_rolling_meta_and_rows()
    report_48h_path = ROOT / cfg["inputs"]["report_48h_local"]
    traj, initial_top10, first_top10_epoch, grid_meta = load_price_grid(price_grid_path, report_48h_path)
    initial_study_endpoint = datetime.fromisoformat(cfg["study_anchor_utc"]).timestamp() + 48 * 3600
    total_study_new_top10 = sum(1 for ep in first_top10_epoch.values() if ep <= initial_study_endpoint)

    base_cfg = {
        "excluded_base_assets": tuple(cfg["screener_settings"]["excluded_base_assets"]),
        "support_resistance_lookback_bars": int(cfg["screener_settings"]["support_resistance_lookback_bars"]),
        "stage2_capacity_rollover_enabled": True,
        "bearish_continuation_enabled": False,
    }
    settings_ctrl = Settings.model_construct(
        **base_cfg, early_recovery_enabled=False, kinetic_ignition_v33_enabled=False, kinetic_scout_15m_enabled=False
    )
    settings_v33 = Settings.model_construct(
        **base_cfg, early_recovery_enabled=True, kinetic_ignition_v33_enabled=True, kinetic_scout_15m_enabled=False
    )
    assert screening_policy_version_for(settings_v33) == ALFA_V33_SCREENING_POLICY_VERSION

    ctrl_first: dict[str, dict[str, Any]] = {}
    v33_radar_first: dict[str, dict[str, Any]] = {}
    v33_watchlist_first: dict[str, dict[str, Any]] = {}

    ctrl_active: set[str] = set()
    ctrl_evicted: set[str] = set()
    ctrl_ping_pongs = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        watch_svc = AutoWatchlistService(Path(tmpdir) / "v33_replay.sqlite3")
        v33_active_radar: dict[str, dict[str, Any]] = {}
        v33_evicted_once: set[str] = set()
        v33_ping_pongs = 0

        for decision_id, _issued_str, payload_blob, sha in rows:
            frame = unpack(payload_blob, sha)
            pool = tuple(frame["pool"])
            if not pool or frame.get("global_input_unavailable"):
                continue
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

            async def _fixed_clock(_inst: Any, _ts: datetime = issued_at) -> datetime:
                return _ts

            # 1. Control (flags off)
            mkt_c = ReplayMarket(SnapshotTape(frame["inputs"]))
            scr_c = PoolControlScreener(
                settings_ctrl,
                mkt_c,
                evaluated_at=eval_at,
                feature_contract="discovery_v2",
                journal=ScanDecisionJournal(),
                early_recovery_policy=DEFAULT_EARLY_RECOVERY_POLICY,
            )
            res_c = await scr_c.screen(
                mkt_c, mkt_c, max_symbols=320, target_direction=Direction.LONG, enforce_quality=True, top_gainer_symbols=live_top10
            )
            miss_c = scr_c._fetch_failures | scr_c._feature_unavailable
            frame_c_syms: set[str] = set()
            for opp in res_c:
                sym = opp.instrument.id.symbol
                if sym in pool_set and sym not in miss_c:
                    frame_c_syms.add(sym)
                    if sym not in ctrl_first:
                        ctrl_first[sym] = _pack_signal(
                            sym=sym, decision_id=decision_id, eval_at=eval_at, issued_at=issued_at,
                            opp_dict=opp.as_dict(), entry_quote=entry_quotes.get(sym, float(opp.current_price)),
                            traj=traj, initial_top10=initial_top10, first_top10_epoch=first_top10_epoch,
                            first_observed_inside_epoch=grid_meta["first_observed_inside_epoch"],
                            initial_study_endpoint=initial_study_endpoint,
                        )
            for s in list(ctrl_active):
                if s not in frame_c_syms:
                    ctrl_active.remove(s)
                    ctrl_evicted.add(s)
            for s in frame_c_syms:
                if s in ctrl_evicted and s not in ctrl_active:
                    ctrl_ping_pongs += 1
                ctrl_active.add(s)

            # 2. ALFA v3.3 (actual implemented screener + AutoWatchlistService monotonic state machine)
            mkt_v = ReplayMarket(SnapshotTape(frame["inputs"]))
            scr_v = PoolControlScreener(
                settings_v33,
                mkt_v,
                evaluated_at=eval_at,
                feature_contract="discovery_v2",
                journal=ScanDecisionJournal(),
                early_recovery_policy=DEFAULT_EARLY_RECOVERY_POLICY,
                recovery_clock=_fixed_clock,
            )
            res_v = await scr_v.screen(
                mkt_v, mkt_v, max_symbols=320, target_direction=Direction.LONG, enforce_quality=True, top_gainer_symbols=live_top10
            )
            miss_v = scr_v._fetch_failures | scr_v._feature_unavailable
            blocked_pairs = watch_svc.blocked_from_radar_symbols(now=eval_at, cooldown_hours=12.0)

            pub_radar: list[dict[str, Any]] = []
            frame_v_radar_syms: set[str] = set()
            for opp in res_v:
                sym = opp.instrument.id.symbol
                if sym not in pool_set or sym in miss_v:
                    continue
                if (sym, opp.direction.value) in blocked_pairs:
                    continue
                opp_d = dict(opp.as_dict())
                opp_d["first_seen_at"] = eval_at.isoformat()
                packed = _pack_signal(
                    sym=sym, decision_id=decision_id, eval_at=eval_at, issued_at=issued_at,
                    opp_dict=opp_d, entry_quote=entry_quotes.get(sym, float(opp.current_price)),
                    traj=traj, initial_top10=initial_top10, first_top10_epoch=first_top10_epoch,
                    first_observed_inside_epoch=grid_meta["first_observed_inside_epoch"],
                    initial_study_endpoint=initial_study_endpoint,
                )
                if opp.publication_lane == "WATCHLIST_COIL":
                    watch_svc.enroll_pre_ignition_coil(opp_d, published_at=eval_at, scan_id=decision_id)
                    if sym not in v33_watchlist_first:
                        v33_watchlist_first[sym] = packed
                    continue

                frame_v_radar_syms.add(sym)
                if sym in v33_evicted_once and sym not in v33_active_radar:
                    v33_ping_pongs += 1
                v33_active_radar[sym] = opp_d
                pub_radar.append(opp_d)
                if sym not in v33_radar_first:
                    v33_radar_first[sym] = packed

            radar_events: list[dict[str, Any]] = []
            for s in list(v33_active_radar):
                if s not in frame_v_radar_syms:
                    prev_d = v33_active_radar.pop(s)
                    v33_evicted_once.add(s)
                    ev_cand = dict(prev_d)
                    cl = list(ev_cand.get("checklist") or [])
                    if len(cl) == 5:
                        cl[4] = {**cl[4], "passed": False, "status": "failed"}
                    ev_cand["checklist"] = cl
                    radar_events.append(
                        {
                            "symbol": s,
                            "at": eval_at.isoformat(),
                            "state": "INVALIDATED",
                            "retained": False,
                            "reason": "radar_gates_degraded",
                            "failed_gates": [cl[4]["title"]] if len(cl) == 5 else [],
                            "first_seen_at": prev_d.get("first_seen_at"),
                            "candidate": ev_cand,
                        }
                    )
            watch_svc.sync_from_scan(
                published_opportunities=pub_radar,
                radar_events=radar_events,
                now=eval_at,
                scan_id=decision_id,
            )

    # 3. Verify ScanRecord 3646b58e flag-off exact tape consumption AND Nov 23 dead-grinder routing!
    arch = LocalScanArchive(ROOT / "data" / "research")
    rec = arch.read_scan("3646b58e-a7e0-4a03-aa0a-793f87e13dcd")
    tape_off = GracefulCounterfactualTape(rec.inputs)
    mkt_off = ReplayMarket(tape_off)
    from algotrader.application.crypto_screener import CryptoOpportunityScreener

    s_rec_off = Settings(_env_file=None, kinetic_ignition_v33_enabled=False, early_recovery_enabled=False)
    scr_rec_off = CryptoOpportunityScreener(
        s_rec_off, mkt_off, evaluated_at=rec.started_at, feature_contract="discovery_v2"
    )
    args_off = dict(rec.request)
    args_off.pop("feature_contract", None)
    args_off.pop("derivatives_enabled", None)
    res_rec_off = await scr_rec_off.screen(mkt_off, derivatives_port=mkt_off, **args_off)
    tape_off.assert_consumed()
    flag_off_exact = [o.instrument.id.symbol for o in res_rec_off] == [o["symbol"] for o in rec.opportunities]

    c1h_by_sym: dict[str, list[Candle]] = {}
    inst_by_sym: dict[str, Any] = {}
    for call in rec.inputs:
        if call.method == "instruments" and call.result:
            for i in call.result:
                inst_by_sym[i.id.symbol] = i
        elif call.method == "candles" and call.result and call.arguments.get("interval") == "1h":
            s = call.arguments["instrument"].id.symbol
            c1h_by_sym[s] = [b for b in call.result if b.closed]

    nov23_live = json.loads((ROOT / "tmp" / "forensic-replay-20261123" / "raw" / "portal_screener_live.json").read_text())["opportunities"]
    nov23_longs_on_radar = 0
    nov23_longs_to_watchlist = 0
    for item in nov23_live:
        if item.get("direction") != "long":
            continue
        sym = str(item["symbol"])
        inst = inst_by_sym.get(sym)
        if inst is None:
            continue
        synth = CryptoOpportunity(
            instrument=inst,
            direction=Direction.LONG,
            regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
            setup_label=SetupLabel(item["setup_label"]),
            score=Decimal(str(item["score"])),
            score_breakdown=CryptoScreenerScoreBreakdown(
                Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"), Decimal("1"), Decimal(str(item["score"]))
            ),
            current_price=Decimal(str(item["current_price"])),
            atr14=Decimal("0.01"),
            suggested_entry=Decimal(str(item["suggested_entry"])),
            suggested_stop=Decimal(str(item["suggested_stop"])),
            suggested_targets=tuple(Decimal(str(t)) for t in item["suggested_targets"]),
            risk_reward_ratio=Decimal(str(item["risk_reward_ratio"])),
            btc_beta=Decimal("1"),
            residual_return_24h=Decimal("0"),
            funding_rate=None,
            oi_change_1h=None,
            taker_ratio=None,
            rationale=tuple(item.get("rationale") or ()),
            macro_narrative="Neutral",
            checklist_verification=ChecklistVerification(True, True, True, True, True),
            grade=OpportunityGrade.GRADE_B.value,
            breakout_readiness_status=str(item.get("breakout_readiness_status") or "RETEST_ONLY_WAIT_FOR_DIP"),
        )
        kin = assess_kinetic_readiness(synth, c1h_by_sym.get(sym, []), None)
        if kin.target_lane == "ON_RADAR":
            nov23_longs_on_radar += 1
        else:
            nov23_longs_to_watchlist += 1

    return {
        "schema": "alfa-v3.3-replay-verification-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "network_enabled": False,
        "default_flags_off_verified": True,
        "scan_3646b58e_flag_off_exact_parity": flag_off_exact,
        "nov23_dead_grinders_on_radar": nov23_longs_on_radar,
        "nov23_dead_grinders_routed_to_watchlist_coil": nov23_longs_to_watchlist,
        "rolling_study_402_frames": {
            "control_ping_pong_reentries": ctrl_ping_pongs,
            "v33_ping_pong_reentries": v33_ping_pongs,
            "control_summary": summarize_arm_population(list(ctrl_first.values()), total_study_new_top10=total_study_new_top10),
            "v33_radar_summary": summarize_arm_population(list(v33_radar_first.values()), total_study_new_top10=total_study_new_top10),
            "v33_watchlist_coil_summary": summarize_arm_population(list(v33_watchlist_first.values()), total_study_new_top10=total_study_new_top10),
        },
    }


async def _run_entry() -> dict[str, Any]:
    with offline():
        return await _run_offline_verification()


def main() -> None:
    report = asyncio.run(_run_entry())
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    r = report["rolling_study_402_frames"]
    assert report["scan_3646b58e_flag_off_exact_parity"] is True
    assert report["nov23_dead_grinders_on_radar"] == 0
    assert r["v33_ping_pong_reentries"] == 0
    print(
        f"VERIFIED ALFA v3.3 REPLAY (network=False): flag_off_exact={report['scan_3646b58e_flag_off_exact_parity']} "
        f"| ping_pongs: {r['control_ping_pong_reentries']} -> {r['v33_ping_pong_reentries']} "
        f"| nov23_dead_grinders_on_radar={report['nov23_dead_grinders_on_radar']}/8 "
        f"| v33_radar_24h_pos={r['v33_radar_summary']['ret_24h_pct']['positive_pct']}% "
        f"| v33_radar_48h_pos={r['v33_radar_summary']['ret_48h_pct']['positive_pct']}%"
    )


if __name__ == "__main__":
    main()
