"""Permanent CI Cross-Layer Lifecycle Parity Contract Suite.

Guarantees that EVERY production `SetupLabel` archetype maintains strict end-to-end
contract alignment across all 4 system layers:
  1. `T=0` Admission & Publication Policy (`screener_policy.py`)
  2. Kinetic Lane Routing (`kinetic_readiness.py`)
  3. `T+20m` & `T+2h` Multi-Hour Retention Re-Verification (`radar_lifecycle.py`)
     — including non-contradictory UI badge text on passing gates (`0.20 < bbw <= 0.24`)
     — and `VCP_DISTRIBUTION_SHORT` when `poc_30d < close <= ema50_4h`
  4. `48h` Auto-Watchlist Soft-Flow Demotion & Monotonic Lock (`auto_watchlist.py` / `auto_watchlist_service.py`)
     — covering both `T=0` Screener checklist titles ("Trapped Longs / Funding", "Derivatives Flow")
     — and `T+1h` Retention checklist titles ("Smart Money Absorption", "Positioning Confirmation", etc.)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.domain.auto_watchlist import AutoWatchStatus, classify_radar_eviction
from algotrader.domain.bearish_continuation import TITLES as CONTINUATION_TITLES
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.early_recovery import TITLES as RECOVERY_TITLES
from algotrader.domain.radar_lifecycle import evaluate_radar_candidate_lifecycle

_FAILURE_PHRASES = (
    "without upside breakout",
    "orderflow weakened",
    "slipped",
    "squeezed above climax",
    "broke above climax",
    "lost emerging base",
    "excursion wide",
)


@pytest.mark.parametrize(
    ("setup_label", "below_poc", "bbw"),
    [
        (SetupLabel.VCP_COMPRESSION_LONG.value, False, "0.14"),
        (SetupLabel.COIL_SPRING_LONG.value, False, "0.12"),
        (SetupLabel.OI_ACCUMULATION_LONG.value, False, "0.18"),
        (SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG.value, True, "0.22"),
        (SetupLabel.MOMENTUM_EMERGING_LONG.value, True, "0.21"),
        (SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG.value, False, "0.23"),
        (SetupLabel.BREAKOUT_LONG.value, False, "0.19"),
    ],
)
def test_all_long_archetypes_hold_end_to_end_retention_and_watchlist_parity(
    tmp_path: Path,
    setup_label: str,
    below_poc: bool,
    bbw: str,
) -> None:
    now = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    px = Decimal("0.9600") if below_poc else Decimal("1.0200")
    cand = {
        "symbol": "PARITYUSDT",
        "direction": "long",
        "setup_label": setup_label,
        "execution_mode": "RETEST_ACCUMULATION" if below_poc else "CEILING_BREAKOUT",
        "score": "88.0",
        "current_price": str(px),
        "discovery_price": str(px),
        "suggested_entry": str(px * Decimal("0.99")),
        "breakout_trigger": str(px * Decimal("1.04")),
        "suggested_stop": str(px * Decimal("0.93")),
        "suggested_targets": [str(px * Decimal("1.12")), str(px * Decimal("1.20"))],
        "poc_price": "1.0000",
        "val_price": "0.9450",
        "vah_price": "1.0500",
        "evaluated_at": (now - timedelta(minutes=20)).isoformat(),
        "radar_metrics": {
            "bb_bandwidth_4h": bbw,
            "oi_change_7d_pct": "+16.0%",
            "oi_change_1h_pct": "+0.5%",
            "funding_rate_bps": "-2.00 bps",
            "taker_buy_sell_ratio": "1.14",
            "volume_contraction_ratio": "1.80",
        },
    }
    candles_1h = [
        {
            "time": int((now - timedelta(hours=72 - i)).timestamp()),
            "open": float(px) * 0.99,
            "high": float(px) * 1.01,
            "low": float(px) * 0.98,
            "close": float(px),
            "volume": 1000,
        }
        for i in range(72)
    ]

    # 1. T+20m & constructive volume (vcr=1.80, bbw up to 0.23): 5/5 retained + zero contradictory failure strings
    dec = evaluate_radar_candidate_lifecycle(
        cand,
        current_price=px,
        recent_candles_1h=candles_1h,
        ema50_4h=px * Decimal("0.97"),
        now=now,
    )
    assert dec.retained is True
    assert dec.gates_passed == 5
    assert dec.failed_gates == ()
    for item in dec.updated_checklist:
        assert item["passed"] is True
        detail_lower = str(item["detail"]).lower()
        assert not any(bad in detail_lower for bad in _FAILURE_PHRASES), (
            f"Passing gate {item['title']} emitted contradictory failure text: {item['detail']}"
        )

    # 2. T+4h soft flow dip (taker drops to 0.90) -> demotes to WATCHLIST and locks monotonically
    cand_dip = {
        **cand,
        "evaluated_at": (now - timedelta(hours=4)).isoformat(),
        "radar_metrics": {**cand["radar_metrics"], "taker_buy_sell_ratio": "0.90"},
    }
    dec_dip = evaluate_radar_candidate_lifecycle(
        cand_dip,
        current_price=px,
        recent_candles_1h=candles_1h,
        ema50_4h=px * Decimal("0.97"),
        now=now,
    )
    assert dec_dip.gates_passed == 4
    assert dec_dip.failed_gates == ("Smart Money Absorption",)

    svc = AutoWatchlistService(tmp_path / f"{setup_label}.sqlite3")
    enrolled = svc.enroll_candidate(
        {**cand, "checklist": list(dec.updated_checklist)},
        published_at=now - timedelta(hours=4),
        scan_id="scan-t0",
    )
    assert enrolled is not None
    ev = {
        "symbol": "PARITYUSDT",
        "state": "INVALIDATED",
        "retained": False,
        "reason": "radar_gates_degraded",
        "failed_gates": list(dec_dip.failed_gates),
        "candidate": {**cand_dip, "checklist": list(dec_dip.updated_checklist)},
    }
    svc.sync_from_scan(published_opportunities=[], radar_events=[ev], now=now, scan_id="scan-t4")
    resp = svc.build_response(days=7, now=now)
    assert [w["symbol"] for w in resp["watchlist"]] == ["PARITYUSDT"]
    assert ("PARITYUSDT", "long") in svc.blocked_from_radar_symbols(now=now + timedelta(hours=1))


@pytest.mark.parametrize(
    ("setup_label", "px_str", "ema50_str", "poc_str", "bbw", "is_climax"),
    [
        # Climax / UTAD shorts above VAH, POC, and EMA50 with wide bandwidth
        (SetupLabel.EXHAUSTION_REVERSAL_SHORT.value, "1.2600", "0.9400", "0.9800", "0.38", True),
        (SetupLabel.UTAD_DIVERGENCE_SHORT.value, "1.1800", "0.9600", "1.0000", "0.31", True),
        # Macro breakdown below both EMA50 and POC
        (SetupLabel.DISTRIBUTION_SHORT.value, "0.9700", "1.0200", "1.0100", "0.16", False),
        # VCP_DISTRIBUTION_SHORT below both EMA50 and POC
        (SetupLabel.VCP_DISTRIBUTION_SHORT.value, "0.9750", "1.0150", "1.0050", "0.14", False),
        # VCP_DISTRIBUTION_SHORT anchored below EMA50 while 30d POC sits lower (poc < close <= ema50)
        (SetupLabel.VCP_DISTRIBUTION_SHORT.value, "1.0200", "1.0350", "0.9600", "0.15", False),
    ],
)
def test_all_short_archetypes_hold_retention_and_t0_plus_t1h_watchlist_title_parity(
    setup_label: str,
    px_str: str,
    ema50_str: str,
    poc_str: str,
    bbw: str,
    is_climax: bool,
) -> None:
    now = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    px = Decimal(px_str)
    cand = {
        "symbol": "SHORTPARITYUSDT",
        "direction": "short",
        "setup_label": setup_label,
        "execution_mode": "RETEST_ACCUMULATION",
        "score": "86.5",
        "current_price": px_str,
        "discovery_price": px_str,
        "suggested_entry": str(px * Decimal("1.005")),
        "breakout_trigger": str(px * Decimal("0.960")),
        "suggested_stop": str(px * Decimal("1.055")),
        "suggested_targets": [str(px * Decimal("0.880")), str(px * Decimal("0.800"))],
        "poc_price": poc_str,
        "val_price": "0.8800",
        "vah_price": "1.0600",
        "evaluated_at": (now - timedelta(minutes=20)).isoformat(),
        "radar_metrics": {
            "bb_bandwidth_4h": bbw,
            "oi_change_7d_pct": "+11.0%",
            "oi_change_1h_pct": "-0.6%",
            "funding_rate_bps": "+3.20 bps",
            "taker_buy_sell_ratio": "0.88",
            "volume_contraction_ratio": "1.60" if is_climax else "0.92",
        },
    }
    candles_1h = [
        {
            "time": int((now - timedelta(hours=72 - i)).timestamp()),
            "open": 0.90 if (is_climax and i < 50) else float(px),
            "high": 0.92 if (is_climax and i < 50) else float(px) * 1.01,
            "low": 0.88 if (is_climax and i < 50) else float(px) * 0.99,
            "close": 0.91 if (is_climax and i < 50) else float(px),
            "volume": 1000,
        }
        for i in range(72)
    ]

    dec = evaluate_radar_candidate_lifecycle(
        cand,
        current_price=px,
        recent_candles_1h=candles_1h,
        ema50_4h=Decimal(ema50_str),
        now=now,
    )
    assert dec.retained is True, f"{setup_label} failed retention: {dec}"
    assert dec.gates_passed == 5
    assert dec.failed_gates == ()

    # Verify BOTH T+1h retention checklist ("Smart Money Absorption") AND T=0 Screener checklist
    # ("Trapped Longs / Funding") soft-dip cleanly to WATCHLIST!
    svc_mem = AutoWatchlistService(Path(":memory:"))
    del svc_mem
    from algotrader.domain.auto_watchlist import build_auto_watch_episode

    ep = build_auto_watch_episode(
        {**cand, "checklist": list(dec.updated_checklist)},
        published_at=now - timedelta(hours=1),
    )
    assert ep is not None

    # Case A: T+1h retention soft flow dip (taker=1.28)
    cand_dip = {**cand, "radar_metrics": {**cand["radar_metrics"], "taker_buy_sell_ratio": "1.28"}}
    dec_dip = evaluate_radar_candidate_lifecycle(
        cand_dip,
        current_price=px,
        recent_candles_1h=candles_1h,
        ema50_4h=Decimal(ema50_str),
        now=now,
    )
    st_ret, _, _, _ = classify_radar_eviction(
        ep,
        event={
            "state": "INVALIDATED",
            "reason": "radar_gates_degraded",
            "candidate": {**cand_dip, "checklist": list(dec_dip.updated_checklist)},
        },
        now=now,
    )
    assert st_ret is AutoWatchStatus.WATCHLIST

    # Case B: T=0 Screener-titled Short checklist where only "Trapped Longs / Funding" failed
    t0_short_cl = [
        {"title": "Climax Run-Up", "passed": True, "status": "passed", "detail": "ok"},
        {"title": "MTF Overbought Climax", "passed": True, "status": "passed", "detail": "ok"},
        {"title": "MACD Bearish Crossover", "passed": True, "status": "passed", "detail": "ok"},
        {"title": "Volume & Momentum Exhaustion", "passed": True, "status": "passed", "detail": "ok"},
        {"title": "Trapped Longs / Funding", "passed": False, "status": "failed", "detail": "taker rose"},
    ]
    st_t0, _, _, _ = classify_radar_eviction(
        ep,
        event={
            "state": "INVALIDATED",
            "reason": "radar_gates_degraded",
            "candidate": {**cand_dip, "checklist": t0_short_cl},
        },
        now=now,
    )
    assert st_t0 is AutoWatchStatus.WATCHLIST


def test_special_families_recovery_and_continuation_watchlist_contract_parity() -> None:
    """Verify RECOVERY_RECLAIM_LONG and BREAKDOWN_RETEST_SHORT 5-gate contracts align across layers."""
    from algotrader.domain.auto_watchlist import build_auto_watch_episode

    now = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    # 1. RECOVERY_RECLAIM_LONG
    rec_cand = {
        "symbol": "RECUSDT",
        "direction": "long",
        "setup_label": SetupLabel.RECOVERY_RECLAIM_LONG.value,
        "execution_mode": "CEILING_BREAKOUT",
        "score": "79.0",
        "current_price": "0.1150",
        "suggested_entry": "0.1160",
        "breakout_trigger": "0.1160",
        "suggested_stop": "0.1090",
        "suggested_targets": ["0.1300", "0.1400"],
        "evaluated_at": (now - timedelta(hours=1)).isoformat(),
        "radar_metrics": {"taker_buy_sell_ratio": "0.92", "oi_change_4h_pct": "+0.8%", "funding_rate_bps": "+0.5bps"},
    }
    dec_rec = evaluate_radar_candidate_lifecycle(
        rec_cand,
        current_price=Decimal("0.1150"),
        recovery_gates=(True, True, True, True, False),
        recovery_details=("ok", "ok", "ok", "ok", "soft dip"),
        now=now,
    )
    assert dec_rec.gates_passed == 4
    assert dec_rec.failed_gates == (RECOVERY_TITLES[4],)
    ep_rec = build_auto_watch_episode(
        {**rec_cand, "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in RECOVERY_TITLES]},
        published_at=now - timedelta(hours=1),
    )
    assert ep_rec is not None
    st_rec, _, _, _ = classify_radar_eviction(
        ep_rec,
        event={"state": "INVALIDATED", "reason": "radar_gates_degraded", "candidate": {**rec_cand, "checklist": list(dec_rec.updated_checklist)}},
        now=now,
    )
    assert st_rec is AutoWatchStatus.WATCHLIST

    # 2. BREAKDOWN_RETEST_SHORT
    cont_cand = {
        "symbol": "CONTUSDT",
        "direction": "short",
        "setup_label": SetupLabel.BREAKDOWN_RETEST_SHORT.value,
        "execution_mode": "RETEST_ACCUMULATION",
        "score": "81.0",
        "current_price": "0.9500",
        "suggested_entry": "0.9550",
        "breakout_trigger": "0.9350",
        "suggested_stop": "0.9900",
        "suggested_targets": ["0.8800", "0.8400"],
        "evaluated_at": (now - timedelta(hours=1)).isoformat(),
        "radar_metrics": {"taker_buy_sell_ratio": "1.10", "funding_rate_bps": "+1.0bps"},
    }
    dec_cont = evaluate_radar_candidate_lifecycle(
        cont_cand,
        current_price=Decimal("0.9500"),
        continuation_gates=(True, True, True, True, False),
        now=now,
    )
    assert dec_cont.gates_passed == 4
    assert dec_cont.failed_gates == (CONTINUATION_TITLES[4],)
    ep_cont = build_auto_watch_episode(
        {**cont_cand, "checklist": [{"title": t, "passed": True, "status": "passed", "detail": "ok"} for t in CONTINUATION_TITLES]},
        published_at=now - timedelta(hours=1),
    )
    assert ep_cont is not None
    st_cont, _, _, _ = classify_radar_eviction(
        ep_cont,
        event={"state": "INVALIDATED", "reason": "radar_gates_degraded", "candidate": {**cont_cand, "checklist": list(dec_cont.updated_checklist)}},
        now=now,
    )
    assert st_cont is AutoWatchStatus.WATCHLIST


def test_sub_ema50_emerging_and_short_squeeze_hold_t1h_retention_parity() -> None:
    """SSVUSDT / MANTRAUSDT / SEIUSDT regression: sub-EMA50 local base reclaims must not fail Trend Baseline at T+1h when holding discovery base."""
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    for lbl in (
        SetupLabel.MOMENTUM_EMERGING_LONG.value,
        SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG.value,
    ):
        cand = {
            "symbol": "SSVUSDT",
            "direction": "long",
            "setup_label": lbl,
            "execution_mode": "EXECUTABLE",
            "score": "84.0",
            "current_price": "1.6100",
            "discovery_price": "1.5950",
            "suggested_entry": "1.5950",
            "breakout_trigger": "1.6100",
            "suggested_stop": "1.5220",
            "suggested_targets": ["1.7410", "1.8140", "1.9016"],
            "poc_price": "2.0910",
            "val_price": "1.5400",
            "vah_price": "2.5220",
            "evaluated_at": (now - timedelta(hours=1)).isoformat(),
            "radar_metrics": {
                "bb_bandwidth_4h": "0.078",
                "oi_change_7d_pct": "-10.8%" if lbl == SetupLabel.MOMENTUM_EMERGING_LONG.value else "+14.0%",
                "oi_change_1h_pct": "+0.4%",
                "funding_rate_bps": "-1.20 bps",
                "taker_buy_sell_ratio": "1.24",
                "volume_contraction_ratio": "1.45",
            },
        }
        candles_1h = [
            {
                "time": int((now - timedelta(hours=72 - i)).timestamp()),
                "open": 1.58,
                "high": 1.62,
                "low": 1.56,
                "close": 1.61,
                "volume": 1000,
            }
            for i in range(72)
        ]
        # Notice 4H EMA50 is 1.6782 (> current_price 1.6100), yet price is in profit above discovery_price 1.5950!
        dec = evaluate_radar_candidate_lifecycle(
            cand,
            current_price=Decimal("1.6100"),
            recent_candles_1h=candles_1h,
            ema50_4h=Decimal("1.6782"),
            now=now,
        )
        assert dec.retained is True, f"{lbl} falsely failed sub-EMA50 retention: {dec}"
        assert dec.gates_passed == 5
        assert dec.failed_gates == ()


