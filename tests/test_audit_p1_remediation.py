"""Regression tests for Audit Batch 1 P1/P2 remediation (A1, A3, A4, A5)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.api import portal
from algotrader.api.portal_app import create_app
from algotrader.config import Settings
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.screener_policy import SCREENING_POLICY_VERSION
from algotrader.domain.watchlist_models import WatchlistItem, WatchlistPriority, WyckoffStage

TOKEN = "o" * 40


def _inst(symbol: str = "FLATUSDT") -> Instrument:
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, symbol),
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.1"),
        min_quantity=Decimal("0.1"),
        min_notional=Decimal("5.0"),
    )


# --- A1: No synthetic signal metadata on /api/chart/{symbol} ---


async def test_a1_chart_inspection_never_synthesizes_opportunity_metadata(monkeypatch):
    now = datetime.now(UTC)
    flat_candle = Candle(
        opened_at=now - timedelta(hours=1),
        closed_at=now,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("10"),
        closed=True,
    )
    runtime = MagicMock()
    runtime.settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime.futures_market = AsyncMock()
    runtime.futures_market.venue = Venue.BINANCE_USDM
    runtime.futures_market.instruments.return_value = [_inst("FLATUSDT")]
    runtime.futures_market.candles.return_value = [flat_candle]

    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    monkeypatch.setattr(portal, "_cached_instruments", [_inst("FLATUSDT")])
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal._watchlist_service, "get_item", lambda _sym: None)

    resp = await portal.get_symbol_chart("FLATUSDT")
    assert resp["record_kind"] == "chart_inspection"
    assert resp["opportunity"] is None
    assert resp["watchlist_view"] is None
    assert resp["inspection"]["qualified"] is False
    assert resp["inspection"]["current_price"] == "100.0"
    assert "score" not in resp["inspection"]
    assert "poc_price" not in resp["inspection"]
    assert "breakout_readiness_status" not in resp["inspection"]


async def test_a1_unaudited_watchlist_item_never_fabricates_passing_checks(monkeypatch):
    now = datetime.now(UTC)
    candle = Candle(
        opened_at=now - timedelta(hours=1),
        closed_at=now,
        open=Decimal("10"),
        high=Decimal("10.5"),
        low=Decimal("9.8"),
        close=Decimal("10.2"),
        volume=Decimal("100"),
        closed=True,
    )
    item = WatchlistItem(
        symbol="WATCHUSDT",
        name="WATCH",
        sector="DeFi",
        stage=WyckoffStage.COIL_IMMINENT,
        timeframe="1-3d",
        current_price=Decimal("10.2"),
        trigger_price=Decimal("11.0"),
        support_price=Decimal("9.5"),
        invalidation_price=Decimal("9.0"),
        target_1=Decimal("12.0"),
        target_2=Decimal("13.0"),
        distance_to_trigger_pct=Decimal("7.8"),
        score=Decimal("82"),
        thesis="Manual watch",
        checklist_passed=0,
        checklist_total=5,
        priority=WatchlistPriority.HIGH,
        audit=None,
    )
    runtime = MagicMock()
    runtime.settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime.futures_market = AsyncMock()
    runtime.futures_market.venue = Venue.BINANCE_USDM
    runtime.futures_market.instruments.return_value = [_inst("WATCHUSDT")]
    runtime.futures_market.candles.return_value = [candle]

    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    monkeypatch.setattr(portal, "_cached_instruments", [_inst("WATCHUSDT")])
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal._watchlist_service, "get_item", lambda _sym: item)

    resp = await portal.get_symbol_chart("WATCHUSDT")
    assert resp["record_kind"] == "watchlist_view"
    assert resp["opportunity"] is None
    wv = resp["watchlist_view"]
    assert wv["audit_verified"] is False
    assert wv["checklist_passed"] == 0
    assert wv["expected_value_r"] is None
    assert wv["estimated_win_rate_pct"] is None
    assert wv["breakout_readiness_status"] == "WATCHLIST_UNVERIFIED"
    assert all(c["passed"] is False and c["status"] == "unknown" for c in wv["checklist"])


# --- A3: Stale cache withheld and /readyz fails closed; failed scan invalidates prior cache ---


async def test_a3_stale_seven_day_cache_withheld_and_readyz_503(monkeypatch):
    old_ts = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    settings = Settings(_env_file=None, screener_cron_enabled=False, screener_interval_hours=1)
    runtime = AsyncMock()
    runtime.settings = settings
    runtime.readiness.return_value = (True, {"status": "ready"})

    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(portal, "_consecutive_scan_failures", 0)
    monkeypatch.setattr(portal, "_cache_timestamp", datetime.fromisoformat(old_ts))
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "timestamp": old_ts,
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
            "opportunities": [{"symbol": "STALEUSDT"}],
            "qualifying_count": 1,
        },
    )
    app = create_app(settings)
    app.state.runtime = runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        live = await client.get("/api/screener/live")
        assert live.status_code == 200
        body = live.json()
        assert body["opportunities"] == []
        assert body["qualifying_count"] == 0
        assert body["stale"] is True
        assert body["data_status"] == "stale"

        ready = await client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["data_status"] == "stale"


async def test_a3_failed_scan_invalidates_prior_cached_candidates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fresh_ts = datetime.now(UTC).isoformat()
    runtime = AsyncMock()
    runtime.settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime.run_crypto_scan.side_effect = RuntimeError("synthetic outage")

    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(portal, "_shared_runtime", runtime)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "timestamp": fresh_ts,
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
            "opportunities": [{"symbol": "STALEUSDT"}],
            "qualifying_count": 1,
        },
    )

    fail_res = await portal._do_crypto_screen()
    assert "synthetic outage" in fail_res["macro_narrative"]
    assert fail_res["opportunities"] == []

    after = await portal.get_live_screener()
    assert after["opportunities"] == []
    assert after["qualifying_count"] == 0
    assert after["data_status"] == "scan_failed"


# --- A4: Operator auth on refresh/rescan and watchlist writes ---


@pytest.mark.parametrize("header", [None, "Bearer wrong-token"])
async def test_a4_unauthenticated_refresh_rescan_and_watchlist_writes_blocked(monkeypatch, header):
    settings = Settings(_env_file=None, operator_token=TOKEN, screener_cron_enabled=False)
    monkeypatch.setattr(portal, "load_settings", lambda: settings)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(portal, "_last_manual_refresh_at", None)
    headers = {"Authorization": header} if header else {}

    with (
        patch.object(portal, "_do_crypto_screen", new_callable=AsyncMock) as scan,
        patch.object(portal._watchlist_service, "delete_item") as delete_mock,
        patch.object(portal._watchlist_service, "update_notes") as notes_mock,
        patch.object(portal._watchlist_service, "add_or_update") as add_mock,
    ):
        app = create_app(settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            r1 = await client.get("/api/screener/live?refresh=true&scope=full", headers=headers)
            r2 = await client.post("/api/screener/rescan?scope=full", headers=headers)
            r3 = await client.delete("/api/watchlist/TESTUSDT", headers=headers)
            r4 = await client.patch(
                "/api/watchlist/TESTUSDT/notes", json={"notes": "unauthorized"}, headers=headers
            )
            r5 = await client.post("/api/watchlist", json={"symbol": "TESTUSDT"}, headers=headers)
        assert (r1.status_code, r2.status_code, r3.status_code, r4.status_code, r5.status_code) == (
            401,
            401,
            401,
            401,
            401,
        )
        scan.assert_not_awaited()
        delete_mock.assert_not_called()
        notes_mock.assert_not_called()
        add_mock.assert_not_called()


async def test_a4_authorized_operator_rescan_and_watchlist_write_succeed(monkeypatch):
    settings = Settings(_env_file=None, operator_token=TOKEN, screener_cron_enabled=False)
    monkeypatch.setattr(portal, "load_settings", lambda: settings)
    monkeypatch.setattr(portal, "_screener_task", None)
    monkeypatch.setattr(portal, "_last_manual_refresh_at", None)
    monkeypatch.setattr(
        portal,
        "_screener_cache",
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "screening_policy_version": SCREENING_POLICY_VERSION,
            "radar_lifecycle_version": portal.RADAR_PUBLICATION_VERSION,
            "opportunities": [],
            "qualifying_count": 0,
        },
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with (
        patch.object(portal, "_do_crypto_screen", new_callable=AsyncMock, return_value={}) as scan,
        patch.object(portal._watchlist_service, "delete_item", return_value=True) as delete_mock,
    ):
        app = create_app(settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            r_scan = await client.post("/api/screener/rescan", headers=headers)
            r_del = await client.delete("/api/watchlist/TESTUSDT", headers=headers)
        assert r_scan.status_code == 200
        assert r_del.status_code == 200
        scan.assert_awaited_once()
        delete_mock.assert_called_once_with("TESTUSDT")


# --- A5: Quote exchange timestamp preservation & crossed-book rejection ---


async def test_a5_adapter_preserves_exchange_time_and_rejects_crossed_books():
    seven_days_ago_ms = int((datetime.now(UTC) - timedelta(days=7)).timestamp() * 1000)

    class FakeClient:
        async def request(self, method: str, path: str, params=None):
            if path.endswith("ticker/24hr"):
                return [
                    {
                        "symbol": "STALEUSDT",
                        "quoteVolume": "2000000",
                        "closeTime": seven_days_ago_ms,
                    },
                    {
                        "symbol": "CROSSUSDT",
                        "quoteVolume": "2000000",
                        "closeTime": int(datetime.now(UTC).timestamp() * 1000),
                    },
                ]
            if path.endswith("ticker/bookTicker"):
                return [
                    {
                        "symbol": "STALEUSDT",
                        "bidPrice": "99.95",
                        "askPrice": "100.05",
                        "time": seven_days_ago_ms,
                    },
                    {
                        "symbol": "CROSSUSDT",
                        "bidPrice": "101.00",
                        "askPrice": "99.00",
                        "time": int(datetime.now(UTC).timestamp() * 1000),
                    },
                ]
            return []

    adapter = BinanceUsdmAdapter(FakeClient())  # type: ignore[arg-type]
    quotes = await adapter.quotes([_inst("STALEUSDT"), _inst("CROSSUSDT")])
    by_sym = {q.instrument_id.symbol: q for q in quotes}

    # Crossed book must be dropped at the adapter boundary
    assert "CROSSUSDT" not in by_sym

    # Stale exchange quote must retain its true 7-day-old exchange timestamp, not a fresh receipt time
    stale_q = by_sym["STALEUSDT"]
    assert stale_q.exchange_observed_at is not None
    assert stale_q.received_at is not None
    assert (stale_q.received_at - stale_q.observed_at).total_seconds() > 6 * 86400
    assert stale_q.is_fresh_at(stale_q.received_at) is False

    # Direct crossed Quote construction must also fail validation and never pass spread_bps <= 50
    crossed = Quote(
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "CROSSUSDT"),
        bid=Decimal("101"),
        ask=Decimal("99"),
        last=Decimal("100"),
        quote_volume_24h=Decimal("2000000"),
    )
    assert crossed.is_valid_book is False
    assert crossed.spread_bps == Decimal("Infinity")
    assert not (crossed.spread_bps <= Decimal("50"))

