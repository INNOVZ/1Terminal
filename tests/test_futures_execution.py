from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.domain.errors import VenueError
from algotrader.domain.models import (
    Direction,
    MarketKind,
    MomentumState,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    ProtectionSet,
    TimeInForce,
    TradeSignal,
    Venue,
)
from tests.conftest import make_instrument


class FakeFuturesClient:
    def __init__(
        self,
        *,
        reject_second_algo_order: bool = False,
        cancellation_remains_visible: bool = False,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.reject_second_algo_order = reject_second_algo_order
        self.cancellation_remains_visible = cancellation_remains_visible
        self.algo_order_count = 0
        self.open_algo_orders: list[dict[str, object]] = []

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
        self.calls.append((path, params or {}))
        if path.endswith("positionSide/dual"):
            return {"dualSidePosition": True}
        if path.endswith("leverage"):
            return {"leverage": int(str((params or {})["leverage"]))}
        if path.endswith("openAlgoOrders"):
            return list(self.open_algo_orders)
        if path.endswith("algoOrder") and method == "DELETE":
            client_algo_id = str((params or {}).get("clientAlgoId", ""))
            if not self.cancellation_remains_visible:
                self.open_algo_orders = [
                    item
                    for item in self.open_algo_orders
                    if str(item.get("clientAlgoId", "")) != client_algo_id
                ]
            return {"clientAlgoId": client_algo_id, "algoStatus": "CANCELED"}
        if path.endswith("algoOrder") and method == "POST":
            self.algo_order_count += 1
            if self.reject_second_algo_order and self.algo_order_count == 2:
                raise VenueError("target rejected")
            result = {
                "symbol": "BTCUSDT",
                "clientAlgoId": (params or {})["clientAlgoId"],
                "algoId": 456,
                "algoStatus": "NEW",
                "positionSide": (params or {})["positionSide"],
            }
            self.open_algo_orders.append(result)
            return result
        return {
            "symbol": "BTCUSDT",
            "clientOrderId": (params or {})["newClientOrderId"],
            "orderId": 123,
            "status": "FILLED",
            "executedQty": "0.01",
            "avgPrice": "60000",
        }


async def test_futures_leverage_is_applied_before_first_order() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    intent = OrderIntent(
        instrument=instrument,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.01"),
        client_order_id="atl-test",
        direction=Direction.LONG,
        time_in_force=TimeInForce.IOC,
        price=Decimal("60010"),
    )
    await adapter.place_order(intent)
    assert [path for path, _ in client.calls] == [
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/leverage",
        "/fapi/v1/order",
    ]
    assert client.calls[1][1]["leverage"] == 2
    assert client.calls[2][1]["type"] == "LIMIT"
    assert client.calls[2][1]["timeInForce"] == "IOC"
    assert client.calls[2][1]["price"] == "60010"


async def test_futures_leverage_is_reapplied_before_each_new_entry() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    for suffix in ("one", "two"):
        await adapter.place_order(
            OrderIntent(
                instrument=instrument,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("0.01"),
                client_order_id=f"atl-{suffix}",
                direction=Direction.LONG,
                time_in_force=TimeInForce.IOC,
                price=Decimal("60010"),
            )
        )

    assert [path for path, _ in client.calls].count("/fapi/v1/leverage") == 2


async def test_futures_protection_uses_conditional_algo_orders() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("60000"),
        signal_bar_close=Decimal("60000"),
        atr=Decimal("1000"),
        stop_price=Decimal("58000"),
        target_price=Decimal("64000"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )

    results = await adapter.place_protection(signal, Decimal("0.01"))

    assert [path for path, _ in client.calls] == [
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/algoOrder",
        "/fapi/v1/algoOrder",
    ]
    assert [result.status for result in results] == [OrderStatus.NEW, OrderStatus.NEW]
    # Role marker sits between the signal id and the bracket-revision digest.
    assert [result.client_order_id.split("-")[2] for result in results] == ["sl", "tp"]
    stop_params = client.calls[1][1]
    target_params = client.calls[2][1]
    assert stop_params == {
        "algoType": "CONDITIONAL",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "positionSide": "LONG",
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "true",
        "newOrderRespType": "RESULT",
        "type": "STOP_MARKET",
        "triggerPrice": "58000",
        "clientAlgoId": stop_params["clientAlgoId"],
    }
    assert target_params["type"] == "TAKE_PROFIT_MARKET"
    assert target_params["triggerPrice"] == "64000"
    assert "stopPrice" not in stop_params
    assert "newClientOrderId" not in stop_params


async def test_futures_protection_normalizes_trigger_precision() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = replace(
        make_instrument(
            venue=Venue.BINANCE_USDM,
            market_kind=MarketKind.FUTURES,
            symbol="TUTUSDT",
        ),
        price_tick=Decimal("0.0000100"),
    )
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.LONG,
        score=Decimal("100"),
        entry_price=Decimal("0.0202700"),
        signal_bar_close=Decimal("0.0202700"),
        atr=Decimal("0.0004"),
        stop_price=Decimal("0.019641234"),
        target_price=Decimal("0.021527891"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )

    await adapter.place_protection(signal, Decimal("53297.5"))

    assert client.calls[1][1]["triggerPrice"] == "0.01965"
    assert client.calls[2][1]["triggerPrice"] == "0.02152"


async def test_futures_keeps_confirmed_stop_when_target_is_rejected() -> None:
    client = FakeFuturesClient(reject_second_algo_order=True)
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.SHORT,
        score=Decimal("90"),
        entry_price=Decimal("60000"),
        signal_bar_close=Decimal("60000"),
        atr=Decimal("1000"),
        stop_price=Decimal("62000"),
        target_price=Decimal("56000"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )

    results = await adapter.place_protection(signal, Decimal("0.01"))

    assert len(results) == 1
    # Ids carry a bracket-revision digest so an amended stop is not rejected as a duplicate.
    assert "-sl-" in results[0].client_order_id
    assert client.calls[1][1]["side"] == "BUY"
    assert client.calls[1][1]["positionSide"] == "SHORT"


async def test_futures_runner_replacement_has_stop_but_no_fixed_target() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("60000"),
        signal_bar_close=Decimal("60000"),
        atr=Decimal("1000"),
        stop_price=Decimal("61000"),
        target_price=Decimal("64000"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("runner",),
    )
    owned_client_id = f"atl-{signal.signal_id.hex[:12]}-sl-old"
    client.open_algo_orders = [
        {
            "symbol": "BTCUSDT",
            "clientAlgoId": owned_client_id,
            "algoId": 100,
            "positionSide": "LONG",
        },
        {
            "symbol": "BTCUSDT",
            "clientAlgoId": "manual-stop-must-survive",
            "algoId": 101,
            "positionSide": "LONG",
        },
        {
            "symbol": "BTCUSDT",
            "clientAlgoId": "atl-another-signal-sl-123456",
            "algoId": 102,
            "positionSide": "LONG",
        },
    ]

    protection = await adapter.replace_protection(
        signal,
        Decimal("0.005"),
        existing=ProtectionSet(),
        include_target=False,
    )

    assert [path for path, _ in client.calls] == [
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/algoOrder",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/algoOrder",
    ]
    assert client.calls[1][1] == {"clientAlgoId": owned_client_id}
    assert any(
        item["clientAlgoId"] == "manual-stop-must-survive" for item in client.open_algo_orders
    )
    assert any(
        item["clientAlgoId"] == "atl-another-signal-sl-123456" for item in client.open_algo_orders
    )
    assert protection.stop is not None
    assert protection.stop.quantity == Decimal("0.005")
    assert protection.target is None
    assert client.calls[-1][1]["type"] == "STOP_MARKET"


async def test_futures_replacement_refuses_to_race_a_visible_old_algo_order() -> None:
    client = FakeFuturesClient(cancellation_remains_visible=True)
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    signal = TradeSignal(
        instrument=instrument,
        direction=Direction.SHORT,
        score=Decimal("90"),
        entry_price=Decimal("60000"),
        signal_bar_close=Decimal("60000"),
        atr=Decimal("1000"),
        stop_price=Decimal("62000"),
        target_price=Decimal("56000"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("runner",),
    )
    client.open_algo_orders = [
        {
            "symbol": "BTCUSDT",
            "clientAlgoId": f"atl-{signal.signal_id.hex[:12]}-sl-old",
            "algoId": 100,
            "positionSide": "SHORT",
        }
    ]

    try:
        await adapter.replace_protection(
            signal,
            Decimal("0.005"),
            existing=ProtectionSet(),
            include_target=False,
        )
    except VenueError as exc:
        assert "remained open after cancellation" in str(exc)
    else:  # pragma: no cover - the assertion above is the behavior under test
        raise AssertionError("replacement must not race a still-visible conditional order")

    assert not any(
        path == "/fapi/v1/algoOrder" and "type" in params for path, params in client.calls
    )


async def test_futures_scale_out_keeps_existing_protection_until_fill_confirmed() -> None:
    client = FakeFuturesClient()
    adapter = BinanceUsdmAdapter(client, max_leverage=2)  # type: ignore[arg-type]
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )

    result = await adapter.reduce_position(
        instrument,
        Direction.LONG,
        Decimal("0.01"),
        client_order_id="atl-scale-out",
    )

    assert result.status is OrderStatus.FILLED
    assert [path for path, _ in client.calls] == ["/fapi/v1/order"]
    assert client.calls[0][1]["side"] == "SELL"
    assert client.calls[0][1]["positionSide"] == "LONG"
    assert client.calls[0][1]["type"] == "MARKET"
