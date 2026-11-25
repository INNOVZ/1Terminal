"""Tests for Task 4: Monotonic Watchlist <-> Radar state machine and anti-ping-pong cooldown."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.domain.auto_watchlist import AutoWatchStatus, RadarPresence


def _cand(
    symbol: str = "PROMUSDT",
    *,
    first_seen: datetime,
    price: str = "5.10",
    lane: str = "ON_RADAR",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "execution_mode": "RETEST_ACCUMULATION",
        "score": "85.0",
        "tick_size": "0.001",
        "first_seen_at": first_seen.isoformat(),
        "evaluated_at": first_seen.isoformat(),
        "current_price": price,
        "suggested_entry": "5.00",
        "breakout_trigger": "5.25",
        "suggested_stop": "4.60",
        "suggested_targets": ["5.80", "6.20", "6.80"],
        "publication_lane": lane,
        "checklist": [
            {"title": "Trend Baseline", "passed": True, "status": "passed", "detail": "ok"},
            {"title": "Anti-Chasing Validated", "passed": True, "status": "passed", "detail": "ok"},
            {"title": "Value Acceptance", "passed": True, "status": "passed", "detail": "ok"},
            {"title": "Volatility Compression", "passed": True, "status": "passed", "detail": "ok"},
            {"title": "Smart Money Absorption", "passed": True, "status": "passed", "detail": "ok"},
        ],
    }


def test_demoted_watchlist_episode_never_ping_pongs_back_to_on_radar(tmp_path: Path) -> None:
    svc = AutoWatchlistService(tmp_path / "state.sqlite3")
    t0 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
    c0 = _cand("PROMUSDT", first_seen=t0)

    # T=0: Published ON_RADAR
    svc.sync_from_scan(published_opportunities=[c0], radar_events=[], now=t0, scan_id="scan-0")
    eps0 = svc.list_episodes(now=t0)
    assert len(eps0) == 1 and eps0[0].status is AutoWatchStatus.ON_RADAR

    # T+1h: Soft gate dip (Smart Money Absorption fails) -> demoted to WATCHLIST
    t1 = t0 + timedelta(hours=1)
    evict_cand = dict(c0)
    evict_cand["checklist"] = [
        *c0["checklist"][:4],
        {"title": "Smart Money Absorption", "passed": False, "status": "failed", "detail": "taker 1.01"},
    ]
    evict_event = {
        "symbol": "PROMUSDT",
        "at": t1.isoformat(),
        "state": "INVALIDATED",
        "retained": False,
        "reason": "radar_gates_degraded",
        "failed_gates": ["Smart Money Absorption"],
        "first_seen_at": t0.isoformat(),
        "candidate": evict_cand,
    }
    svc.sync_from_scan(published_opportunities=[], radar_events=[evict_event], now=t1, scan_id="scan-1")
    eps1 = svc.list_episodes(now=t1)
    assert len(eps1) == 1
    assert eps1[0].status is AutoWatchStatus.WATCHLIST
    assert eps1[0].was_promoted_to_watchlist is True

    # T+2h (<6h) AND T+7h (>6h): blocked_from_radar_symbols must include PROMUSDT,
    # and even if sync_from_scan receives a fresh candidate, a demoted WATCHLIST episode MUST NOT flip back to ON_RADAR!
    for offset_h in (2, 7):
        t_next = t0 + timedelta(hours=offset_h)
        blocked = svc.blocked_from_radar_symbols(now=t_next, cooldown_hours=12.0)
        assert ("PROMUSDT", "long") in blocked
        c_flicker = _cand("PROMUSDT", first_seen=t_next, price="5.12")
        svc.sync_from_scan(published_opportunities=[c_flicker], radar_events=[], now=t_next, scan_id=f"scan-{offset_h}")
        eps_next = svc.list_episodes(now=t_next)
        assert len(eps_next) == 1
        assert eps_next[0].status is AutoWatchStatus.WATCHLIST
        assert eps_next[0].radar_presence is RadarPresence.EVICTED_FROM_RADAR


def test_pre_ignition_watchlist_coil_graduates_in_place_once_and_then_locks(tmp_path: Path) -> None:
    svc = AutoWatchlistService(tmp_path / "state.sqlite3")
    t0 = datetime(2026, 11, 24, 8, 0, tzinfo=UTC)
    c_coil = _cand("OUSDT", first_seen=t0, price="0.520", lane="WATCHLIST_COIL")

    # T=0: Pre-ignition coil enrolls directly in WATCHLIST_COIL (visible on Watchlist, NOT blocked from 1st graduation!)
    ep_coil = svc.enroll_pre_ignition_coil(c_coil, published_at=t0, scan_id="scan-0")
    assert ep_coil is not None
    assert ep_coil.status is AutoWatchStatus.WATCHLIST
    assert ep_coil.was_promoted_to_watchlist is False
    assert ep_coil.graduation_count == 0
    assert ("OUSDT", "long") not in svc.blocked_from_radar_symbols(now=t0 + timedelta(minutes=30))

    # Build response shows OUSDT on active Watchlist while coiled
    resp0 = svc.build_response(live_radar_symbols=(), now=t0)
    assert [w["symbol"] for w in resp0["watchlist"]] == ["OUSDT"]

    # T+1h: Kinetic ignition fires -> graduates IN-PLACE to ON_RADAR (same episode_id, graduation_count == 1)
    t1 = t0 + timedelta(hours=1)
    c_ignited = _cand("OUSDT", first_seen=t1, price="0.548", lane="ON_RADAR")
    svc.sync_from_scan(published_opportunities=[c_ignited], radar_events=[], now=t1, scan_id="scan-1")
    eps1 = svc.list_episodes(now=t1)
    assert len(eps1) == 1
    assert eps1[0].episode_id == ep_coil.episode_id
    assert eps1[0].status is AutoWatchStatus.ON_RADAR
    assert eps1[0].graduation_count == 1

    # T+2h: Soft dip demotes graduated episode to WATCHLIST -> now locked (cannot graduate a 2nd time!)
    t2 = t0 + timedelta(hours=2)
    ev_cand = dict(c_ignited)
    ev_cand["first_seen_at"] = t0.isoformat()
    ev_cand["checklist"] = [
        *c_ignited["checklist"][:4],
        {"title": "Smart Money Absorption", "passed": False, "status": "failed", "detail": "soft dip"},
    ]
    svc.sync_from_scan(
        published_opportunities=[],
        radar_events=[
            {
                "symbol": "OUSDT",
                "at": t2.isoformat(),
                "state": "INVALIDATED",
                "retained": False,
                "reason": "radar_gates_degraded",
                "failed_gates": ["Smart Money Absorption"],
                "first_seen_at": t0.isoformat(),
                "candidate": ev_cand,
            }
        ],
        now=t2,
        scan_id="scan-2",
    )
    assert ("OUSDT", "long") in svc.blocked_from_radar_symbols(now=t2 + timedelta(hours=1))


def test_policy_change_invalidation_allows_immediate_fresh_coil_enrollment(tmp_path: Path) -> None:
    svc = AutoWatchlistService(tmp_path / "state.sqlite3")
    t0 = datetime(2026, 11, 24, 17, 1, tzinfo=UTC)
    c_old = _cand("1000LUNCUSDT", first_seen=t0, price="5.10", lane="ON_RADAR")
    svc.sync_from_scan(published_opportunities=[c_old], radar_events=[], now=t0, scan_id="scan-old")

    t1 = datetime(2026, 11, 24, 17, 51, tzinfo=UTC)
    policy_event = {
        "symbol": "1000LUNCUSDT",
        "at": t1.isoformat(),
        "state": "POLICY_CHANGED",
        "retained": False,
        "reason": "screening_policy_changed_requires_fresh_qualification",
        "failed_gates": [],
        "first_seen_at": t0.isoformat(),
        "candidate": c_old,
    }
    svc.sync_from_scan(published_opportunities=[], radar_events=[policy_event], now=t1, scan_id="scan-new")
    assert ("1000LUNCUSDT", "long") not in svc.blocked_from_radar_symbols(now=t1 + timedelta(minutes=10))

    c_new_coil = _cand("1000LUNCUSDT", first_seen=t1, price="5.15", lane="WATCHLIST_COIL")
    enrolled = svc.enroll_pre_ignition_coil(c_new_coil, published_at=t1, scan_id="scan-new")
    assert enrolled is not None
    assert enrolled.status is AutoWatchStatus.WATCHLIST
    assert enrolled.radar_status_label == "PRE-IGNITION COIL"


