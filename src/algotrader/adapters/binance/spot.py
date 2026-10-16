from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import structlog

from algotrader.adapters.binance.base import (
    BinanceMarketDataBase,
    bracket_revision,
    parse_order_result,
)
from algotrader.adapters.binance.client import BinanceHttpClient, decimal_text
from algotrader.domain.errors import (
    ConfigurationError,
    OrderRejected,
    SafetyInterlockError,
    VenueError,
)
from algotrader.domain.models import (
    AccountSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderType,
    Position,
    ProtectionOrder,
    ProtectionRole,
    ProtectionSet,
    TradeSignal,
    Venue,
)
from algotrader.domain.ports import SafetyStateStore
from algotrader.domain.precision import normalize_bracket_prices, normalize_stop_price

log = structlog.get_logger()
SPOT_TESTNET_BASELINE_KEY = "binance_spot_testnet_balance_baseline"


class BinanceSpotAdapter(BinanceMarketDataBase):
    venue = Venue.BINANCE_SPOT
    market_kind = MarketKind.SPOT
    exchange_info_path = "/api/v3/exchangeInfo"
    ticker_24h_path = "/api/v3/ticker/24hr"
    book_ticker_path = "/api/v3/ticker/bookTicker"
    klines_path = "/api/v3/klines"

    def __init__(
        self,
        client: BinanceHttpClient,
        *,
        ignored_balance_assets: Sequence[str] = (),
        repository: SafetyStateStore | None = None,
        use_testnet_balance_baseline: bool = False,
    ) -> None:
        super().__init__(client)
        self._ignored_balance_assets = frozenset(ignored_balance_assets)
        self._repository = repository
        self._use_testnet_balance_baseline = use_testnet_balance_baseline

    async def capture_testnet_balance_baseline(self, *, replace: bool = False) -> int:
        if not self._use_testnet_balance_baseline or self._repository is None:
            raise ConfigurationError("Spot balance baselines are available only in testnet mode")
        existing = await self._repository.get_state(SPOT_TESTNET_BASELINE_KEY)
        if existing is not None and not replace:
            raise ConfigurationError(
                "Spot Testnet balance baseline already exists; use the reset command to replace it"
            )
        open_orders = await self._client.request("GET", "/api/v3/openOrders", signed=True)
        if open_orders:
            raise SafetyInterlockError(
                "cannot capture Spot Testnet baseline while open Spot orders exist"
            )
        balances = await self._account_balances()
        totals = {
            asset: free + locked for asset, (free, locked) in balances.items() if free + locked > 0
        }
        await self._repository.set_state(
            SPOT_TESTNET_BASELINE_KEY,
            {
                "version": 1,
                "assets": {asset: str(quantity) for asset, quantity in totals.items()},
            },
        )
        log.info("spot_testnet_balance_baseline_captured", asset_count=len(totals))
        return len(totals)

    async def _account_balances(self) -> dict[str, tuple[Decimal, Decimal]]:
        payload = await self._client.request("GET", "/api/v3/account", signed=True)
        return {
            str(item["asset"]): (
                Decimal(str(item.get("free", "0"))),
                Decimal(str(item.get("locked", "0"))),
            )
            for item in payload.get("balances", [])
        }

    async def _managed_testnet_balances(
        self, balances: dict[str, tuple[Decimal, Decimal]]
    ) -> dict[str, Decimal]:
        if self._repository is None:
            raise ConfigurationError("Spot Testnet baseline repository is not configured")
        state = await self._repository.get_state(SPOT_TESTNET_BASELINE_KEY)
        if state is None:
            raise ConfigurationError(
                "Spot Testnet balance baseline is not initialized; "
                "run `atlas init-testnet-spot-baseline` before testnet execution"
            )
        encoded_assets = state.get("assets")
        if state.get("version") != 1 or not isinstance(encoded_assets, dict):
            raise VenueError(
                "Spot Testnet balance baseline is invalid; run `atlas reset-testnet-spot-baseline`"
            )
        try:
            baseline = {
                str(asset): Decimal(str(quantity)) for asset, quantity in encoded_assets.items()
            }
        except Exception as exc:
            raise VenueError(
                "Spot Testnet balance baseline contains invalid quantities; "
                "run `atlas reset-testnet-spot-baseline`"
            ) from exc

        current = {asset: free + locked for asset, (free, locked) in balances.items()}
        deficits = sorted(
            asset
            for asset, baseline_quantity in baseline.items()
            if asset != "USDT" and current.get(asset, Decimal("0")) < baseline_quantity
        )
        if deficits:
            names = ", ".join(deficits[:5])
            suffix = "..." if len(deficits) > 5 else ""
            raise VenueError(
                "Spot Testnet balances fell below the captured baseline for "
                f"{names}{suffix}; confirm there are no managed positions, then run "
                "`atlas reset-testnet-spot-baseline`"
            )
        return {
            asset: quantity - baseline.get(asset, Decimal("0"))
            for asset, quantity in current.items()
            if asset != "USDT" and quantity - baseline.get(asset, Decimal("0")) > 0
        }

    async def account_snapshot(self) -> AccountSnapshot:
        balances = await self._account_balances()
        usdt_free, usdt_locked = balances.get("USDT", (Decimal("0"), Decimal("0")))
        available = usdt_free
        equity = usdt_free + usdt_locked
        positions: list[Position] = []
        inventory = (
            await self._managed_testnet_balances(balances)
            if self._use_testnet_balance_baseline
            else {
                asset: free + locked
                for asset, (free, locked) in balances.items()
                if asset != "USDT" and free + locked > 0
            }
        )
        ignored_nonzero_assets = sorted(
            asset
            for asset, quantity in inventory.items()
            if asset in self._ignored_balance_assets and quantity > 0
        )
        for asset in ignored_nonzero_assets:
            log.warning("spot_balance_asset_ignored", asset=asset)
        non_usdt = {
            asset: quantity
            for asset, quantity in inventory.items()
            if asset not in self._ignored_balance_assets and quantity > 0
        }
        if non_usdt:
            prices_payload = await self._client.request("GET", "/api/v3/ticker/price")
            prices = {str(item["symbol"]): Decimal(str(item["price"])) for item in prices_payload}
            for asset, quantity in non_usdt.items():
                symbol = f"{asset}USDT"
                mark = prices.get(symbol)
                if mark is None or mark <= 0:
                    raise VenueError(
                        f"cannot safely value nonzero Spot balance {asset} through {symbol}"
                    )
                equity += quantity * mark
                positions.append(
                    Position(
                        instrument_id=InstrumentId(self.venue, symbol),
                        direction=Direction.LONG,
                        quantity=quantity,
                        # Spot account does not expose tax-lot cost basis; mark is used only for
                        # portfolio exposure. Execution fills remain the source of entry price.
                        entry_price=mark,
                        mark_price=mark,
                        unrealized_pnl=Decimal("0"),
                    )
                )
        return AccountSnapshot(
            venue=self.venue,
            equity=equity,
            available_balance=available,
            realized_pnl_today=Decimal("0"),
            peak_equity=equity,
            positions=tuple(positions),
        )

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        if intent.direction is Direction.SHORT and not intent.reduce_only:
            raise OrderRejected("cash Spot cannot open short positions; use USD-M Futures")
        params: dict[str, object] = {
            "symbol": intent.instrument.id.symbol,
            "side": intent.side.value,
            "type": intent.order_type.value,
            "quantity": decimal_text(intent.quantity),
            "newClientOrderId": intent.client_order_id,
            "newOrderRespType": "FULL",
        }
        if intent.price is not None:
            params.update(
                price=decimal_text(intent.price),
                timeInForce=(intent.time_in_force.value if intent.time_in_force else "GTC"),
            )
        if intent.stop_price is not None:
            params["stopPrice"] = decimal_text(intent.stop_price)
        payload = await self._client.request(
            "POST", "/api/v3/order", params=params, signed=True, order_submission=True
        )
        return parse_order_result(payload, self.venue, intent.instrument.id.symbol)

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        if signal.direction is not Direction.LONG:
            raise OrderRejected("Spot protection only supports long inventory")
        try:
            stop_price, target_price = normalize_bracket_prices(
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                tick_size=signal.instrument.price_tick,
            )
        except ValueError as exc:
            raise OrderRejected(f"invalid Spot protective bracket: {exc}") from exc
        # Atomic OCO avoids double-reserving the same Spot inventory. The list id carries a
        # digest of the bracket so an amended stop gets a fresh id, while retrying the same
        # amendment reuses it and stays idempotent.
        list_id = f"atl-{signal.signal_id.hex[:14]}-{bracket_revision(stop_price, target_price)}"
        params = {
            "symbol": signal.instrument.id.symbol,
            "side": OrderSide.SELL.value,
            "quantity": decimal_text(quantity),
            "listClientOrderId": list_id,
            "aboveType": "LIMIT_MAKER",
            "abovePrice": decimal_text(target_price),
            "belowType": "STOP_LOSS",
            "belowStopPrice": decimal_text(stop_price),
            "newOrderRespType": "RESULT",
        }
        payload = await self._client.request(
            "POST", "/api/v3/orderList/oco", params=params, signed=True, order_submission=True
        )
        stop: ProtectionOrder | None = None
        target: ProtectionOrder | None = None
        for report in payload.get("orderReports", []):
            order_type = str(report.get("type", ""))
            result = parse_order_result(report, self.venue, signal.instrument.id.symbol)
            if order_type.startswith("STOP_LOSS"):
                stop = ProtectionOrder(ProtectionRole.STOP, result, stop_price, quantity)
            elif order_type in {"LIMIT_MAKER", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"}:
                target = ProtectionOrder(ProtectionRole.TARGET, result, target_price, quantity)
        return ProtectionSet(stop=stop, target=target)

    async def replace_protection(
        self,
        signal: TradeSignal,
        quantity: Decimal,
        *,
        existing: ProtectionSet,
        include_target: bool = True,
    ) -> ProtectionSet:
        """Cancel every open protective order on the symbol, then place a fresh OCO.

        Cancelling by symbol rather than by the ids in ``existing`` is deliberate: a partial
        scale-out or an earlier amendment can leave an OCO leg the caller does not know
        about, and two live stops against one inventory is the worse failure.
        """
        del existing
        await self._cancel_open_orders(signal.instrument.id.symbol)
        if include_target:
            return await self.place_protection(signal, quantity)
        return await self._place_stop_only(signal, quantity)

    async def _place_stop_only(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        """Protect a post-partial Spot runner without retaining the original 2R target."""
        if signal.direction is not Direction.LONG:
            raise OrderRejected("Spot protection only supports long inventory")
        try:
            stop_price = normalize_stop_price(
                direction=signal.direction,
                stop_price=signal.stop_price,
                tick_size=signal.instrument.price_tick,
            )
        except ValueError as exc:
            raise OrderRejected(f"invalid Spot runner stop: {exc}") from exc
        client_order_id = f"atl-{signal.signal_id.hex[:14]}-sl-{bracket_revision(stop_price)}"
        payload = await self._client.request(
            "POST",
            "/api/v3/order",
            params={
                "symbol": signal.instrument.id.symbol,
                "side": OrderSide.SELL.value,
                "type": "STOP_LOSS",
                "quantity": decimal_text(quantity),
                "stopPrice": decimal_text(stop_price),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
            signed=True,
            order_submission=True,
        )
        result = parse_order_result(payload, self.venue, signal.instrument.id.symbol)
        return ProtectionSet(
            stop=ProtectionOrder(ProtectionRole.STOP, result, stop_price, quantity)
        )

    async def _cancel_open_orders(self, symbol: str) -> None:
        try:
            await self._client.request(
                "DELETE",
                "/api/v3/openOrders",
                params={"symbol": symbol},
                signed=True,
            )
        except VenueError as exc:
            # Binance returns -2011 when there is nothing open; that is the desired state.
            if "-2011" not in str(exc) and "Unknown order" not in str(exc):
                raise

    async def reduce_position(
        self,
        instrument: Instrument,
        direction: Direction,
        quantity: Decimal,
        *,
        client_order_id: str,
    ) -> OrderResult:
        if direction is not Direction.LONG:
            raise OrderRejected("cash Spot holds only long inventory")
        # The protective OCO reserves the same inventory, so it must be released before a
        # scale-out can sell against it. The caller re-places protection for the remainder.
        await self._cancel_open_orders(instrument.id.symbol)
        return await self.place_order(
            OrderIntent(
                instrument=instrument,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=quantity,
                client_order_id=client_order_id,
                direction=direction,
                reduce_only=True,
            )
        )

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        payload = await self._client.request(
            "GET",
            "/api/v3/order",
            params={"symbol": instrument_id.symbol, "origClientOrderId": client_order_id},
            signed=True,
        )
        return parse_order_result(payload, self.venue, instrument_id.symbol)

    async def cancel_order(self, instrument_id: InstrumentId, client_order_id: str) -> OrderResult:
        payload = await self._client.request(
            "DELETE",
            "/api/v3/order",
            params={"symbol": instrument_id.symbol, "origClientOrderId": client_order_id},
            signed=True,
        )
        return parse_order_result(payload, self.venue, instrument_id.symbol)
