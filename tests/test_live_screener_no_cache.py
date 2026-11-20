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
    with patch.object(portal_module, "_screener_cache", {"opportunities": [], "qualifying_count": 0, "radar_lifecycle_version": portal_module.RADAR_PUBLICATION_VERSION, "screening_policy_version": portal_module.SCREENING_POLICY_VERSION}):
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
        patch.object(portal_module, "_screener_cache", {"opportunities": [candidate], "screening_policy_version": portal_module.SCREENING_POLICY_VERSION}),
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


async def test_previous_screening_policy_cache_is_refreshed_even_with_current_radar():
    old = {
        "opportunities": [{"symbol": "OLDUSDT"}],
        "radar_lifecycle_version": portal_module.RADAR_PUBLICATION_VERSION,
        "screening_policy_version": "atlas-v2.7-structural-brackets-2",
    }
    with (
        patch.object(portal_module, "_screener_cache", old),
        patch.object(portal_module, "_screener_task", None),
        patch.object(portal_module, "_do_crypto_screen", new_callable=AsyncMock) as scan,
    ):
        result = await get_live_screener()
        assert result["opportunities"] == [] and result["is_scanning"] is True
        await portal_module._screener_task
        scan.assert_awaited_once()


async def test_previous_screening_policy_candidates_cannot_be_retained_after_upgrade():
    runtime = AsyncMock()
    runtime.run_crypto_scan.return_value = ScreeningRun([], {}, ScanReceipt("fixture", "saved"))
    with (
        patch.object(portal_module, "_screener_cache", {
            "opportunities": [{"symbol": "OLDUSDT"}],
            "screening_policy_version": "old",
        }),
        patch.object(portal_module, "get_portal_runtime", return_value=runtime),
        patch.object(portal_module, "reconcile_radar_cohort", new_callable=AsyncMock, return_value=[]) as reconcile,
        patch.object(portal_module, "atomic_write_json"),
    ):
        result = await portal_module._do_crypto_screen()
        assert result["opportunities"] == []
        assert reconcile.call_args.kwargs["prior_opportunities"] == []
        assert result["radar_events"][0]["reason"] == "screening_policy_changed_requires_fresh_qualification"


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic invalid"])
async def test_manual_short_watch_refresh_requires_operator_before_any_work(authorization):
    import httpx

    from algotrader.api.portal_app import create_app
    from algotrader.config import Settings

    settings = Settings(_env_file=None, operator_token="x" * 40, screener_cron_enabled=False)
    with (
        patch.object(portal_module, "load_settings", return_value=settings),
        patch.object(portal_module, "_run_short_watch_cycle", new_callable=AsyncMock) as cycle,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)),
                                     base_url="http://localhost") as client:
            headers = {"Authorization": authorization} if authorization else {}
            response = await client.post("/api/screener/short-watch/tick", headers=headers)
        assert response.status_code == 401
        cycle.assert_not_awaited()


async def test_manual_short_watch_refresh_accepts_existing_operator_credential():
    import httpx

    from algotrader.api.portal_app import create_app
    from algotrader.config import Settings

    settings = Settings(_env_file=None, operator_token="x" * 40, screener_cron_enabled=False)
    with (
        patch.object(portal_module, "load_settings", return_value=settings),
        patch.object(portal_module, "_run_short_watch_cycle", new_callable=AsyncMock, return_value={}) as cycle,
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)),
                                     base_url="http://localhost") as client:
            response = await client.post("/api/screener/short-watch/tick", headers={"Authorization": "Bearer " + "x" * 40})
        assert response.status_code == 200
        cycle.assert_awaited_once()


async def test_unconfigured_manual_short_watch_refresh_is_disabled():
    from fastapi import HTTPException

    from algotrader.config import Settings

    with (
        patch.object(portal_module, "load_settings", return_value=Settings(_env_file=None, operator_token="")),
        patch.object(portal_module, "_run_short_watch_cycle", new_callable=AsyncMock) as cycle,
    ):
        with pytest.raises(HTTPException) as raised:
            await portal_module.trigger_short_watch_tick(authorization=None)
        assert raised.value.status_code == 503
        cycle.assert_not_awaited()
