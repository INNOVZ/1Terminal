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
    with patch.object(portal_module, "_screener_cache", {"opportunities": [], "qualifying_count": 0}):
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
    mock_system = AsyncMock()
    mock_system.crypto_screen.return_value = []
    mock_runtime = AsyncMock()
    mock_runtime.system = mock_system

    with (
        patch("algotrader.api.portal.get_portal_runtime", return_value=mock_runtime),
        patch("algotrader.api.portal.load_settings"),
    ):
        result = await portal_module._do_crypto_screen()
        assert result["opportunities"] == []
        assert result["qualifying_count"] == 0
