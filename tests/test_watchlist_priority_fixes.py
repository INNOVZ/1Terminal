from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from algotrader.application.auto_watchlist_service import AutoWatchlistService
from algotrader.application.watchlist_service import WatchlistService
from algotrader.domain.auto_watchlist import (
    AutoWatchStatus,
    build_auto_watch_episode,
    compute_performance_summary,
    evaluate_auto_watch_episode,
)
from algotrader.domain.models import DerivativesSnapshot, Venue
from tests.conftest import make_instrument
from tests.test_auto_watchlist import T0, _make_candle, _sample_candidate


def episode():
    ep = build_auto_watch_episode(_sample_candidate())
    assert ep is not None
    return ep


def test_old_price_does_not_become_48h_endpoint_and_null_survives_wire():
    ep = evaluate_auto_watch_episode(episode(), candles=[_make_candle(T0 + timedelta(hours=1),
        h="103", low="99", c="102")], live_price=None, now=T0 + timedelta(hours=49))
    assert ep.status is AutoWatchStatus.EXPIRED_48H
    assert ep.resolved_return_pct is None
    assert ep.as_dict()["current_return_pct"] is None
    assert ep.as_dict()["resolved_price"] is None
    summary = compute_performance_summary([ep])["overall"]
    assert summary["unknown_outcome_count"] == 1
    assert summary["avg_return_pct"] is None


def test_exact_endpoint_can_be_measured_without_claiming_complete_path():
    ep = evaluate_auto_watch_episode(episode(), candles=[_make_candle(T0 + timedelta(hours=47),
        h="104", low="99", c="103")], live_price=None, now=T0 + timedelta(hours=49))
    assert ep.resolved_return_pct == Decimal("3.00")
    assert ep.endpoint_observed_at == ep.expires_at
    assert not ep.coverage_complete
    assert ep.coverage_gaps > 0


def test_contiguous_path_certifies_first_barrier_but_gap_does_not():
    bars = [_make_candle(T0, h="104"), _make_candle(T0 + timedelta(hours=1), h="113")]
    full = evaluate_auto_watch_episode(episode(), candles=bars, live_price=None, now=T0 + timedelta(hours=2))
    gap = evaluate_auto_watch_episode(episode(), candles=bars[1:], live_price=None, now=T0 + timedelta(hours=2))
    assert full.barrier_order_certified and full.coverage_complete
    assert gap.status is AutoWatchStatus.TARGET_REACHED
    assert not gap.barrier_order_certified
    assert compute_performance_summary([full])["overall"]["target_hit_rate_pct"] == 100
    assert compute_performance_summary([gap])["overall"]["target_hit_rate_pct"] is None


def test_empty_extrema_and_legacy_returns_do_not_enter_measured_statistics(tmp_path):
    ep = episode()
    expired = evaluate_auto_watch_episode(ep, candles=[], live_price=None, now=ep.expires_at)
    summary = compute_performance_summary([expired])["overall"]
    assert summary["avg_peak_mfe_pct"] is None
    assert expired.as_dict()["peak_mfe_pct"] is None
    store = AutoWatchlistService(tmp_path / "state.sqlite3")
    store.enroll_candidate(_sample_candidate())
    with store._get_connection() as conn:
        conn.execute("UPDATE radar_auto_watchlist SET status='EXPIRED_48H', resolved_return_pct='99', measurement_json=NULL")
    old = AutoWatchlistService(tmp_path / "state.sqlite3").list_episodes(now=T0)[0]
    assert old.as_dict()["measurement_status"] == "legacy_unverified"
    assert old.as_dict()["resolved_return_pct"] is None
    assert compute_performance_summary([old])["overall"]["avg_return_pct"] is None


