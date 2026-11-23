import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from algotrader.api import portal
from algotrader.api.portal_app import create_app
from algotrader.application.watchlist_service import WatchlistService
from algotrader.config import Settings
from algotrader.domain.models import Candle, Venue
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
    """Two users watching the same symbol with different invalidation/target levels receive distinct audits."""
    runtime = MagicMock()
    runtime.settings = Settings(_env_file=None, screener_cron_enabled=False)
    runtime.futures_market = AsyncMock()
    runtime.futures_market.instruments.return_value = []
    monkeypatch.setattr(portal, "get_portal_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(portal, "_cached_instruments", [])

    app = create_app(runtime.settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        # User A has target_1 below current price (24.19 fallback for COMPUSDT) -> TARGET_REACHED
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
        # User B has invalidation_price above live price (25.0 >= 24.19) -> STOP_BREACHED
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
        # Malformed or abusive payloads (e.g. path traversal in symbol, negative prices, oversized batch) are rejected (422)
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
    audit_a = res_a.json()["items"][0]["audit"]
    audit_b = res_b.json()["items"][0]["audit"]
    assert audit_a["status"] == "TARGET_REACHED"
    assert audit_b["status"] == "STOP_BREACHED"
    assert bad_symbol.status_code == 422
    assert bad_price.status_code == 422
    assert oversized_batch.status_code == 422


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


