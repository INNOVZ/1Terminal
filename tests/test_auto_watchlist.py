from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.domain.auto_watchlist import (
    AutoWatchStatus,
    build_auto_watch_episode,
    classify_radar_eviction,
    evaluate_auto_watch_episode,
)
from algotrader.domain.models import Candle, Quote, Venue
from tests.conftest import make_instrument

T0 = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


def _make_candle(
    opened_at: datetime,
    *,
    o: str = "100",
    h: str = "101",
    low: str = "99",
    c: str = "100",
    closed: bool = True,
) -> Candle:
    return Candle(
        opened_at=opened_at,
        closed_at=opened_at + timedelta(hours=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1000"),
        closed=closed,
    )


def _five_gate_checklist(*, failed_titles: set[str] | None = None, unknown_titles: set[str] | None = None) -> list[dict]:
    failed = failed_titles or set()
    unknown = unknown_titles or set()
    titles = (
        "Trend Baseline",
        "Anti-Chasing Validated",
        "Value Acceptance",
        "Volatility Compression",
        "Smart Money Absorption",
    )
    out = []
    for title in titles:
        if title in unknown:
            out.append({"title": title, "passed": False, "status": "unknown", "detail": "Unavailable"})
        elif title in failed:
            out.append({"title": title, "passed": False, "status": "failed", "detail": f"{title} weakened"})
        else:
            out.append({"title": title, "passed": True, "status": "passed", "detail": f"{title} intact"})
    return out


def _sample_candidate(
    symbol: str = "USUALUSDT",
    *,
    direction: str = "long",
    execution_mode: str = "RETEST_ACCUMULATION",
    setup_label: str = "VCP_COMPRESSION_LONG",
    published_at: datetime = T0,
    signal_price: str = "100",
    entry: str = "98",
    trigger: str = "104",
    stop: str = "92",
    t1: str = "112",
    episode_id: str | None = None,
    gates_passed: int = 5,
) -> dict:
    failed = set() if gates_passed == 5 else {"Smart Money Absorption"}
    return {
        "symbol": symbol,
        "direction": direction,
        "setup_label": setup_label,
        "execution_mode": execution_mode,
        "score": "88.5",
        "tick_size": "0.01",
        "current_price": signal_price,
        "suggested_entry": entry,
        "breakout_trigger": trigger,
        "suggested_stop": stop,
        "suggested_targets": [t1, "125" if direction == "long" else "75"],
        "poc_price": "99",
        "vah_price": "105",
        "val_price": "95",
        "evaluated_at": published_at.isoformat(),
        "first_seen_at": published_at.isoformat(),
        "radar_status_label": "FRESH COIL",
        "checklist": _five_gate_checklist(failed_titles=failed),
        "radar_signal": {
            "version": 1,
            "episode_id": episode_id or f"ep-{symbol}-{int(published_at.timestamp())}",
            "published_at": published_at.isoformat(),
            "price": signal_price,
            "quote_observed_at": published_at.isoformat(),
        },
    }


def test_only_verified_five_of_five_candidates_enroll_in_on_radar_state():
    bad = _sample_candidate(gates_passed=4)
    assert build_auto_watch_episode(bad) is None
    good = _sample_candidate(gates_passed=5)
    ep = build_auto_watch_episode(good)
    assert ep is not None
    assert ep.status is AutoWatchStatus.ON_RADAR
    assert not ep.is_watchlist  # Never duplicated in Watchlist while ON_RADAR
    assert ep.expires_at == T0 + timedelta(hours=48)
    assert ep.signal_price == Decimal("100")


def test_classify_radar_eviction_separates_soft_flow_dip_from_hard_invalidation():
    ep = build_auto_watch_episode(_sample_candidate(symbol="HMSTRUSDT"))
    assert ep is not None

    # 1. Soft flow dip (HMSTRUSDT pattern: 4/4 structure intact, taker 0.9016, +20.6% 7d OI) -> WATCHLIST
    soft_event = {
        "symbol": "HMSTRUSDT",
        "state": "INVALIDATED",
        "retained": False,
        "reason": "radar_gates_degraded",
        "candidate": {
            "current_price": "101",
            "checklist": _five_gate_checklist(failed_titles={"Smart Money Absorption"}),
            "radar_metrics": {
                "taker_buy_sell_ratio": "0.9016",
                "oi_change_7d_pct": "+20.60%",
                "oi_change_1h_pct": "-0.43%",
                "funding_rate_bps": "+0.50 bps",
                "volume_contraction_ratio": "1.42",
            },
        },
    }
    status, _, summary, cl = classify_radar_eviction(ep, event=soft_event, now=T0 + timedelta(hours=1))
    assert status is AutoWatchStatus.WATCHLIST
    assert summary is not None and "Taker 0.90" in summary and "+20.6%" in summary
    assert sum(1 for c in cl if c["passed"]) == 4  # Honest 4/5 checklist preserved

    # 2. Severe seller dump (taker 0.65 < 0.78) -> HARD_INVALIDATED (must NOT enter Watchlist)
    dump_event = {
        **soft_event,
        "candidate": {
            **soft_event["candidate"],
            "radar_metrics": {**soft_event["candidate"]["radar_metrics"], "taker_buy_sell_ratio": "0.65"},
        },
    }
    dump_status, dump_reason, _, _ = classify_radar_eviction(ep, event=dump_event, now=T0 + timedelta(hours=1))
    assert dump_status is AutoWatchStatus.HARD_INVALIDATED
    assert "severe_seller_dump" in dump_reason

    # 3. Structural gate failure (Trend Baseline lost) -> HARD_INVALIDATED (must NOT enter Watchlist)
    struct_event = {
        **soft_event,
        "candidate": {
            **soft_event["candidate"],
            "checklist": _five_gate_checklist(failed_titles={"Trend Baseline"}),
        },
    }
    struct_status, struct_reason, _, _ = classify_radar_eviction(ep, event=struct_event, now=T0 + timedelta(hours=1))
    assert struct_status is AutoWatchStatus.HARD_INVALIDATED
    assert "structural_gate_failed" in struct_reason

    # 4. Unknown telemetry -> HARD_INVALIDATED (never guesses structure is intact)
    unk_event = {
        **soft_event,
        "candidate": {
            **soft_event["candidate"],
            "checklist": _five_gate_checklist(unknown_titles={"Smart Money Absorption"}),
        },
    }
    unk_status, _, _, _ = classify_radar_eviction(ep, event=unk_event, now=T0 + timedelta(hours=1))
    assert unk_status is AutoWatchStatus.HARD_INVALIDATED


def test_target_reached_immediately_resolves_and_ignores_pre_publication_bars():
    ep = build_auto_watch_episode(_sample_candidate())
    assert ep is not None
    candles = [
        _make_candle(T0 - timedelta(hours=2), h="120", low="85", c="100"),
        _make_candle(T0, o="100", h="101", low="99", c="100.5"),
        _make_candle(T0 + timedelta(hours=1), o="100.5", h="103", low="97.5", c="102"),
        _make_candle(T0 + timedelta(hours=2), o="102", h="113", low="101", c="111"),
    ]
    resolved = evaluate_auto_watch_episode(
        ep,
        candles=candles,
        live_price=Decimal("108"),
        live_observed_at=T0 + timedelta(hours=3),
        now=T0 + timedelta(hours=3),
    )
    assert resolved.status is AutoWatchStatus.TARGET_REACHED
    assert resolved.is_resolved
    assert resolved.barrier_order_certified
    assert resolved.resolved_price == Decimal("112")
    assert resolved.resolved_return_pct == Decimal("12.00")
    assert resolved.retest_touched is True
    assert resolved.breakout_touched is True
    assert resolved.peak_mfe_pct == Decimal("13.00")
    assert resolved.worst_mae_pct == Decimal("-2.50")


def test_stop_breached_takes_conservative_priority_in_same_bar():
    ep = build_auto_watch_episode(_sample_candidate())
    assert ep is not None
    candles = [_make_candle(T0, o="100", h="115", low="90", c="105")]
    resolved = evaluate_auto_watch_episode(
        ep, candles=candles, live_price=Decimal("106"), live_observed_at=T0 + timedelta(hours=1), now=T0 + timedelta(hours=1)
    )
    assert resolved.status is AutoWatchStatus.STOP_BREACHED
    assert resolved.resolution_reason == "stop_and_target_same_bar_conservative_stop"
    assert resolved.resolved_price == Decimal("92")
    assert resolved.resolved_return_pct == Decimal("-8.00")


def test_expired_48h_immediately_resolves_after_window():
    ep = build_auto_watch_episode(_sample_candidate())
    assert ep is not None
    candles = [
        _make_candle(T0 + timedelta(hours=h), o="100", h="106" if h == 10 else "104", low="98.5", c="103.5")
        for h in range(48)
    ] + [_make_candle(T0 + timedelta(hours=50), o="103.5", h="120", low="90", c="115")]
    resolved = evaluate_auto_watch_episode(
        ep, candles=candles, live_price=Decimal("115"), live_observed_at=T0 + timedelta(hours=51), now=T0 + timedelta(hours=51)
    )
    assert resolved.status is AutoWatchStatus.EXPIRED_48H
    assert resolved.is_resolved
    assert resolved.coverage_complete
    assert resolved.resolved_at == T0 + timedelta(hours=48)
    assert resolved.resolved_price == Decimal("103.5")
    assert resolved.resolved_return_pct == Decimal("3.50")
    assert resolved.peak_mfe_pct == Decimal("6.00")


def test_short_direction_live_target_resolution():
    short_cand = _sample_candidate(
        symbol="OUSDT",
        direction="short",
        setup_label="BREAKDOWN_RETEST_SHORT",
        signal_price="100",
        entry="101",
        trigger="96",
        stop="108",
        t1="88",
    )
    ep = build_auto_watch_episode(short_cand)
    assert ep is not None
    hit = evaluate_auto_watch_episode(
        ep,
        candles=[_make_candle(T0 + timedelta(hours=h), o="100", h="100.5", low="95", c="96") for h in range(3)],
        live_price=Decimal("87"),
        live_observed_at=T0 + timedelta(hours=3, minutes=5),
        now=T0 + timedelta(hours=3, minutes=5),
    )
    assert hit.status is AutoWatchStatus.TARGET_REACHED
    assert hit.resolved_return_pct == Decimal("13.00")
    assert hit.breakout_touched is True


@pytest.mark.asyncio
async def test_disjoint_lifecycle_zero_duplication_soft_promotion_and_radar_reentry(tmp_path: Path):
    db_path = tmp_path / "auto_watch.sqlite3"
    events_dir = tmp_path / "radar_events"
    events_dir.mkdir()

    # Scan 1 (T0): HMSTRUSDT and USUALUSDT both live 5/5 on Radar -> NEITHER appears in Watchlist!
    hmstr = _sample_candidate(symbol="HMSTRUSDT", published_at=T0, episode_id="ep-hmstr")
    usual = _sample_candidate(symbol="USUALUSDT", published_at=T0, episode_id="ep-usual")
    svc = AutoWatchlistService(db_path)
    svc.sync_from_scan(published_opportunities=[hmstr, usual], radar_events=[], now=T0, scan_id="s1")

    r1 = svc.build_response(live_radar_symbols=["HMSTRUSDT", "USUALUSDT"], now=T0)
    assert r1["watchlist"] == []  # ZERO duplication with live 5/5 Radar!
    assert {e["symbol"] for e in r1["on_radar"]} == {"HMSTRUSDT", "USUALUSDT"}

    # Scan 2 (T0+1h): HMSTRUSDT soft-evicted (taker 0.90, 4/4 structure intact); USUALUSDT stays on Radar
    hmstr_eviction = {
        "symbol": "HMSTRUSDT",
        "state": "INVALIDATED",
        "retained": False,
        "reason": "radar_gates_degraded",
        "candidate": {
            "current_price": "101",
            "checklist": _five_gate_checklist(failed_titles={"Smart Money Absorption"}),
            "radar_metrics": {"taker_buy_sell_ratio": "0.90", "oi_change_7d_pct": "+20.6%", "oi_change_1h_pct": "-0.4%", "funding_rate_bps": "+0.5 bps"},
        },
    }
    svc.sync_from_scan(
        published_opportunities=[usual],
        radar_events=[hmstr_eviction],
        now=T0 + timedelta(hours=1),
        scan_id="s2",
    )
    r2 = svc.build_response(live_radar_symbols=["USUALUSDT"], now=T0 + timedelta(hours=1))
    assert [e["symbol"] for e in r2["watchlist"]] == ["HMSTRUSDT"]
    assert r2["watchlist"][0]["gates_passed"] == 4
    assert r2["watchlist"][0]["was_promoted_to_watchlist"] is True
    assert [e["symbol"] for e in r2["on_radar"]] == ["USUALUSDT"]

    # Scan 3 (T0+2h): HMSTRUSDT hits Target 1 -> immediately removed from Watchlist into Resolved!
    inst_h = make_instrument(symbol="HMSTRUSDT", venue=Venue.BINANCE_USDM)
    inst_u = make_instrument(symbol="USUALUSDT", venue=Venue.BINANCE_USDM)
    now_check = T0 + timedelta(hours=2)
    market = SimpleNamespace(
        quotes=AsyncMock(
            return_value=[
                Quote(inst_h.id, Decimal("112.9"), Decimal("113.1"), Decimal("113"), Decimal("100000"), now_check),
                Quote(inst_u.id, Decimal("102.9"), Decimal("103.1"), Decimal("103"), Decimal("100000"), now_check),
            ]
        ),
        candles=AsyncMock(
            side_effect=lambda inst, tf, limit: [
                _make_candle(T0, o="100", h="103", low="99", c="102"),
                _make_candle(T0 + timedelta(hours=1), o="102", h="113" if inst.id.symbol == "HMSTRUSDT" else "104", low="101", c="112.5" if inst.id.symbol == "HMSTRUSDT" else "103"),
            ]
        ),
    )
    await svc.refresh_and_evaluate(market, [inst_h, inst_u], force_refresh=True, internal_scheduler=True, now=now_check)

    reopened = AutoWatchlistService(db_path).build_response(live_radar_symbols=["USUALUSDT"], now=now_check)
    assert reopened["watchlist"] == []  # Immediately removed upon TARGET_REACHED!
    assert [e["symbol"] for e in reopened["resolved"]] == ["HMSTRUSDT"]
    assert reopened["resolved"][0]["status"] == AutoWatchStatus.TARGET_REACHED.value
    assert reopened["resolved"][0]["barrier_order_certified"] is True
    assert reopened["evicted_from_radar_cohort"]["target_reached"] == 1
    assert reopened["summary"]["target_hit_rate_pct"] == 100.0
