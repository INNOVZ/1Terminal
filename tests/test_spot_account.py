from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.domain.errors import ConfigurationError, SafetyInterlockError, VenueError
from algotrader.domain.models import Direction, MomentumState, ProtectionSet, TradeSignal, Venue
from algotrader.infrastructure.sqlite_state import SqliteStateRepository
from tests.conftest import make_instrument


class FakeClient:
    def __init__(
        self,
        prices: list[dict[str, str]],
        balances: list[dict[str, str]] | None = None,
        open_orders: list[dict[str, str]] | None = None,
    ) -> None:
        self._prices = prices
        self._balances = balances or [
            {"asset": "USDT", "free": "500", "locked": "100"},
            {"asset": "BTC", "free": "0.01", "locked": "0.01"},
        ]
        self._open_orders = open_orders or []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        signed: bool = False,
        order_submission: bool = False,
    ) -> Any:
        del method, params, signed, order_submission
        if path == "/api/v3/account":
            return {"balances": self._balances}
        if path == "/api/v3/openOrders":
            return self._open_orders
        return self._prices


class FakeProtectionClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        signed: bool = False,
        order_submission: bool = False,
    ) -> Any:
        del signed, order_submission
        request_params = params or {}
        self.calls.append((method, path, request_params))
        if method == "DELETE":
            return []
        return {
            "symbol": request_params["symbol"],
            "clientOrderId": request_params["newClientOrderId"],
            "orderId": 123,
            "status": "NEW",
            "executedQty": "0",
        }


async def test_spot_equity_includes_marked_inventory() -> None:
    client = cast(BinanceHttpClient, FakeClient([{"symbol": "BTCUSDT", "price": "60000"}]))
    adapter = BinanceSpotAdapter(client)
    account = await adapter.account_snapshot()
    assert account.available_balance == Decimal("500")
    assert account.equity == Decimal("1800")
    assert account.positions[0].quantity == Decimal("0.02")


async def test_spot_account_fails_closed_when_inventory_cannot_be_valued() -> None:
    adapter = BinanceSpotAdapter(cast(BinanceHttpClient, FakeClient([])))
    with pytest.raises(VenueError, match="cannot safely value"):
        await adapter.account_snapshot()


async def test_spot_account_can_explicitly_ignore_testnet_only_asset() -> None:
    client = FakeClient(
        [],
        balances=[
            {"asset": "USDT", "free": "500", "locked": "100"},
            {"asset": "这是测试币", "free": "1000", "locked": "0"},
        ],
    )
    adapter = BinanceSpotAdapter(
        cast(BinanceHttpClient, client),
        ignored_balance_assets=("这是测试币",),
    )

    account = await adapter.account_snapshot()

    assert account.available_balance == Decimal("500")
    assert account.equity == Decimal("600")
    assert account.positions == ()


async def test_spot_runner_replacement_uses_stop_only_order() -> None:
    client = FakeProtectionClient()
    adapter = BinanceSpotAdapter(cast(BinanceHttpClient, client))
    instrument = make_instrument(venue=Venue.BINANCE_SPOT)
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("2"),
        stop_price=Decimal("102"),
        target_price=Decimal("108"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("runner",),
    )

    protection = await adapter.replace_protection(
        signal,
        Decimal("5"),
        existing=ProtectionSet(),
        include_target=False,
    )

    assert [(method, path) for method, path, _ in client.calls] == [
        ("DELETE", "/api/v3/openOrders"),
        ("POST", "/api/v3/order"),
    ]
    params = client.calls[-1][2]
    assert params["type"] == "STOP_LOSS"
    assert params["quantity"] == "5"
    assert params["stopPrice"] == "102"
    assert protection.stop is not None
    assert protection.target is None


async def test_testnet_baseline_tracks_only_post_capture_inventory(
    tmp_path: Path,
) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    client = FakeClient(
        [{"symbol": "BTCUSDT", "price": "60000"}],
        balances=[
            {"asset": "USDT", "free": "500", "locked": "100"},
            {"asset": "BTC", "free": "1", "locked": "0"},
            {"asset": "这是测试币", "free": "1000", "locked": "0"},
        ],
    )
    adapter = BinanceSpotAdapter(
        cast(BinanceHttpClient, client),
        repository=repository,
        use_testnet_balance_baseline=True,
    )

    with pytest.raises(ConfigurationError, match="not initialized"):
        await adapter.account_snapshot()

    assert await adapter.capture_testnet_balance_baseline() == 3
    baseline_account = await adapter.account_snapshot()
    assert baseline_account.equity == Decimal("600")
    assert baseline_account.positions == ()

    client._balances = [
        {"asset": "USDT", "free": "400", "locked": "100"},
        {"asset": "BTC", "free": "1.01", "locked": "0"},
        {"asset": "这是测试币", "free": "1000", "locked": "0"},
    ]
    managed_account = await adapter.account_snapshot()
    assert managed_account.equity == Decimal("1100.00")
    assert managed_account.positions[0].quantity == Decimal("0.01")


async def test_testnet_baseline_refuses_open_orders_and_stale_inventory(
    tmp_path: Path,
) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    client = FakeClient(
        [{"symbol": "BTCUSDT", "price": "60000"}],
        balances=[
            {"asset": "USDT", "free": "500", "locked": "0"},
            {"asset": "BTC", "free": "1", "locked": "0"},
        ],
        open_orders=[{"symbol": "BTCUSDT"}],
    )
    adapter = BinanceSpotAdapter(
        cast(BinanceHttpClient, client),
        repository=repository,
        use_testnet_balance_baseline=True,
    )

    with pytest.raises(SafetyInterlockError, match="open Spot orders"):
        await adapter.capture_testnet_balance_baseline()

    client._open_orders = []
    await adapter.capture_testnet_balance_baseline()
    with pytest.raises(ConfigurationError, match="already exists"):
        await adapter.capture_testnet_balance_baseline()

    client._balances = [
        {"asset": "USDT", "free": "500", "locked": "0"},
        {"asset": "BTC", "free": "0.99", "locked": "0"},
    ]
    with pytest.raises(VenueError, match="fell below the captured baseline"):
        await adapter.account_snapshot()
