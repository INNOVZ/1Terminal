from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response

import algotrader.api.portal as portal_module
from algotrader.api.portal import (
    _get_fresh_baseline_opportunities,
    get_live_screener,
)
from algotrader.domain.scan_observations import ScanReceipt
from algotrader.infrastructure.runtime import ScreeningRun


def test_baseline_opportunities_never_contains_cached_candidates() -> None:
    """Verifies baseline opportunities never load cached opportunities from disk."""
    baseline = _get_fresh_baseline_opportunities()
    assert baseline["opportunities"] == []
    assert baseline["qualifying_count"] == 0


@pytest.mark.asyncio
async def test_live_screener_sets_no_cache_headers() -> None:
    """Verifies that /screener/live sets strict cache prevention headers."""
    resp = Response()
    # Mock _screener_cache to avoid initiating a real scan during test
    with patch.object(portal_module, "_screener_cache", {"opportunities": [], "qualifying_count": 0, "radar_lifecycle_version": portal_module.RADAR_PUBLICATION_VERSION}):
        await get_live_screener(response=resp)

    assert resp.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"
    assert resp.headers.get("Pragma") == "no-cache"


@pytest.mark.asyncio
async def test_live_screener_returns_empty_while_scanning() -> None:
    """Verifies that while scanning is in progress, opportunities is always empty (never cached candidates)."""
    # Create an artificial running task
    running_task = asyncio.create_task(asyncio.sleep(10))
    try:
        with patch.object(portal_module, "_screener_task", running_task):
            res = await get_live_screener()
            assert res["is_scanning"] is True
            assert res["opportunities"] == []
            assert res["qualifying_count"] == 0
    finally:
        running_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running_task


@pytest.mark.asyncio
async def test_do_crypto_screen_returns_empty_when_no_candidates_pass() -> None:
    """Verifies that if 0 setups qualify, _do_crypto_screen does NOT fall back to disk cache."""
    mock_runtime = AsyncMock()
    mock_runtime.run_crypto_scan.return_value = ScreeningRun([], {"selected": 12, "evaluated": 10, "failed": 2}, ScanReceipt("fixture", "saved"))

    with (
        patch("algotrader.api.portal.get_portal_runtime", return_value=mock_runtime),
        patch("algotrader.api.portal.load_settings"),
        patch("algotrader.api.portal.atomic_write_json"),
    ):
        result = await portal_module._do_crypto_screen()
        assert result["opportunities"] == []
        assert result["qualifying_count"] == 0
        assert result["scan_archive"]["status"] == "saved"
        assert result["total_scanned"] == 10
        assert result["order_execution_enabled"] is False


@pytest.mark.parametrize("verified", [False, True])
async def test_retained_radar_requires_five_current_verified_gates(verified):
    from datetime import UTC, datetime

    candidate = {
        "symbol": "TESTUSDT", "base_asset": "TEST", "first_seen_at": datetime.now(UTC).isoformat(),
        "radar_data_status": "current" if verified else "unavailable", "failed_gates": [],
        "checklist": [{"passed": verified, "status": "passed" if verified else "unknown"} for _ in range(5)],
    }
    runtime = AsyncMock()
    runtime.run_crypto_scan.return_value = ScreeningRun([], {}, ScanReceipt("fixture", "saved"))
    with (
        patch.object(portal_module, "_screener_cache", {"opportunities": [candidate]}),
        patch.object(portal_module, "get_portal_runtime", return_value=runtime),
        patch.object(portal_module, "reconcile_radar_cohort", return_value=[candidate]),
        patch.object(portal_module, "atomic_write_json"),
    ):
        result = await portal_module._do_crypto_screen()
    assert len(result["opportunities"]) == int(verified)
    if not verified:
        assert any(e["reason"] == "radar_gates_unverified" for e in result["radar_events"])


async def test_previous_publication_cache_is_refreshed_before_display():
    from unittest.mock import AsyncMock

    old = {"opportunities": [{"symbol": "OLDUSDT"}], "radar_lifecycle_version": "radar-v2"}
    with (
        patch.object(portal_module, "_screener_cache", old),
        patch.object(portal_module, "_screener_task", None),
        patch.object(portal_module, "_do_crypto_screen", new_callable=AsyncMock) as scan,
    ):
        result = await get_live_screener()
        assert result["opportunities"] == [] and result["is_scanning"] is True
        await portal_module._screener_task
        scan.assert_awaited_once()
