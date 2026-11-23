import asyncio
import contextlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from algotrader.api import portal
from algotrader.api.portal_app import create_app
from algotrader.application.watchlist_service import WatchlistService
from algotrader.config import Settings
from algotrader.domain.models import Candle, Quote, Venue
from algotrader.domain.watchlist_models import WatchlistItem, WatchlistPriority, WyckoffStage
from tests.conftest import make_instrument


@pytest.mark.parametrize("message", ["database is locked", "disk I/O error"])
def test_failed_watchlist_open_preserves_database_sidecars(tmp_path, monkeypatch, message):
    database = tmp_path / "state.sqlite3"
    shm = tmp_path / "state.sqlite3-shm"
    wal = tmp_path / "state.sqlite3-wal"
    shm.write_bytes(b"existing shared memory")
    wal.write_bytes(b"committed WAL evidence")

    @contextlib.contextmanager
    def unavailable(self):
        raise sqlite3.OperationalError(message)
        yield

    monkeypatch.setattr(WatchlistService, "_get_connection", unavailable)
    with pytest.raises(sqlite3.OperationalError, match=message):
        WatchlistService(database)
    assert shm.read_bytes() == b"existing shared memory"
    assert wal.read_bytes() == b"committed WAL evidence"


@pytest.mark.asyncio
async def test_stateless_batch_watchlist_audit_evaluates_per_user_levels_independently(monkeypatch):
    """W1: Verified exchange data evaluates per-user levels independently; missing data returns DATA_UNAVAILABLE
    and never poisons the shared cache with a user's reference price."""
    now = datetime.now(UTC)
    inst_comp = make_instrument(symbol="COMPUSDT", venue=Venue.BINANCE_USDM)
    inst_sol = make_instrument(symbol="SOLUSDT", venue=Venue.BINANCE_USDM)
    comp_candle = Candle(
        opened_at=now - timedelta(hours=1),
        closed_at=now,
        open=Decimal("24.00"),
        high=Decimal("24.50"),
        low=Decimal("23.80"),
        close=Decimal("24.19"),
        volume=Decimal("1000"),
        closed=True,
    )

    class FakeFutures:
        _client = SimpleNamespace(request=AsyncMock(return_value=[{"buySellRatio": "1.05", "timestamp": int(now.timestamp() * 1000)}]))

        async def quotes(self, instruments):
            return [Quote(inst_comp.id, Decimal("24.18"), Decimal("24.20"), Decimal("24.19"), Decimal("100000"), now)]

        async def instruments(self):
            return [inst_comp, inst_sol]

        async def candles(self, inst, interval: str, limit: int):
            if inst.id.symbol == "COMPUSDT":
                hours = 1 if interval == "1h" else 4
                return [replace(comp_candle, opened_at=now - timedelta(hours=hours * (limit - i)),
                                closed_at=now - timedelta(hours=hours * (limit - i - 1))) for i in range(limit)]
            raise RuntimeError("SOL market data unavailable")

    runtime = MagicMock()
    runtime.settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime.futures_market = FakeFutures()
    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(portal, "_cached_instruments", [inst_comp, inst_sol])
    portal._watchlist_service._telemetry_cache.clear()
    portal._watchlist_service._price_map_cache.clear()

    app = create_app(runtime.settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        # User A has target_1 below verified live COMP price (24.19) -> TARGET_REACHED
        res_a = await client.post(
            "/api/watchlist/audit",
            json={
                "items": [
                    {
                        "symbol": "COMPUSDT",
                        "name": "Compound",
                        "sector": "DeFi",
                        "stage": "VCP: Volatility Compression",
                        "timeframe": "1–3 Days",
                        "current_price": 23.0,
                        "trigger_price": 23.5,
                        "support_price": 22.8,
                        "invalidation_price": 22.0,
                        "target_1": 24.0,
                        "target_2": 26.0,
                        "distance_to_trigger_pct": 2.0,
                        "score": 65.0,
                        "thesis": "User A personal setup",
                    }
                ],
                "refresh": True,
            },
        )
        # User B has invalidation_price above verified live COMP price (25.0 >= 24.19) -> STOP_BREACHED
        res_b = await client.post(
            "/api/watchlist/audit",
            json={
                "items": [
                    {
                        "symbol": "COMPUSDT",
                        "name": "Compound",
                        "sector": "DeFi",
                        "stage": "VCP: Volatility Compression",
                        "timeframe": "1–3 Days",
                        "current_price": 25.5,
                        "trigger_price": 26.5,
                        "support_price": 25.2,
                        "invalidation_price": 25.0,
                        "target_1": 28.0,
                        "target_2": 30.0,
                        "distance_to_trigger_pct": 3.9,
                        "score": 65.0,
                        "thesis": "User B personal setup",
                    }
                ],
                "refresh": True,
            },
        )
        # W1 regression: User A sends SOLUSDT with reference price 200 while market fails ->
        # must return DATA_UNAVAILABLE (not 200) and must NOT poison User B (reference price 100)
        sol_a = await client.post(
            "/api/watchlist/audit",
            json={"items": [{"symbol": "SOLUSDT", "current_price": 200.0, "trigger_price": 210.0, "support_price": 190.0, "invalidation_price": 180.0, "target_1": 220.0, "target_2": 240.0}]},
        )
        sol_b = await client.post(
            "/api/watchlist/audit",
            json={"items": [{"symbol": "SOLUSDT", "current_price": 100.0, "trigger_price": 110.0, "support_price": 90.0, "invalidation_price": 80.0, "target_1": 120.0, "target_2": 140.0}]},
        )
        # Malformed or abusive payloads are rejected (422)
        bad_symbol = await client.post(
            "/api/watchlist/audit",
            json={"items": [{"symbol": "../etc/passwd", "current_price": 1.0}]},
        )
        bad_price = await client.post(
            "/api/watchlist/audit",
            json={"items": [{"symbol": "BTCUSDT", "current_price": -5.0}]},
        )
        oversized_batch = await client.post(
            "/api/watchlist/audit",
            json={"items": [{"symbol": f"SYM{i}USDT"} for i in range(30)]},
        )

    assert res_a.status_code == 200
    assert res_b.status_code == 200
    assert res_a.json()["items"][0]["audit"]["status"] == "TARGET_REACHED"
    assert res_b.json()["items"][0]["audit"]["status"] == "STOP_BREACHED"

    # W1 verification: no synthetic fallback, no cross-user reference price poisoning
    assert sol_a.status_code == 200 and sol_b.status_code == 200
    assert sol_a.json()["items"][0]["audit"]["status"] == "DATA_UNAVAILABLE"
    assert sol_a.json()["items"][0]["audit"]["live_price"] is None
    assert sol_a.json()["items"][0]["audit"]["audited_at"] is None
    assert sol_b.json()["items"][0]["audit"]["status"] == "DATA_UNAVAILABLE"
    assert sol_b.json()["items"][0]["audit"]["live_price"] is None
    assert sol_b.json()["items"][0]["current_price"] == 100.0
    assert "SOLUSDT" not in portal._watchlist_service._telemetry_cache

    assert bad_symbol.status_code == 422
    assert bad_price.status_code == 422
    assert oversized_batch.status_code == 422


@pytest.mark.asyncio
async def test_w4_unauthenticated_forced_refresh_blocked_on_watchlist_endpoints(monkeypatch):
    """W4: Anonymous forced refresh=true on /api/watchlist/auto, /api/watchlist, /api/watchlist/audit returns 401."""
    settings = Settings(_env_file=None, operator_token="o" * 40, screener_cron_enabled=False)
    monkeypatch.setattr(portal, "load_settings", lambda: settings)
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        r_auto = await client.get("/api/watchlist/auto?refresh=true")
        r_list = await client.get("/api/watchlist?refresh=true")
        r_audit = await client.get("/api/watchlist/audit?refresh=true")
    assert (r_auto.status_code, r_list.status_code, r_audit.status_code) == (401, 401, 401)


@pytest.mark.asyncio
async def test_singleflight_coalescing_and_unlisted_symbol_cache_poisoning_prevention(tmp_path):
    """Concurrent requests for a listed symbol coalesce into 1 upstream fetch, while unlisted symbols never allocate cache entries."""
    svc = WatchlistService(tmp_path / "watchlist.sqlite3")
    inst = make_instrument(symbol="SOLUSDT", venue=Venue.BINANCE_USDM)
    now = datetime.now(UTC)
    candle = Candle(
        opened_at=now - timedelta(hours=1),
        closed_at=now,
        open=Decimal("180"),
        high=Decimal("186"),
        low=Decimal("179"),
        close=Decimal("185"),
        volume=Decimal("1000"),
        closed=True,
    )

    call_count = 0

    class SlowMarket:
        async def quotes(self, instruments):
            return [Quote(inst.id, Decimal("184.9"), Decimal("185.1"), Decimal("185"), Decimal("100000"), now)]

        async def candles(self, instrument, interval: str, limit: int):
            nonlocal call_count
            if interval == "1h":
                call_count += 1
            await asyncio.sleep(0.05)
            return [candle] * limit

    market = SlowMarket()
    listed_item = WatchlistItem(
        symbol="SOLUSDT",
        name="Solana",
        sector="L1",
        stage=WyckoffStage.VCP_CONTRACTION,
        timeframe="1–3 Days",
        current_price=Decimal("185"),
        trigger_price=Decimal("192"),
        support_price=Decimal("181"),
        invalidation_price=Decimal("175"),
        target_1=Decimal("210"),
        target_2=Decimal("230"),
        distance_to_trigger_pct=Decimal("3.7"),
        score=Decimal("80"),
        thesis="Test",
        checklist_passed=5,
        checklist_total=5,
        priority=WatchlistPriority.HIGH,
    )
    fake_items = [
        WatchlistItem(
            symbol=f"FAKE{i}USDT",
            name="Fake",
            sector="Fake",
            stage=WyckoffStage.COIL_IMMINENT,
            timeframe="1–3 Days",
            current_price=Decimal("1"),
            trigger_price=Decimal("1.1"),
            support_price=Decimal("0.9"),
            invalidation_price=Decimal("0.8"),
            target_1=Decimal("1.2"),
            target_2=Decimal("1.3"),
            distance_to_trigger_pct=Decimal("10"),
            score=Decimal("50"),
            thesis="Cache poison attempt",
            checklist_passed=5,
            checklist_total=5,
            priority=WatchlistPriority.HIGH,
        )
        for i in range(20)
    ]

    # 10 concurrent callers auditing SOLUSDT at cold cache start -> singleflight must execute 1h candles only ONCE
    await asyncio.gather(
        *[
            svc.run_live_audit_for_items([listed_item], market, [inst])  # type: ignore[arg-type]
            for _ in range(10)
        ]
    )
    assert call_count == 1
    assert list(svc._telemetry_cache.keys()) == ["SOLUSDT"]

    # Auditing 20 unlisted/fake symbols must allocate 0 cache entries and make 0 market calls
    await svc.run_live_audit_for_items(fake_items, market, [inst])  # type: ignore[arg-type]
    assert call_count == 1
    assert list(svc._telemetry_cache.keys()) == ["SOLUSDT"]


