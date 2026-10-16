from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.adapters.binance.base import (
    BinanceMarketDataBase,
    bracket_revision,
    parse_algo_order_result,
    parse_order_result,
)
from algotrader.adapters.binance.client import BinanceHttpClient, decimal_text
from algotrader.domain.errors import ConfigurationError, OrderRejected, VenueError
from algotrader.domain.models import (
    AccountSnapshot,
    DerivativesSnapshot,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    OrderIntent,
    OrderResult,
    OrderSide,
    Position,
    ProtectionOrder,
    ProtectionRole,
    ProtectionSet,
    TradeSignal,
    Venue,
    VenueFill,
)
from algotrader.domain.precision import normalize_bracket_prices, normalize_stop_price


class BinanceUsdmAdapter(BinanceMarketDataBase):
    venue = Venue.BINANCE_USDM
    market_kind = MarketKind.FUTURES
    exchange_info_path = "/fapi/v1/exchangeInfo"
    ticker_24h_path = "/fapi/v1/ticker/24hr"
    book_ticker_path = "/fapi/v1/ticker/bookTicker"
    klines_path = "/fapi/v1/klines"

    def __init__(
        self,
        client: BinanceHttpClient,
        *,
        require_hedge_mode: bool = True,
        max_leverage: int = 3,
    ) -> None:
        super().__init__(client)
        self._require_hedge_mode = require_hedge_mode
        self._max_leverage = max_leverage
        self._account_mode_validated = False

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        """Fetch public point-in-time derivatives features without API credentials.

        The calls are independent and run concurrently. No signal logic consumes these
        values yet: first they must demonstrate forward information out of sample.
        """
        symbol = instrument.id.symbol

        async def fetch_daily_oi() -> list[dict[str, object]]:
            try:
                res = await self._client.request(
                    "GET",
                    "/futures/data/openInterestHist",
                    params={"symbol": symbol, "period": "1d", "limit": 8},
                )
                return res if isinstance(res, list) else []
            except Exception:
                return []

        premium, open_interest, oi_history, oi_daily_history, taker, depth = await asyncio.gather(
            self._client.request("GET", "/fapi/v1/premiumIndex", params={"symbol": symbol}),
            self._client.request("GET", "/fapi/v1/openInterest", params={"symbol": symbol}),
            self._client.request(
                "GET",
                "/futures/data/openInterestHist",
                params={"symbol": symbol, "period": "1h", "limit": 2},
            ),
            fetch_daily_oi(),
            self._client.request(
                "GET",
                "/futures/data/takerlongshortRatio",
                params={"symbol": symbol, "period": "1h", "limit": 1},
            ),
            self._client.request("GET", "/fapi/v1/depth", params={"symbol": symbol, "limit": 20}),
        )
        if not isinstance(premium, dict) or not isinstance(open_interest, dict):
            raise VenueError("Binance returned invalid derivatives snapshot objects")
        if not isinstance(oi_history, list) or not isinstance(taker, list):
            raise VenueError("Binance returned invalid derivatives history arrays")
        if not isinstance(depth, dict):
            raise VenueError("Binance returned an invalid order-book snapshot")

        mark = Decimal(str(premium.get("markPrice", "0")))
        index = Decimal(str(premium.get("indexPrice", "0")))
        basis = (mark - index) / index if index > 0 else Decimal("0")
        oi_change: Decimal | None = None
        if len(oi_history) >= 2:
            previous = Decimal(str(oi_history[-2].get("sumOpenInterestValue", "0")))
            current = Decimal(str(oi_history[-1].get("sumOpenInterestValue", "0")))
            if previous > 0:
                oi_change = (current - previous) / previous

        oi_change_7d: Decimal | None = None
        if isinstance(oi_daily_history, list) and len(oi_daily_history) >= 2:
            first_daily = Decimal(str(oi_daily_history[0].get("sumOpenInterestValue", "0")))
            latest_daily = Decimal(str(oi_daily_history[-1].get("sumOpenInterestValue", "0")))
            if first_daily > 0:
                oi_change_7d = (latest_daily - first_daily) / first_daily

        taker_item = taker[-1] if taker else {}

        def depth_notional(side: object) -> Decimal:
            if not isinstance(side, list):
                return Decimal("0")
            return sum(
                (
                    Decimal(str(level[0])) * Decimal(str(level[1]))
                    for level in side
                    if isinstance(level, list) and len(level) >= 2
                ),
                Decimal("0"),
            )

        return DerivativesSnapshot(
            funding_rate=Decimal(str(premium.get("lastFundingRate", "0"))),
            basis_rate=basis,
            open_interest=Decimal(str(open_interest.get("openInterest", "0"))),
            open_interest_change_1h=oi_change,
            taker_buy_sell_ratio=Decimal(str(taker_item.get("buySellRatio", "1"))),
            bid_depth_notional=depth_notional(depth.get("bids")),
            ask_depth_notional=depth_notional(depth.get("asks")),
            open_interest_change_7d=oi_change_7d,
        )

    async def _validate_position_mode(self) -> None:
        if self._account_mode_validated:
            return
        payload = await self._client.request("GET", "/fapi/v1/positionSide/dual", signed=True)
        hedge_enabled = bool(payload.get("dualSidePosition"))
        if self._require_hedge_mode and not hedge_enabled:
            raise ConfigurationError(
                "USD-M Futures account must use Hedge Mode for independent long/short positions"
            )
        self._account_mode_validated = True

    async def _ensure_leverage(self, symbol: str) -> None:
        # Enforce before every new entry. Leverage can be changed out-of-band through
        # Binance, so a process-lifetime cache is not an authoritative safety control.
        payload = await self._client.request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": self._max_leverage},
            signed=True,
        )
        applied = int(payload.get("leverage", 0))
        if applied != self._max_leverage:
            raise ConfigurationError(
                f"venue applied leverage {applied}, expected {self._max_leverage}"
            )

    async def account_snapshot(self) -> AccountSnapshot:
        # The demo testnet may not support /fapi/v3/account; fall back to v2.
        try:
            payload = await self._client.request("GET", "/fapi/v3/account", signed=True)
        except VenueError:
            payload = await self._client.request("GET", "/fapi/v2/account", signed=True)
        positions: list[Position] = []
        for item in payload.get("positions", []):
            amount = Decimal(str(item.get("positionAmt", "0")))
            if amount == 0:
                continue
            side = str(item.get("positionSide", "BOTH"))
            direction = Direction.SHORT if side == "SHORT" or amount < 0 else Direction.LONG
            positions.append(
                Position(
                    instrument_id=InstrumentId(self.venue, str(item["symbol"])),
                    direction=direction,
                    quantity=abs(amount),
                    entry_price=Decimal(str(item.get("entryPrice", "0"))),
                    mark_price=Decimal(str(item.get("markPrice", item.get("entryPrice", "0")))),
                    unrealized_pnl=Decimal(str(item.get("unrealizedProfit", "0"))),
                )
            )
        equity = Decimal(
            str(payload.get("totalMarginBalance", payload.get("totalWalletBalance", "0")))
        )
        return AccountSnapshot(
            venue=self.venue,
            equity=equity,
            available_balance=Decimal(str(payload.get("availableBalance", "0"))),
            realized_pnl_today=Decimal("0"),
            peak_equity=equity,
            positions=tuple(positions),
        )

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        await self._validate_position_mode()
        await self._ensure_leverage(intent.instrument.id.symbol)
        params: dict[str, object] = {
            "symbol": intent.instrument.id.symbol,
            "side": intent.side.value,
            "positionSide": intent.direction.value.upper(),
            "type": intent.order_type.value,
            "quantity": decimal_text(intent.quantity),
            "newClientOrderId": intent.client_order_id,
            "newOrderRespType": "RESULT",
        }
        if intent.price is not None:
            params.update(
                price=decimal_text(intent.price),
                timeInForce=(intent.time_in_force.value if intent.time_in_force else "GTC"),
            )
        if intent.stop_price is not None:
            params["stopPrice"] = decimal_text(intent.stop_price)
        payload = await self._client.request(
            "POST", "/fapi/v1/order", params=params, signed=True, order_submission=True
        )
        return parse_order_result(payload, self.venue, intent.instrument.id.symbol)

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        return await self._place_protection(signal, quantity, include_target=True)

    async def _place_protection(
        self, signal: TradeSignal, quantity: Decimal, *, include_target: bool
    ) -> ProtectionSet:
        await self._validate_position_mode()
        try:
            if include_target:
                stop_price, target_price = normalize_bracket_prices(
                    direction=signal.direction,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    tick_size=signal.instrument.price_tick,
                )
            else:
                stop_price = normalize_stop_price(
                    direction=signal.direction,
                    stop_price=signal.stop_price,
                    tick_size=signal.instrument.price_tick,
                )
                target_price = None
        except ValueError as exc:
            raise OrderRejected(f"invalid USD-M protective bracket: {exc}") from exc
        side = OrderSide.SELL if signal.direction is Direction.LONG else OrderSide.BUY
        common = {
            "algoType": "CONDITIONAL",
            "symbol": signal.instrument.id.symbol,
            "side": side.value,
            "positionSide": signal.direction.value.upper(),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
            "newOrderRespType": "RESULT",
        }
        protection = ProtectionSet()
        specifications = [(ProtectionRole.STOP, "sl", "STOP_MARKET", stop_price)]
        if include_target:
            if target_price is None:
                raise OrderRejected("USD-M target protection was requested without a target price")
            specifications.append((ProtectionRole.TARGET, "tp", "TAKE_PROFIT_MARKET", target_price))
        for role, suffix, order_type, trigger_price in specifications:
            params = {
                **common,
                "type": order_type,
                "triggerPrice": decimal_text(trigger_price),
                # The revision digest lets an amended bracket take a fresh algo id while a
                # retry of the same amendment stays idempotent.
                "clientAlgoId": (
                    f"atl-{signal.signal_id.hex[:12]}-{suffix}-"
                    f"{bracket_revision(stop_price, target_price)}"
                ),
            }
            try:
                payload = await self._client.request(
                    "POST",
                    "/fapi/v1/algoOrder",
                    params=params,
                    signed=True,
                    order_submission=True,
                )
            except VenueError:
                if protection.stop is not None:
                    # The stop is already active. Return it so the coordinator can halt
                    # new entries without flattening a protected position blindly.
                    return protection
                raise
            order = ProtectionOrder(
                role=role,
                result=parse_algo_order_result(payload, self.venue, signal.instrument.id.symbol),
                trigger_price=trigger_price,
                quantity=quantity,
            )
            protection = (
                ProtectionSet(stop=order, target=protection.target)
                if role is ProtectionRole.STOP
                else ProtectionSet(stop=protection.stop, target=order)
            )
        return protection

    async def replace_protection(
        self,
        signal: TradeSignal,
        quantity: Decimal,
        *,
        existing: ProtectionSet,
        include_target: bool = True,
    ) -> ProtectionSet:
        """Cancel the open conditional orders on the symbol, then re-place the bracket.

        USD-M protection is two independent ``closePosition`` algo orders, so there is no
        atomic amend. Cancelling both first guarantees a single stop is live at a time; the
        stop is re-placed before the target, preserving the ordering invariant from entry.
        """
        del existing
        await self._cancel_open_algo_orders(signal)
        return await self._place_protection(signal, quantity, include_target=include_target)

    async def _cancel_open_algo_orders(self, signal: TradeSignal) -> None:
        """Cancel and confirm only conditional orders owned by this ATLAS trade.

        Binance moved USD-M conditional orders to the Algo service. The regular
        ``/allOpenOrders`` endpoint does not cancel them, which leaves the old
        ``closePosition`` order live and makes its replacement fail with ``-4130``.

        A symbol-wide Algo cancel would fix that error but would also delete manual orders
        and protection for another hedge-mode position. Client ids include the durable
        signal id, so query first and cancel only the exact trade's orders individually.
        """
        symbol = signal.instrument.id.symbol
        client_id_prefix = f"atl-{signal.signal_id.hex[:12]}-"

        def owned_orders(payload: object) -> list[dict[str, object]]:
            if not isinstance(payload, list):
                raise VenueError("Binance returned invalid open Algo orders payload")
            return [
                item
                for item in payload
                if isinstance(item, dict)
                and str(item.get("symbol", symbol)) == symbol
                and str(item.get("positionSide", signal.direction.value.upper()))
                == signal.direction.value.upper()
                and str(item.get("clientAlgoId", "")).startswith(client_id_prefix)
            ]

        async def current_owned_orders() -> list[dict[str, object]]:
            payload = await self._client.request(
                "GET",
                "/fapi/v1/openAlgoOrders",
                params={"symbol": symbol},
                signed=True,
            )
            return owned_orders(payload)

        for item in await current_owned_orders():
            client_algo_id = str(item.get("clientAlgoId", ""))
            if not client_algo_id:
                raise VenueError("open ATLAS Algo order has no clientAlgoId")
            try:
                await self._client.request(
                    "DELETE",
                    "/fapi/v1/algoOrder",
                    params={"clientAlgoId": client_algo_id},
                    signed=True,
                )
            except VenueError as exc:
                if "-2011" not in str(exc) and "Unknown order" not in str(exc):
                    raise

        # Algo cancellation is not guaranteed to be visible to a subsequent service call
        # immediately. Never submit the replacement while Binance still reports the old
        # closePosition order; that is the race that produces -4130.
        for attempt in range(4):
            if not await current_owned_orders():
                return
            if attempt < 3:
                await asyncio.sleep(0.05 * (2**attempt))
        raise VenueError(f"ATLAS Algo protection for {symbol} remained open after cancellation")

    async def reduce_position(
        self,
        instrument: Instrument,
        direction: Direction,
        quantity: Decimal,
        *,
        client_order_id: str,
    ) -> OrderResult:
        # Keep closePosition protection live during the market reduction. Unlike Spot OCO,
        # USD-M conditional orders do not reserve inventory; canceling them first creates an
        # avoidable unprotected window if the reduce response is delayed or ambiguous. The
        # coordinator cancels/replaces them after the fill is confirmed.
        side = OrderSide.SELL if direction is Direction.LONG else OrderSide.BUY
        payload = await self._client.request(
            "POST",
            "/fapi/v1/order",
            params={
                "symbol": instrument.id.symbol,
                "side": side.value,
                "positionSide": direction.value.upper(),
                "type": "MARKET",
                "quantity": decimal_text(quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
            signed=True,
            order_submission=True,
        )
        return parse_order_result(payload, self.venue, instrument.id.symbol)

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        payload = await self._client.request(
            "GET",
            "/fapi/v1/order",
            params={"symbol": instrument_id.symbol, "origClientOrderId": client_order_id},
            signed=True,
        )
        return parse_order_result(payload, self.venue, instrument_id.symbol)

    async def cancel_order(self, instrument_id: InstrumentId, client_order_id: str) -> OrderResult:
        payload = await self._client.request(
            "DELETE",
            "/fapi/v1/order",
            params={"symbol": instrument_id.symbol, "origClientOrderId": client_order_id},
            signed=True,
        )
        return parse_order_result(payload, self.venue, instrument_id.symbol)

    async def recent_fills(
        self, instrument_id: InstrumentId, *, since: datetime
    ) -> tuple[VenueFill, ...]:
        """Executed trades for a symbol, for reconciling a close against venue truth.

        The alternative is asking an operator to recall an exit price from memory. That
        figure lands in the ledger and the ledger drives the daily realised-loss breaker, so
        a guess does not merely misreport history -- it mis-calibrates a live risk control.
        """
        payload = await self._client.request(
            "GET",
            "/fapi/v1/userTrades",
            params={
                "symbol": instrument_id.symbol,
                "startTime": int(since.timestamp() * 1000),
                "limit": 1000,
            },
            signed=True,
        )
        return tuple(
            VenueFill(
                symbol=str(item["symbol"]),
                side=str(item.get("side", "")),
                price=Decimal(str(item["price"])),
                quantity=Decimal(str(item["qty"])),
                commission=Decimal(str(item.get("commission", "0"))),
                commission_asset=str(item.get("commissionAsset", "")),
                realized_pnl=Decimal(str(item.get("realizedPnl", "0"))),
                traded_at=datetime.fromtimestamp(int(item["time"]) / 1000, tz=UTC),
            )
            for item in payload
        )