def test_measurement_metadata_round_trips_without_overwriting_origin(tmp_path):
    store = AutoWatchlistService(tmp_path / "state.sqlite3")
    ep = store.enroll_candidate(_sample_candidate())
    assert ep is not None
    measured = evaluate_auto_watch_episode(ep, candles=[_make_candle(T0, h="113")], live_price=None,
                                         now=T0 + timedelta(hours=1))
    with store._get_connection() as conn:
        store._upsert_episode_locked(conn, measured)
    reopened = AutoWatchlistService(tmp_path / "state.sqlite3").list_episodes(now=T0)[0]
    assert reopened.barrier_order_certified
    assert reopened.endpoint_observed_at == T0 + timedelta(hours=1)
    assert reopened.published_at == ep.published_at


@pytest.mark.parametrize("observed", [None, T0 - timedelta(hours=1), T0 + timedelta(hours=3)])
def test_live_quotes_need_actual_in_window_fresh_time(observed):
    ep = evaluate_auto_watch_episode(episode(), candles=[], live_price=Decimal("120"),
        now=T0 + timedelta(hours=2), live_observed_at=observed)
    assert ep.is_active
    assert not ep.has_observations


@pytest.mark.asyncio
async def test_force_refresh_cooldown_singleflight_and_failure_budget(tmp_path):
    store = AutoWatchlistService(tmp_path / "state.sqlite3")
    store.enroll_candidate(_sample_candidate())
    inst = make_instrument(symbol="USUALUSDT", venue=Venue.BINANCE_USDM)
    market = SimpleNamespace(quotes=AsyncMock(return_value=[]), candles=AsyncMock(return_value=[]))
    await asyncio.gather(*(store.refresh_and_evaluate(market, [inst], force_refresh=True,
                        now=T0 + timedelta(hours=1)) for _ in range(8)))
    assert market.quotes.await_count == market.candles.await_count == 1
    await store.refresh_and_evaluate(market, [inst], force_refresh=True,
                                    now=T0 + timedelta(hours=1, seconds=30))
    assert market.quotes.await_count == 2


def histories(now, hours, count):
    return [replace(_make_candle(now - timedelta(hours=hours * (count - i))),
                    closed_at=now - timedelta(hours=hours * (count - i - 1))) for i in range(count)]


@pytest.mark.parametrize("missing", ["4h", "flow", "old_quote", "old_candles", "wrong_interval", "none"])
def test_audit_requires_sufficient_fresh_components_and_has_a_working_success_path(tmp_path, missing):
    now = datetime.now(UTC)
    service = WatchlistService(tmp_path / "state.sqlite3")
    item = replace(service.list_items()[0], symbol="SOLUSDT", current_price=Decimal("100"),
                   trigger_price=Decimal("104"), support_price=Decimal("98"), invalidation_price=Decimal("92"),
                   target_1=Decimal("112"))
    flow = DerivativesSnapshot(funding_rate=Decimal(0), basis_rate=Decimal(0), open_interest=Decimal(0),
        open_interest_change_1h=None, taker_buy_sell_ratio=Decimal("1.1"), bid_depth_notional=Decimal(0),
        ask_depth_notional=Decimal(0), observed_at=now)
    c1 = histories(now - timedelta(days=3) if missing == "old_candles" else now, 1, 60)
    if missing == "wrong_interval":
        c4 = [replace(_make_candle(now - timedelta(minutes=25 - i)),
                      closed_at=now - timedelta(minutes=24 - i)) for i in range(25)]
    else:
        c4 = [] if missing == "4h" else histories(now, 4, 25)
    audit = service.audit_item(item, Decimal("100"), c1, c4, None if missing == "flow" else flow,
        observed_at=now - timedelta(hours=1) if missing == "old_quote" else now)
    if missing == "none":
        assert audit.telemetry_available
        assert audit.status.value == "COIL_HOLDING"
    else:
        assert not audit.telemetry_available
        assert audit.current_rsi_4h is None
        assert audit.status.value == "DATA_UNAVAILABLE"
        if missing == "old_quote":
            assert audit.audited_at is None
            assert audit.live_price is None
    assert "All 6" not in audit.gate_tripped


