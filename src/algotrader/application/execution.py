from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from algotrader.application.risk import PortfolioRiskManager
from algotrader.domain.errors import (
    SafetyInterlockError,
    TradeRejected,
    UnknownExecutionStatus,
    VenueError,
)
from algotrader.domain.models import (
    AccountSnapshot,
    Direction,
    ExecutionLifecycle,
    ExecutionStage,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    ProtectionSet,
    RiskDecision,
    TimeInForce,
    TradeRecord,
    TradeSignal,
)
from algotrader.domain.ports import ExecutionPort, ExecutionRepository
from algotrader.domain.precision import capped_entry_limit_price


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    signal: TradeSignal
    entry: OrderResult
    protection: ProtectionSet


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    signal: TradeSignal
    intent: OrderIntent
    decision: RiskDecision
    leverage: Decimal
    limit_price: Decimal


@dataclass(frozen=True, slots=True)
class FilledEntry:
    signal: TradeSignal
    result: OrderResult
    lifecycle: ExecutionLifecycle
    breached_limit: bool
    price_uncertain: bool = False


class ExecutionCoordinator:
    def __init__(
        self,
        execution: ExecutionPort,
        repository: ExecutionRepository,
        risk_manager: PortfolioRiskManager,
        portfolio_account_provider: Callable[[], Awaitable[AccountSnapshot]] | None = None,
    ) -> None:
        self._execution = execution
        self._repository = repository
        self._risk = risk_manager
        self._portfolio_account_provider = portfolio_account_provider

    async def execute(self, signal: TradeSignal) -> ExecutionReport:
        plan = await self._build_plan(signal)
        lifecycle = await self._persist_intent(plan)
        entry = await self._submit_entry(plan)
        filled = await self._record_entry_fill(plan, lifecycle, entry)
        protection, lifecycle = await self._place_protection(filled)
        await self._record_open_trade(plan, filled, protection, lifecycle)
        if filled.breached_limit or filled.price_uncertain:
            reason = (
                "exchange fill breached the capped IOC limit"
                if filled.breached_limit
                else "entry filled but the venue reported no usable average price"
            )
            await self._repository.set_kill_switch(True, reason)
            raise SafetyInterlockError(f"{reason}; position is protected and trading is halted")
        return ExecutionReport(signal=filled.signal, entry=entry, protection=protection)

    async def _build_plan(self, signal: TradeSignal) -> ExecutionPlan:
        self._ensure_signal_fresh(signal)
        if await self._repository.is_kill_switch_active():
            raise SafetyInterlockError("kill switch is active")
        limit_price = capped_entry_limit_price(
            direction=signal.direction,
            reference_price=signal.entry_price,
            signal_bar_close=signal.signal_bar_close,
            atr=signal.atr,
            tick_size=signal.instrument.price_tick,
            offset_bps=self._risk.entry_limit_offset_bps,
            max_entry_chase_atr=self._risk.max_entry_chase_atr,
        )
        venue_account = await self._execution.account_snapshot()
        portfolio_account = (
            await self._portfolio_account_provider()
            if self._portfolio_account_provider is not None
            else venue_account
        )
        decision = self._risk.evaluate(
            signal,
            portfolio_account,
            venue_account,
            executable_entry_price=limit_price,
        )
        if not decision.approved:
            raise TradeRejected(f"risk rejected trade: {decision.reason}")
        local_leverage = (
            Decimal(self._risk.max_futures_leverage)
            if signal.instrument.market_kind.value == "futures"
            else Decimal("1")
        )
        if decision.notional > venue_account.available_balance * local_leverage:
            raise TradeRejected("risk rejected trade: insufficient venue balance")
        self._ensure_signal_fresh(signal)
        side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
        client_order_id = f"atl-{signal.signal_id.hex[:24]}"
        intent = OrderIntent(
            instrument=signal.instrument,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=decision.quantity,
            client_order_id=client_order_id,
            direction=signal.direction,
            time_in_force=TimeInForce.IOC,
            price=limit_price,
            metadata={
                "reference_price": str(signal.entry_price),
                "strategy": signal.strategy,
                "momentum": signal.momentum_state.value,
                "approved_notional": str(decision.notional),
                "leverage": str(local_leverage),
                "estimated_margin": str(decision.notional / local_leverage),
                "entry_limit_price": str(limit_price),
                "stop_price": str(signal.stop_price),
                "target_price": str(signal.target_price),
                "risk_per_unit": str(decision.risk_per_unit),
                "reward_risk_ratio": str(decision.reward_risk_ratio),
                "planned_loss_at_stop": str(decision.quantity * decision.risk_per_unit),
                "planned_profit_at_target": str(
                    decision.quantity * abs(signal.target_price - limit_price)
                ),
            },
        )
        return ExecutionPlan(signal, intent, decision, local_leverage, limit_price)

    async def _persist_intent(self, plan: ExecutionPlan) -> ExecutionLifecycle:
        signal = plan.signal
        intent = plan.intent
        client_order_id = intent.client_order_id
        if await self._repository.has_order_intent(client_order_id):
            # The signal was already acted on; skipping it is correct, not a failure.
            raise TradeRejected("duplicate order intent")
        await self._repository.record_order_intent(intent)
        lifecycle = ExecutionLifecycle(
            signal_id=signal.signal_id,
            instrument_id=signal.instrument.id,
            direction=signal.direction,
            client_order_id=client_order_id,
            stage=ExecutionStage.INTENT_PERSISTED,
            updated_at=datetime.now(UTC),
        )
        await self._repository.record_execution_lifecycle(lifecycle)
        return lifecycle

    async def _submit_entry(self, plan: ExecutionPlan) -> OrderResult:
        try:
            entry = await self._execution.place_order(plan.intent)
        except UnknownExecutionStatus:
            try:
                recovered_entry = await self._execution.query_order(
                    plan.signal.instrument.id, plan.intent.client_order_id
                )
            except VenueError:
                recovered_entry = None
            if recovered_entry is None:
                await self._repository.set_kill_switch(True, "unresolved entry order status")
                raise SafetyInterlockError(
                    "entry status is unknown; trading halted for manual reconciliation"
                ) from None
            entry = recovered_entry
        await self._repository.record_order_result(entry)
        return entry

    async def _record_entry_fill(
        self,
        plan: ExecutionPlan,
        lifecycle: ExecutionLifecycle,
        entry: OrderResult,
    ) -> FilledEntry:
        if entry.executed_quantity <= Decimal("0"):
            await self._handle_zero_fill(lifecycle, entry)
        protected_quantity = entry.executed_quantity
        fill_price = entry.average_price
        price_uncertain = False
        if fill_price is None or fill_price <= 0:
            # The quantity is confirmed, so inventory exists. Halting here would leave it
            # with no stop -- the exact state that produced an orphaned, unprotected
            # position before. Recover the price if the venue can supply it; otherwise
            # protect first on the capped limit (an IOC cannot fill worse than its limit)
            # and halt afterwards, in `execute`.
            fill_price = await self._recover_fill_price(plan)
            price_uncertain = fill_price is None
            if fill_price is None:
                fill_price = plan.limit_price
        lifecycle = replace(
            lifecycle,
            stage=ExecutionStage.ENTRY_FILLED,
            updated_at=datetime.now(UTC),
            filled_quantity=protected_quantity,
            fill_price=fill_price,
        )
        await self._repository.record_execution_lifecycle(lifecycle)
        fill_beyond_limit = (
            fill_price > plan.limit_price
            if plan.signal.direction is Direction.LONG
            else fill_price < plan.limit_price
        )
        protected_signal = replace(plan.signal, entry_price=fill_price)
        return FilledEntry(protected_signal, entry, lifecycle, fill_beyond_limit, price_uncertain)

    async def _recover_fill_price(self, plan: ExecutionPlan) -> Decimal | None:
        """Ask the venue for the fill price once before giving up on it.

        The immediate order response can omit the average price even when quantity is
        confirmed; a follow-up query usually carries it.
        """
        try:
            refreshed = await self._execution.query_order(
                plan.signal.instrument.id, plan.intent.client_order_id
            )
        except (UnknownExecutionStatus, VenueError):
            return None
        if refreshed is None or refreshed.average_price is None:
            return None
        return refreshed.average_price if refreshed.average_price > 0 else None

    async def _handle_zero_fill(self, lifecycle: ExecutionLifecycle, entry: OrderResult) -> None:
        terminal = entry.status in {
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
        if terminal:
            # A capped IOC limit that does not fill is the system working as designed: no
            # venue state was created, so the cycle can safely try the next candidate.
            await self._repository.record_execution_lifecycle(
                replace(
                    lifecycle,
                    stage=ExecutionStage.ABORTED,
                    updated_at=datetime.now(UTC),
                )
            )
            raise TradeRejected(f"entry did not fill: {entry.raw_status}")
        await self._repository.set_kill_switch(
            True,
            "entry returned a non-terminal zero-fill status",
        )
        raise SafetyInterlockError(f"entry did not fill: {entry.raw_status}")

    async def _place_protection(
        self, filled: FilledEntry
    ) -> tuple[ProtectionSet, ExecutionLifecycle]:
        protected_signal = filled.signal
        protected_quantity = filled.result.executed_quantity
        lifecycle = filled.lifecycle
        try:
            protection = await self._execution.place_protection(
                protected_signal, protected_quantity
            )
        except (UnknownExecutionStatus, VenueError) as exc:
            reason = f"protective-order placement failed: {exc}"
            await self._repository.set_kill_switch(True, reason)
            raise SafetyInterlockError(
                f"protective orders are not confirmed; trading halted for reconciliation: {exc}"
            ) from exc
        for order in protection:
            await self._repository.record_order_result(order)
        if protection.stop is not None:
            lifecycle = replace(
                lifecycle,
                stage=ExecutionStage.STOP_CONFIRMED,
                updated_at=datetime.now(UTC),
                stop_order_id=protection.stop.result.exchange_order_id,
            )
            await self._repository.record_execution_lifecycle(lifecycle)
        if protection.target is not None:
            lifecycle = replace(
                lifecycle,
                stage=ExecutionStage.TARGET_CONFIRMED,
                updated_at=datetime.now(UTC),
                target_order_id=protection.target.result.exchange_order_id,
            )
            await self._repository.record_execution_lifecycle(lifecycle)
        protection_error = self._protection_error(
            protection,
            protected_signal,
            protected_quantity,
        )
        if protection_error is not None:
            await self._repository.set_kill_switch(True, "incomplete protective bracket")
            raise SafetyInterlockError(
                f"protective bracket is not confirmed: {protection_error}; new entries are halted"
            )
        return protection, lifecycle

    async def _record_open_trade(
        self,
        plan: ExecutionPlan,
        filled: FilledEntry,
        protection: ProtectionSet,
        lifecycle: ExecutionLifecycle,
    ) -> None:
        signal = plan.signal
        protected_signal = filled.signal
        entry = filled.result
        protected_quantity = entry.executed_quantity
        fill_price = protected_signal.entry_price
        try:
            await self._repository.record_trade_open(
                TradeRecord(
                    signal_id=signal.signal_id,
                    instrument_id=signal.instrument.id,
                    direction=signal.direction,
                    strategy=signal.strategy,
                    quantity=protected_quantity,
                    entry_price=fill_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    entry_order_id=entry.exchange_order_id,
                    protection_order_ids=tuple(order.exchange_order_id for order in protection),
                    opened_at=datetime.now(UTC),
                )
            )
        except Exception as exc:
            await self._repository.set_kill_switch(
                True, f"filled trade could not be persisted: {type(exc).__name__}"
            )
            raise SafetyInterlockError(
                "trade is protected but its lifecycle record failed; trading halted"
            ) from exc
        await self._repository.record_execution_lifecycle(
            replace(
                lifecycle,
                stage=ExecutionStage.TRACKED,
                updated_at=datetime.now(UTC),
            )
        )
        await self._repository.append_event(
            "trade_opened",
            {
                "signal_id": str(signal.signal_id),
                "instrument": str(signal.instrument.id),
                "direction": signal.direction.value,
                "momentum": signal.momentum_state.value,
                "quantity": str(protected_quantity),
                "approved_notional": str(plan.decision.notional),
                "leverage": str(plan.leverage),
                "estimated_margin": str(plan.decision.notional / plan.leverage),
                "stop_price": str(signal.stop_price),
                "target_price": str(signal.target_price),
                "actual_loss_at_stop": str(protected_quantity * protected_signal.risk_per_unit),
                "actual_profit_at_target": str(
                    protected_quantity * protected_signal.reward_per_unit
                ),
                "actual_reward_risk_ratio": str(protected_signal.reward_risk_ratio),
                "entry_order": entry.exchange_order_id,
            },
        )

    @staticmethod
    def _protection_error(
        protection: ProtectionSet,
        signal: TradeSignal,
        quantity: Decimal,
    ) -> str | None:
        if protection.stop is None:
            return "stop order is missing"
        if protection.target is None:
            return "target order is missing"
        orders = (protection.stop, protection.target)
        exchange_ids = [order.result.exchange_order_id for order in orders]
        valid_geometry = (
            protection.stop.trigger_price < signal.entry_price < protection.target.trigger_price
            if signal.direction is Direction.LONG
            else protection.target.trigger_price
            < signal.entry_price
            < protection.stop.trigger_price
        )
        checks = (
            (
                any(order.result.status is not OrderStatus.NEW for order in orders),
                "a protective order was not accepted",
            ),
            (
                any(not order_id for order_id in exchange_ids) or len(set(exchange_ids)) != 2,
                "protective order identifiers are missing or duplicated",
            ),
            (
                any(order.quantity != quantity for order in orders),
                "protective quantity does not match the entry fill",
            ),
            (not valid_geometry, "protective trigger geometry is invalid"),
        )
        return next((message for failed, message in checks if failed), None)

    @staticmethod
    def _ensure_signal_fresh(signal: TradeSignal) -> None:
        if signal.expires_at is None:
            return
        expires_at = signal.expires_at
        if expires_at.tzinfo is None:
            raise SafetyInterlockError("signal expiry timestamp must be timezone-aware")
        if datetime.now(UTC) >= expires_at.astimezone(UTC):
            raise TradeRejected("signal expired before order submission")
