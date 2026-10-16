"""Applies :class:`PositionManager` decisions to a venue.

Split from the decision logic on purpose: ``PositionManager`` is a pure function of price and
structure, and everything that can fail against an exchange lives here. That keeps the exit
policy testable without a venue and keeps venue error handling out of the policy.

Failure posture matches the entry path. A protective order that cannot be confirmed is a
safety interlock, because the position is then live with unknown protection. Everything else
-- no structure yet, no quote this cycle, nothing to do -- leaves the position exactly as it
was, which is always safe: the ratchet never loosens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.application.position_manager import (
    ManagementPlan,
    MoveStop,
    PositionManager,
    TakePartialProfit,
)
from algotrader.config import Settings
from algotrader.domain.errors import SafetyInterlockError, UnknownExecutionStatus, VenueError
from algotrader.domain.indicators import atr as atr_series
from algotrader.domain.models import (
    Candle,
    Instrument,
    ManagementStage,
    MomentumState,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionManagementState,
    ProtectionSet,
    TradeRecord,
    TradeSignal,
)
from algotrader.domain.ports import ExecutionPort, ExecutionRepository, MarketDataPort
from algotrader.domain.structure import classify

MANAGEMENT_CANDLE_LIMIT = 260


@dataclass(frozen=True, slots=True)
class ManagementOutcome:
    trade: TradeRecord
    state: PositionManagementState
    actions: tuple[str, ...]


class PositionManagementCoordinator:
    def __init__(
        self,
        execution: ExecutionPort,
        market_data: MarketDataPort,
        repository: ExecutionRepository,
        manager: PositionManager,
        settings: Settings,
    ) -> None:
        self._execution = execution
        self._market_data = market_data
        self._repository = repository
        self._manager = manager
        self._settings = settings

    async def manage(
        self, trades: Sequence[TradeRecord], instruments: dict[str, Instrument]
    ) -> list[ManagementOutcome]:
        outcomes: list[ManagementOutcome] = []
        for trade in trades:
            instrument = instruments.get(str(trade.instrument_id))
            if instrument is None:
                continue
            outcome = await self._manage_one(trade, instrument)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    async def _manage_one(
        self, trade: TradeRecord, instrument: Instrument
    ) -> ManagementOutcome | None:
        state = await self._load_or_seed(trade)
        candles = await self._market_data.candles(instrument, "1h", MANAGEMENT_CANDLE_LIMIT)
        closed = [candle for candle in candles if candle.closed]
        if not closed:
            return None
        mark_price = await self._mark_price(instrument, closed)
        if mark_price is None:
            return None

        plan = self._manager.plan(
            state,
            mark_price=mark_price,
            atr=self._atr(closed),
            quantity_step=instrument.quantity_step,
            tick_size=instrument.price_tick,
            structure=classify(
                closed,
                left=self._settings.swing_pivot_left,
                right=self._settings.swing_pivot_right,
            ),
        )
        if not plan.changed:
            if plan.state != state:
                await self._repository.upsert_position_management(plan.state)
            return None

        applied, applied_state = await self._apply(
            trade, instrument, state, plan, mark_price=mark_price
        )
        await self._repository.upsert_position_management(
            replace(applied_state, updated_at=datetime.now(UTC))
        )
        return ManagementOutcome(trade=trade, state=applied_state, actions=applied)

    async def _apply(
        self,
        trade: TradeRecord,
        instrument: Instrument,
        current: PositionManagementState,
        plan: ManagementPlan,
        *,
        mark_price: Decimal,
    ) -> tuple[tuple[str, ...], PositionManagementState]:
        """Apply a plan as a confirmed state transition, never as an optimistic one."""
        applied: list[str] = []
        state = replace(current, best_price=plan.state.best_price)
        executed_partial_quantity = Decimal("0")
        partial = next(
            (action for action in plan.actions if isinstance(action, TakePartialProfit)), None
        )
        stop_moves = [action for action in plan.actions if isinstance(action, MoveStop)]

        if partial is not None:
            result = await self._take_partial(trade, instrument, partial)
            executed = result.executed_quantity
            if executed <= 0:
                await self._replace_protection(
                    trade,
                    instrument,
                    state,
                    stop_price=state.current_stop_price,
                    include_target=state.stage is ManagementStage.INITIAL,
                )
                message = "scale-out was not filled; original protection restored"
                applied.append(message)
                await self._repository.append_event(
                    "position_scale_out_not_filled",
                    {
                        "signal_id": str(trade.signal_id),
                        "instrument": str(trade.instrument_id),
                        "status": result.status.value,
                    },
                )
                return tuple(applied), state
            if executed > state.remaining_quantity or executed > partial.quantity:
                reason = "scale-out executed more quantity than the management intent allowed"
                await self._repository.set_kill_switch(True, reason)
                raise SafetyInterlockError(reason)

            fill_price = result.average_price or mark_price
            multiplier = Decimal("1") if trade.direction.value == "long" else Decimal("-1")
            realized = (fill_price - state.entry_price) * executed * multiplier
            state = replace(
                state,
                stage=ManagementStage.PARTIAL_TAKEN,
                remaining_quantity=state.remaining_quantity - executed,
                realized_partial_pnl=state.realized_partial_pnl + realized,
            )
            executed_partial_quantity = executed
            applied.append(partial.reason)

        # A plan can contain breakeven followed immediately by a structural trail when price
        # gaps through both thresholds. Only the final/tightest stop needs a venue round trip.
        if stop_moves:
            move = stop_moves[-1]
            await self._move_stop(
                trade,
                instrument,
                state,
                move,
                stage=plan.state.stage,
            )
            state = replace(
                state,
                stage=plan.state.stage,
                current_stop_price=move.stop_price,
            )
            applied.append(move.reason)
        elif partial is not None:
            # Even if breakeven is temporarily on the wrong side of a fast market, the runner
            # must immediately regain stop protection after the scale-out canceled its bracket.
            await self._replace_protection(
                trade,
                instrument,
                state,
                stop_price=state.current_stop_price,
                include_target=False,
            )
            applied.append("runner protected with the existing stop")

        if partial is not None:
            await self._repository.append_event(
                "position_scaled_out",
                {
                    "signal_id": str(trade.signal_id),
                    "instrument": str(trade.instrument_id),
                    "quantity": str(executed_partial_quantity),
                    "remaining_quantity": str(state.remaining_quantity),
                    "realized_partial_pnl": str(state.realized_partial_pnl),
                    "r_multiple": str(partial.r_multiple),
                    "reason": partial.reason,
                },
            )
        return tuple(applied), state

    async def _take_partial(
        self, trade: TradeRecord, instrument: Instrument, action: TakePartialProfit
    ) -> OrderResult:
        client_order_id = f"atl-{trade.signal_id.hex[:20]}-tp1"
        side = OrderSide.SELL if trade.direction.value == "long" else OrderSide.BUY
        intent = OrderIntent(
            instrument=instrument,
            side=side,
            order_type=OrderType.MARKET,
            quantity=action.quantity,
            client_order_id=client_order_id,
            direction=trade.direction,
            reduce_only=True,
            metadata={
                "management_action": "partial_take_profit",
                "signal_id": str(trade.signal_id),
                "r_multiple": str(action.r_multiple),
            },
        )
        intent_exists = await self._repository.has_order_intent(client_order_id)
        if intent_exists:
            recorded = await self._repository.get_latest_order_result(client_order_id)
            if recorded is not None:
                return await self._validate_scale_out_result(recorded)
            try:
                recovered = await self._execution.query_order(instrument.id, client_order_id)
            except (UnknownExecutionStatus, VenueError):
                recovered = None
            if recovered is None:
                reason = (
                    "durable scale-out intent has no recorded result and venue status is unknown"
                )
                await self._repository.set_kill_switch(True, reason)
                raise SafetyInterlockError(reason)
            await self._repository.record_order_result(recovered)
            return await self._validate_scale_out_result(recovered)

        if not intent_exists:
            await self._repository.record_order_intent(intent)
        try:
            result = await self._execution.reduce_position(
                instrument,
                trade.direction,
                action.quantity,
                client_order_id=client_order_id,
            )
        except UnknownExecutionStatus as exc:
            try:
                recovered = await self._execution.query_order(instrument.id, client_order_id)
            except VenueError:
                recovered = None
            if recovered is None:
                reason = f"scale-out status is unknown after protection was canceled: {exc}"
                await self._repository.set_kill_switch(True, reason)
                raise SafetyInterlockError(reason) from exc
            result = recovered
        except VenueError as exc:
            # A definite venue rejection means no reduction occurred, but the adapter may have
            # already canceled the bracket. Resolve the durable intent and let the caller restore
            # protection for the unchanged quantity.
            result = OrderResult(
                venue=self._execution.venue,
                symbol=instrument.id.symbol,
                client_order_id=client_order_id,
                exchange_order_id="",
                status=OrderStatus.REJECTED,
                executed_quantity=Decimal("0"),
                average_price=None,
                raw_status=str(exc)[:200],
            )
        await self._repository.record_order_result(result)
        return await self._validate_scale_out_result(result)

    async def _validate_scale_out_result(self, result: OrderResult) -> OrderResult:
        if result.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.UNKNOWN}:
            reason = f"scale-out returned non-terminal status {result.status.value}"
            await self._repository.set_kill_switch(True, reason)
            raise SafetyInterlockError(reason)
        return result

    async def _move_stop(
        self,
        trade: TradeRecord,
        instrument: Instrument,
        state: PositionManagementState,
        action: MoveStop,
        *,
        stage: ManagementStage,
    ) -> str:
        await self._replace_protection(
            trade,
            instrument,
            state,
            stop_price=action.stop_price,
            include_target=False,
        )
        await self._repository.append_event(
            "position_stop_moved",
            {
                "signal_id": str(trade.signal_id),
                "instrument": str(trade.instrument_id),
                "previous_stop": str(action.previous_stop_price),
                "new_stop": str(action.stop_price),
                "stage": stage.value,
                "reason": action.reason,
            },
        )
        return action.reason

    async def _replace_protection(
        self,
        trade: TradeRecord,
        instrument: Instrument,
        state: PositionManagementState,
        *,
        stop_price: Decimal,
        include_target: bool,
    ) -> ProtectionSet:
        signal = self._protection_signal(trade, instrument, state, stop_price)
        try:
            protection = await self._execution.replace_protection(
                signal,
                state.remaining_quantity,
                existing=await self._existing_protection(trade),
                include_target=include_target,
            )
        except (UnknownExecutionStatus, VenueError) as exc:
            reason = f"protective stop amendment failed: {exc}"
            await self._repository.set_kill_switch(True, reason)
            raise SafetyInterlockError(
                f"stop could not be moved and protection is unconfirmed: {exc}"
            ) from exc
        error = self._protection_error(
            protection,
            state.remaining_quantity,
            include_target=include_target,
        )
        if error is not None:
            reason = f"replacement protection is incomplete: {error}"
            await self._repository.set_kill_switch(True, reason)
            raise SafetyInterlockError(reason)
        for order in protection:
            await self._repository.record_order_result(order)
        return protection

    @staticmethod
    def _protection_error(
        protection: ProtectionSet, quantity: Decimal, *, include_target: bool
    ) -> str | None:
        if protection.stop is None:
            return "stop order is missing"
        if protection.stop.result.status is not OrderStatus.NEW:
            return "stop order was not accepted"
        if protection.stop.quantity != quantity:
            return "stop quantity does not match the runner"
        if include_target:
            if protection.target is None:
                return "target order is missing"
            if protection.target.result.status is not OrderStatus.NEW:
                return "target order was not accepted"
            if protection.target.quantity != quantity:
                return "target quantity does not match the position"
        elif protection.target is not None:
            return "managed runner unexpectedly retained a fixed target"
        return None

    async def _existing_protection(self, trade: TradeRecord) -> ProtectionSet:
        del trade
        # Adapters resolve live protective orders from venue state; an empty set means
        # "cancel whatever is open for this symbol", which is the safe superset.
        return ProtectionSet()

    def _protection_signal(
        self,
        trade: TradeRecord,
        instrument: Instrument,
        state: PositionManagementState,
        stop_price: Decimal,
    ) -> TradeSignal:
        return TradeSignal(
            instrument=instrument,
            direction=trade.direction,
            score=Decimal("0"),
            entry_price=state.entry_price,
            signal_bar_close=state.entry_price,
            atr=Decimal("0"),
            stop_price=stop_price,
            target_price=state.target_price,
            strategy=trade.strategy,
            timeframe="1h/4h",
            momentum_state=MomentumState.GAINING,
            rationale=("protective bracket amendment",),
            signal_id=trade.signal_id,
        )

    async def _load_or_seed(self, trade: TradeRecord) -> PositionManagementState:
        existing = await self._repository.get_position_management(str(trade.signal_id))
        if existing is not None:
            return existing
        seeded = PositionManagementState(
            signal_id=trade.signal_id,
            instrument_id=trade.instrument_id,
            direction=trade.direction,
            stage=ManagementStage.INITIAL,
            entry_price=trade.entry_price,
            initial_stop_price=trade.stop_price,
            initial_quantity=trade.quantity,
            remaining_quantity=trade.quantity,
            current_stop_price=trade.stop_price,
            target_price=trade.target_price,
            best_price=trade.entry_price,
        )
        await self._repository.upsert_position_management(seeded)
        return seeded

    async def _mark_price(self, instrument: Instrument, closed: list[Candle]) -> Decimal | None:
        try:
            quotes = await self._market_data.quotes((instrument,))
        except VenueError:
            return closed[-1].close
        quote = next((item for item in quotes if item.instrument_id == instrument.id), None)
        return quote.mid if quote is not None else closed[-1].close

    def _atr(self, closed: list[Candle]) -> Decimal:
        values = atr_series(closed, 14)
        return values[-1] if values else Decimal("0")