def test_uncertified_barrier_and_unknown_expiry_wire_and_denominators():
    # Zero-observation expiry must null out extrema timestamps and distance metrics
    zero_exp = evaluate_auto_watch_episode(episode(), candles=[], live_price=None, now=T0 + timedelta(hours=49))
    wire = zero_exp.as_dict(now=T0 + timedelta(hours=49))
    assert wire["peak_mfe_pct"] is None and wire["peak_mfe_at"] is None
    assert wire["worst_mae_pct"] is None and wire["worst_mae_at"] is None
    assert wire["dist_to_t1_pct"] is None and wire["dist_to_stop_pct"] is None
    assert wire["last_observed_price"] is None

    # Stale T+2h candle preserves last_observed_price separately without fabricating 48h return
    stale_exp = evaluate_auto_watch_episode(
        episode(),
        candles=[_make_candle(T0 + timedelta(hours=1), h="103", low="99", c="102")],
        live_price=None,
        now=T0 + timedelta(hours=49),
    )
    stale_wire = stale_exp.as_dict(now=T0 + timedelta(hours=49))
    assert stale_wire["resolved_price"] is None and stale_wire["resolved_return_pct"] is None
    assert stale_wire["last_observed_price"] == 102.0

    # Uncertified barrier hit cannot enter measured return averages
    gap_hit = evaluate_auto_watch_episode(
        episode(),
        candles=[_make_candle(T0 + timedelta(hours=1), h="113")],
        live_price=None,
        now=T0 + timedelta(hours=2),
    )
    assert gap_hit.status is AutoWatchStatus.TARGET_REACHED
    assert not gap_hit.barrier_order_certified
    summary = compute_performance_summary([gap_hit])["overall"]
    assert summary["measured_resolved"] == 0
    assert summary["unknown_outcome_count"] == 1
    assert summary["avg_resolved_return_pct"] is None
    assert summary["avg_return_pct"] is None
    assert summary["measured_resolved"] + summary["unknown_outcome_count"] == summary["resolved"]


@pytest.mark.asyncio
async def test_internal_scheduler_can_evaluate_while_operator_force_refresh_respects_cooldown(tmp_path):
    store = AutoWatchlistService(tmp_path / "sched.sqlite3")
    store.enroll_candidate(_sample_candidate())
    inst = make_instrument(symbol="USUALUSDT", venue=Venue.BINANCE_USDM)
    market = SimpleNamespace(quotes=AsyncMock(return_value=[]), candles=AsyncMock(return_value=[]))
    at = T0 + timedelta(hours=1)
    await store.refresh_and_evaluate(market, [inst], force_refresh=True, now=at)
    await store.refresh_and_evaluate(market, [inst], force_refresh=True, now=at + timedelta(seconds=5))
    assert market.quotes.await_count == 1
    # Internal post-scan scheduler may still evaluate newly enrolled candidates immediately
    await store.refresh_and_evaluate(
        market, [inst], force_refresh=True, internal_scheduler=True, now=at + timedelta(seconds=5)
    )
    assert market.quotes.await_count == 2


@pytest.mark.asyncio
async def test_watchlist_service_authorized_force_refresh_cooldown_and_stale_cache_rejection(tmp_path):
    svc = WatchlistService(tmp_path / "wl.sqlite3")
    inst = make_instrument(symbol="ONTUSDT", venue=Venue.BINANCE_USDM)
    market = SimpleNamespace(
        quotes=AsyncMock(return_value=[]),
        candles=AsyncMock(return_value=[]),
    )
    await svc.run_live_audit(market, [inst], force_refresh=True)
    calls_first = market.quotes.await_count
    await svc.run_live_audit(market, [inst], force_refresh=True)
    assert market.quotes.await_count == calls_first
    # Stale audit cache older than 60s must never be served as fresh on fallback
    svc._audit_cache_time = datetime.now(UTC) - timedelta(minutes=10)
    assert svc.get_fresh_audit_cache() == {}
