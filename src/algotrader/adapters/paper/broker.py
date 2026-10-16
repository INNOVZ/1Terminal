from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from decimal import Decimal
from uuid import uuid4

from algotrader.domain.fills import quote_limit_fill_price
from algotrader.domain.models import (
    AccountSnapshot,
    Candle,
    Direction,
    Instrument,
    InstrumentId,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProtectionOrder,
    ProtectionRole,
    ProtectionSet,
    Quote,
    TimeInForce,
    TradeSignal,
)
from algotrader.domain.ports import MarketDataPort


@dataclass
class _ProtectiveBracket:
    """Live protective orders for one instrument, evaluated on every price observation."""

    instrument: Instrument
    direction: Direction
    quantity: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    stop_client_order_id: str
    target_client_order_id: str | None


@dataclass(frozen=True, slots=True)
class SimulatedExit:
    instrument_id: InstrumentId
    direction: Direction
    quantity: Decimal
    exit_price: Decimal
    reason: str
    realized_pnl: Decimal
    fees: Decimal


class PaperBroker:
    """Execution simulator over any market-data adapter, including future IBKR data.

    Protective orders here are *evaluated*, not merely recorded. An earlier version stored
    the stop and target as inert ``NEW`` rows and left ``mark_price`` frozen at entry, so a
    paper position had no working stop at all: it could ride a move up and give the whole of
    it back without either leg ever firing. Any simulator whose brackets never trigger will
    report a strategy as profitable that would have been stopped out in the market, so
    ``settle`` is the core of this class rather than an extra.
    """

    def __init__(
        self,
        market_data: MarketDataPort,
        *,
        starting_equity: Decimal = Decimal("100000"),
        slippage_bps: Decimal = Decimal("2"),
        fee_bps: Decimal = Decimal("5"),
        stop_slippage_bps: Decimal = Decimal("5"),
    ) -> None:
        self._market_data = market_data
        self.venue = market_data.venue
        self._equity = starting_equity
        self._available = starting_equity
        self._peak = starting_equity
        self._slippage_bps = slippage_bps
        self._fee_bps = fee_bps
        self._stop_slippage_bps = stop_slippage_bps
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, OrderResult] = {}
        self._brackets: dict[str, _ProtectiveBracket] = {}
        self._instruments: dict[str, Instrument] = {}

    async def instruments(self) -> Sequence[Instrument]:
        return await self._market_data.instruments()

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return await self._market_data.quotes(instruments)

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        return await self._market_data.candles(instrument, interval, limit)

    async def account_snapshot(self) -> AccountSnapshot:
        await self.settle()
        return AccountSnapshot(
            venue=self.venue,
            equity=self._equity,
            available_balance=self._available,
            realized_pnl_today=Decimal("0"),
            peak_equity=self._peak,
            positions=tuple(self._positions.values()),
        )

    async def settle(self) -> tuple[SimulatedExit, ...]:
        """Mark open positions to market and fire any protective order that is through.

        Called before every account read so equity, unrealized PnL, and bracket state are
        never stale relative to the price the rest of the system is looking at.
        """
        if not self._positions:
            return ()
        instruments = [
            self._instruments[key] for key in self._positions if key in self._instruments
        ]
        if not instruments:
            return ()
        quotes = {
            str(quote.instrument_id): quote for quote in await self._market_data.quotes(instruments)
        }
        exits: list[SimulatedExit] = []
        for key in list(self._positions):
            quote = quotes.get(key)
            if quote is None:
                continue
            position = self._positions[key]
            mark = quote.bid if position.direction is Direction.LONG else quote.ask
            self._positions[key] = dc_replace(
                position,
                mark_price=mark,
                unrealized_pnl=self._directional_pnl(
                    position.direction, position.entry_price, mark, position.quantity
                ),
            )
            exit_event = self._trigger_bracket(key, quote)
            if exit_event is not None:
                exits.append(exit_event)
        self._peak = max(self._peak, self._equity)
        return tuple(exits)

    def _trigger_bracket(self, key: str, quote: Quote) -> SimulatedExit | None:
        bracket = self._brackets.get(key)
        position = self._positions.get(key)
        if bracket is None or position is None:
            return None

        slip = self._stop_slippage_bps / Decimal("10000")
        if bracket.direction is Direction.LONG:
            # Conservative ordering: when a bar could have touched both, the stop wins.
            if quote.bid <= bracket.stop_price:
                fill = bracket.stop_price * (Decimal("1") - slip)
                reason = "stop"
            elif bracket.target_price is not None and quote.ask >= bracket.target_price:
                fill = bracket.target_price
                reason = "target"
            else:
                return None
        else:
            if quote.ask >= bracket.stop_price:
                fill = bracket.stop_price * (Decimal("1") + slip)
                reason = "stop"
            elif bracket.target_price is not None and quote.bid <= bracket.target_price:
                fill = bracket.target_price
                reason = "target"
            else:
                return None

        quantity = min(bracket.quantity, position.quantity)
        realized = self._directional_pnl(position.direction, position.entry_price, fill, quantity)
        fees = fill * quantity * self._fee_bps / Decimal("10000")
        self._settle_cash(realized, fees)
        self._reduce_position(key, quantity)
        self._brackets.pop(key, None)
        for client_order_id in (bracket.stop_client_order_id, bracket.target_client_order_id):
            if client_order_id is None:
                continue
            existing = self._orders.get(client_order_id)
            if existing is None:
                continue
            filled = client_order_id == (
                bracket.stop_client_order_id if reason == "stop" else bracket.target_client_order_id
            )
            self._orders[client_order_id] = dc_replace(
                existing,
                status=OrderStatus.FILLED if filled else OrderStatus.CANCELED,
                executed_quantity=quantity if filled else Decimal("0"),
                average_price=fill if filled else None,
                raw_status="FILLED" if filled else "CANCELED",
            )
        return SimulatedExit(
            instrument_id=bracket.instrument.id,
            direction=bracket.direction,
            quantity=quantity,
            exit_price=fill,
            reason=reason,
            realized_pnl=realized,
            fees=fees,
        )

    @staticmethod
    def _directional_pnl(
        direction: Direction, entry: Decimal, exit_price: Decimal, quantity: Decimal
    ) -> Decimal:
        multiplier = Decimal("1") if direction is Direction.LONG else Decimal("-1")
        return (exit_price - entry) * quantity * multiplier

    def _settle_cash(self, realized: Decimal, fees: Decimal) -> None:
        self._equity += realized - fees
        self._available += realized - fees
        self._peak = max(self._peak, self._equity)

    def _reduce_position(self, key: str, quantity: Decimal) -> None:
        position = self._positions.get(key)
        if position is None:
            return
        remaining = position.quantity - quantity
        if remaining <= 0:
            self._positions.pop(key, None)
            return
        self._positions[key] = dc_replace(position, quantity=remaining)

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        quotes = await self._market_data.quotes((intent.instrument,))
        quote = next((item for item in quotes if item.instrument_id == intent.instrument.id), None)
        if quote is None:
            raise ValueError(f"paper quote unavailable for {intent.instrument.id}")
        market_price = quote.ask if intent.side.value == "BUY" else quote.bid
        if intent.price is not None:
            fill_price = quote_limit_fill_price(
                side=intent.side,
                quote=quote,
                limit_price=intent.price,
            )
            if fill_price is None:
                status = (
                    OrderStatus.EXPIRED
                    if intent.time_in_force is TimeInForce.IOC
                    else OrderStatus.NEW
                )
                result = OrderResult(
                    venue=self.venue,
                    symbol=intent.instrument.id.symbol,
                    client_order_id=intent.client_order_id,
                    exchange_order_id=f"paper-{uuid4().hex}",
                    status=status,
                    executed_quantity=Decimal("0"),
                    average_price=None,
                    raw_status=status.value,
                )
                self._orders[intent.client_order_id] = result
                return result
        else:
            bps = self._slippage_bps / Decimal("10000")
            fill_price = market_price * (
                Decimal("1") + bps if intent.side.value == "BUY" else Decimal("1") - bps
            )
            fill_price = max(fill_price, intent.instrument.price_tick)
        fee = fill_price * intent.quantity * self._fee_bps / Decimal("10000")
        self._available -= fee
        self._equity -= fee
        key = str(intent.instrument.id)
        self._instruments[key] = intent.instrument
        if intent.reduce_only:
            position = self._positions.get(key)
            if position is not None:
                realized = self._directional_pnl(
                    position.direction, position.entry_price, fill_price, intent.quantity
                )
                self._equity += realized
                self._available += realized
                self._reduce_position(key, intent.quantity)
        else:
            self._positions[key] = Position(
                instrument_id=intent.instrument.id,
                direction=intent.direction,
                quantity=intent.quantity,
                entry_price=fill_price,
                mark_price=fill_price,
                unrealized_pnl=Decimal("0"),
            )
        result = OrderResult(
            venue=self.venue,
            symbol=intent.instrument.id.symbol,
            client_order_id=intent.client_order_id,
            exchange_order_id=f"paper-{uuid4().hex}",
            status=OrderStatus.FILLED,
            executed_quantity=intent.quantity,
            average_price=fill_price,
            raw_status="FILLED",
        )
        self._orders[intent.client_order_id] = result
        return result

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        return await self._place_protection(signal, quantity, include_target=True)

    async def _place_protection(
        self, signal: TradeSignal, quantity: Decimal, *, include_target: bool
    ) -> ProtectionSet:
        key = str(signal.instrument.id)
        self._instruments[key] = signal.instrument
        orders: dict[ProtectionRole, ProtectionOrder] = {}
        specifications = [(ProtectionRole.STOP, "sl", signal.stop_price)]
        if include_target:
            specifications.append((ProtectionRole.TARGET, "tp", signal.target_price))
        for role, suffix, trigger_price in specifications:
            client_order_id = f"paper-{signal.signal_id.hex[:20]}-{suffix}-{uuid4().hex[:6]}"
            result = OrderResult(
                venue=self.venue,
                symbol=signal.instrument.id.symbol,
                client_order_id=client_order_id,
                exchange_order_id=f"paper-{uuid4().hex}",
                status=OrderStatus.NEW,
                executed_quantity=Decimal("0"),
                average_price=None,
                raw_status="NEW",
            )
            self._orders[client_order_id] = result
            orders[role] = ProtectionOrder(role, result, trigger_price, quantity)

        target = orders.get(ProtectionRole.TARGET)
        self._brackets[key] = _ProtectiveBracket(
            instrument=signal.instrument,
            direction=signal.direction,
            quantity=quantity,
            stop_price=signal.stop_price,
            target_price=signal.target_price if include_target else None,
            stop_client_order_id=orders[ProtectionRole.STOP].result.client_order_id,
            target_client_order_id=(target.result.client_order_id if target is not None else None),
        )
        return ProtectionSet(
            stop=orders[ProtectionRole.STOP],
            target=target,
        )

    async def replace_protection(
        self,
        signal: TradeSignal,
        quantity: Decimal,
        *,
        existing: ProtectionSet,
        include_target: bool = True,
    ) -> ProtectionSet:
        for order in existing:
            current = self._orders.get(order.client_order_id)
            if current is not None and current.status is OrderStatus.NEW:
                await self.cancel_order(signal.instrument.id, order.client_order_id)
        self._brackets.pop(str(signal.instrument.id), None)
        return await self._place_protection(signal, quantity, include_target=include_target)

    async def reduce_position(
        self,
        instrument: Instrument,
        direction: Direction,
        quantity: Decimal,
        *,
        client_order_id: str,
    ) -> OrderResult:
        side = OrderSide.SELL if direction is Direction.LONG else OrderSide.BUY
        result = await self.place_order(
            OrderIntent(
                instrument=instrument,
                side=side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                client_order_id=client_order_id,
                direction=direction,
                reduce_only=True,
            )
        )
        bracket = self._brackets.get(str(instrument.id))
        if bracket is not None:
            bracket.quantity = max(Decimal("0"), bracket.quantity - quantity)
        return result

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id
        return self._orders.get(client_order_id)

    async def cancel_order(self, instrument_id: InstrumentId, client_order_id: str) -> OrderResult:
        del instrument_id
        current = self._orders[client_order_id]
        canceled = OrderResult(
            venue=current.venue,
            symbol=current.symbol,
            client_order_id=current.client_order_id,
            exchange_order_id=current.exchange_order_id,
            status=OrderStatus.CANCELED,
            executed_quantity=current.executed_quantity,
            average_price=current.average_price,
            raw_status="CANCELED",
        )
        self._orders[client_order_id] = canceled
        return canceled
