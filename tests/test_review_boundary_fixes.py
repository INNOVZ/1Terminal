"""Cross-boundary regressions from the November 23 independent review."""
from __future__ import annotations

import errno
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from algotrader.api import portal
from algotrader.api.portal_app import create_app
from algotrader.config import Settings
from algotrader.domain.models import Venue
from algotrader.domain.radar_lifecycle import (
    evaluate_radar_candidate_lifecycle,
    reverify_candidate_gates,
)
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.runtime import ScreeningRun
from tests.conftest import make_instrument


@pytest.mark.parametrize("trigger_kind", ["future", "pre_discovery", "legacy_unknown"])
def test_raw_trigger_cannot_enable_profit_tolerance(trigger_kind):
    now = datetime(2026, 11, 23, 12, tzinfo=UTC)
    bars = [{"time": int((now - timedelta(hours=71-i)).timestamp()),
             "opened_time": int((now - timedelta(hours=72-i)).timestamp()),
             "close": "100", "high": "101", "low": "99"} for i in range(72)]
    stamp = now + timedelta(hours=1) if trigger_kind == "future" else now - timedelta(hours=1)
    if trigger_kind == "pre_discovery":
        stamp = now - timedelta(hours=4)
    candidate = {
        "symbol": "TESTUSDT", "direction": "long", "setup_label": "VCP_COMPRESSION_LONG",
        "execution_mode": "RETEST_ACCUMULATION", "evaluated_at": (now-timedelta(hours=3)).isoformat(),
        "radar_triggered_at": stamp.isoformat(), "suggested_entry": "95", "breakout_trigger": "110",
        "suggested_stop": "90", "suggested_targets": ["120", "130", "140"],
        "poc_price": "99", "val_price": "97", "vah_price": "110",
        "radar_metrics": {"bb_bandwidth_4h": "0.10", "oi_change_7d_pct": "5%",
                          "oi_change_1h_pct": "0%", "taker_buy_sell_ratio": "0.90",
                          "funding_rate_bps": "20 bps", "volume_contraction_ratio": "0.8"},
    }
    decision = evaluate_radar_candidate_lifecycle(
        candidate, current_price=Decimal("100"), recent_candles_1h=bars, ema50_4h=Decimal("99"), now=now,
    )
    assert decision.gates_passed == 4
    assert decision.trigger_entry_price is None
    assert "Smart Money Absorption" in decision.failed_gates
    if trigger_kind != "legacy_unknown":
        assert decision.triggered_at is None
    # Even a raw candidate with a plausible locked reference cannot override explicit caller state.
    candidate["radar_trigger_entry"] = "95"
    _, passed, _, _ = reverify_candidate_gates(
        candidate, current_price=Decimal("100"), recent_candles_1h=bars,
        ema50_4h=Decimal("99"), is_triggered=False,
    )
    assert passed == 4
    # An observed legacy episode without a locked entry gets no invented profit tolerance.
    _, passed, _, _ = reverify_candidate_gates(
        candidate, current_price=Decimal("100"), recent_candles_1h=bars,
        ema50_4h=Decimal("99"), is_triggered=True, trigger_entry_price=None,
    )
    assert passed == 4


@pytest.mark.parametrize("failed_write", [False, True])
async def test_boot_requires_live_verification_even_after_failure_write_error(monkeypatch, tmp_path, failed_write):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    settings = Settings(_env_file=None, screener_cron_enabled=False)
    episode = {"symbol": "TESTUSDT", "radar_signal": {"episode_id": "original"}}
    cache = {"timestamp": now.isoformat(), "data_status": "current", "opportunities": [episode],
             "qualifying_count": 1, "screening_policy_version": portal.SCREENING_POLICY_VERSION,
             "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION}
    cache_file = tmp_path / "data/screener_latest.json"
    cache_file.parent.mkdir()
    cache_file.write_text(json.dumps(cache))
    inst = make_instrument(symbol="TESTUSDT", venue=Venue.BINANCE_USDM)
    runtime = SimpleNamespace(settings=settings, futures_market=SimpleNamespace(
        venue=Venue.BINANCE_USDM, candles=AsyncMock(return_value=[]),
        instruments=AsyncMock(return_value=[inst]),
    ), _closed=False)
    for name, value in {"_screener_cache": dict(cache), "_cache_timestamp": now,
                        "_episode_roster": [], "_episode_policy_version": None,
                        "_episode_macro_narrative": None, "_screener_task": None,
                        "_is_refreshing": False, "_shared_runtime": runtime,
                        "_consecutive_scan_failures": 0, "_last_scan_error": None}.items():
        monkeypatch.setattr(portal, name, value)
    if failed_write:
        with patch.object(portal, "atomic_write_json", side_effect=OSError(errno.ENOSPC, "disk full")):
            result = await portal._persist_degraded_state(
                data_status="scan_failed", stale_reason="scan_failed", error_message="provider outage",
                macro_narrative="Unavailable",
            )
        assert result["cache_persisted"] is False
        assert json.loads(cache_file.read_text())["data_status"] == "current"

    # Simulate fresh memory, then recover old disk evidence. No writes are needed to fail closed.
    portal._screener_cache = {}
    portal._cache_timestamp = None
    portal._episode_roster = []
    portal._consecutive_scan_failures = 0
    loaded = portal.warm_screener_cache(cache_file)
    assert loaded["data_status"] == "startup_revalidation_required"
    assert loaded["opportunities"] == []
    assert portal._get_prior_episodes_for_reconciliation()[0] == [episode]

    with patch.object(portal, "_do_crypto_screen", AsyncMock(return_value={})) as scan:
        live = await portal.get_live_screener()
        await portal._screener_task
        assert scan.await_count == 1
        assert live["opportunities"] == [] and live["is_scanning"]
        # Chart/live must agree about the saved episode's non-public status.
        assert portal._project_public_screener_state(settings)[1]["opportunities"] == []
    app = create_app(settings)
    app.state.runtime = SimpleNamespace(readiness=AsyncMock(return_value=(True, {"status": "ready"})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["data_status"] == "startup_revalidation_required"
    with (patch.object(portal, "get_portal_runtime", AsyncMock(return_value=runtime)),
          patch.object(portal, "_cached_instruments", [inst]),
          patch.object(portal, "_watchlist_service", SimpleNamespace(get_item=lambda _: None)),
          patch.object(portal, "_do_crypto_screen", AsyncMock(return_value={}))):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            api_live = await client.get("/api/screener/live")
            chart = await client.get("/api/chart/TESTUSDT")
        await portal._screener_task
    assert api_live.json()["opportunities"] == []
    assert chart.json()["opportunity"] is None
    assert chart.json()["record_kind"] == "chart_inspection"

    # A successful scan reconciles the original identity before replacing public state.
    runtime.run_crypto_scan = AsyncMock(return_value=ScreeningRun(
        [], {"attempted": 1, "evaluated": 1, "failed": 0}, ScanReceipt(str(uuid4()), "saved"),
    ))
    with (patch.object(portal, "get_portal_runtime", AsyncMock(return_value=runtime)),
          patch.object(portal, "reconcile_radar_cohort", AsyncMock(return_value=[])) as reconcile,
          patch.object(portal.LocalScanArchive, "prune_scans", return_value=())):
        await portal._do_crypto_screen()
    assert reconcile.call_args.kwargs["prior_opportunities"] == [episode]
    status, public = portal._project_public_screener_state(settings)
    assert status == "current" and public["opportunities"] == []
    assert public["data_status"] == "current"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.get("/readyz")).status_code == 200
