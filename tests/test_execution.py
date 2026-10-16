from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.execution import ExecutionCoordinator
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
    Instrument,
    InstrumentId,
    MomentumState,
    OrderIntent,
    OrderResult,
    OrderStatus,
    OrderType,
    ProtectionOrder,
    ProtectionRole,
    ProtectionSet,
    TimeInForce,
    TradeSignal,
    Venue,
)
from algotrader.infrastructure.sqlite_state import SqliteStateRepository
from tests.conftest import make_instrument


class UnknownExecution:
    venue = Venue.BINANCE_SPOT

    async def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            venue=self.venue,
            equity=Decimal("100000"),
            available_balance=Decimal("100000"),
            realized_pnl_today=Decimal("0"),
            peak_equity=Decimal("100000"),
        )

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        del intent
        raise UnknownExecutionStatus("timeout")

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        del signal, quantity
        return ProtectionSet()

    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        return None

    async def cancel_order(self, instrument_id: InstrumentId, client_order_id: str) -> OrderResult:
        raise NotImplementedError

    async def replace_protection(
        self,
        signal: TradeSignal,
        quantity: Decimal,
        *,
        existing: ProtectionSet,
        include_target: bool = True,
    ) -> ProtectionSet:
        del signal, quantity, existing, include_target
        raise NotImplementedError

    async def reduce_position(
        self,
        instrument: Instrument,
        direction: Direction,
        quantity: Decimal,
        *,
        client_order_id: str,
    ) -> OrderResult:
        del instrument, direction, quantity, client_order_id
        raise NotImplementedError


class ReconciliationFailure(UnknownExecution):
    async def query_order(
        self, instrument_id: InstrumentId, client_order_id: str
    ) -> OrderResult | None:
        del instrument_id, client_order_id
        raise VenueError("venue unavailable")


class ProtectionFailure(UnknownExecution):
    async def place_order(self, intent: OrderIntent) -> OrderResult:
        return OrderResult(
            venue=self.venue,
            symbol=intent.instrument.id.symbol,
            client_order_id=intent.client_order_id,
            exchange_order_id="filled-1",
            status=OrderStatus.FILLED,
            executed_quantity=intent.quantity,
            average_price=Decimal("100"),
            raw_status="FILLED",
        )

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        del signal, quantity
        raise VenueError("protection rejected")


class SuccessfulExecution(UnknownExecution):
    def __init__(self) -> None:
        self.intent: OrderIntent | None = None

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        self.intent = intent
        return OrderResult(
            venue=self.venue,
            symbol=intent.instrument.id.symbol,
            client_order_id=intent.client_order_id,
            exchange_order_id="filled-1",
            status=OrderStatus.FILLED,
            executed_quantity=intent.quantity,
            average_price=intent.price,
            raw_status="FILLED",
        )

    async def place_protection(self, signal: TradeSignal, quantity: Decimal) -> ProtectionSet:
        def protective_order(
            role: ProtectionRole, suffix: str, trigger_price: Decimal
        ) -> ProtectionOrder:
            result = OrderResult(
                venue=self.venue,
                symbol=signal.instrument.id.symbol,
                client_order_id=f"protection-{suffix}",
                exchange_order_id=f"protection-{suffix}",
                status=OrderStatus.NEW,
                executed_quantity=Decimal("0"),
                average_price=None,
                raw_status="NEW",
            )
            return ProtectionOrder(role, result, trigger_price, quantity)

        return ProtectionSet(
            stop=protective_order(ProtectionRole.STOP, "sl", signal.stop_price),
            target=protective_order(ProtectionRole.TARGET, "tp", signal.target_price),
        )


async def test_unresolved_entry_activates_persistent_kill_switch(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    coordinator = ExecutionCoordinator(
        UnknownExecution(),
        repository,
        PortfolioRiskManager(settings),  # type: ignore[arg-type]
    )
    trade_signal = TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )
    with pytest.raises(SafetyInterlockError, match="manual reconciliation"):
        await coordinator.execute(trade_signal)
    assert await repository.is_kill_switch_active()


async def test_entry_uses_price_capped_ioc_limit(settings: object) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    execution = SuccessfulExecution()
    coordinator = ExecutionCoordinator(
        execution,
        repository,
        PortfolioRiskManager(settings),  # type: ignore[arg-type]
    )
    trade_signal = TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("100"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )

    await coordinator.execute(trade_signal)

    assert execution.intent is not None
    assert execution.intent.order_type is OrderType.LIMIT
    assert execution.intent.time_in_force is TimeInForce.IOC
    assert execution.intent.price == Decimal("100.05")
    assert execution.intent.metadata["stop_price"] == "95"
    assert execution.intent.metadata["target_price"] == "110"
    assert Decimal(execution.intent.metadata["reward_risk_ratio"]) < Decimal("2")
    assert Decimal(execution.intent.metadata["planned_loss_at_stop"]) <= Decimal("250")
    assert Decimal(execution.intent.metadata["planned_profit_at_target"]) < Decimal("500")
    assert await repository.list_incomplete_execution_lifecycles() == ()


async def test_entry_limit_is_capped_at_half_atr_from_signal_close(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    execution = SuccessfulExecution()
    coordinator = ExecutionCoordinator(
        execution,
        repository,
        PortfolioRiskManager(settings),  # type: ignore[arg-type]
    )
    trade_signal = TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("100"),
        entry_price=Decimal("102.50"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        stop_price=Decimal("95"),
        target_price=Decimal("117.50"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )

    await coordinator.execute(trade_signal)

    assert execution.intent is not None
    assert execution.intent.price == Decimal("102.50")


async def test_expired_signal_is_rejected_before_account_or_order_calls(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    execution = SuccessfulExecution()
    coordinator = ExecutionCoordinator(
        execution,
        repository,
        PortfolioRiskManager(settings),  # type: ignore[arg-type]
    )
    trade_signal = TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("100"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    # An expired signal is a routine decision not to trade, not a safety interlock: no
    # venue state exists, so the cycle must be free to try the next candidate.
    with pytest.raises(TradeRejected, match="expired"):
        await coordinator.execute(trade_signal)

    assert execution.intent is None
    assert not await repository.is_kill_switch_active()


@pytest.mark.parametrize("execution", [ReconciliationFailure(), ProtectionFailure()])
async def test_post_submission_failures_activate_kill_switch(
    settings: object, execution: UnknownExecution
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    coordinator = ExecutionCoordinator(
        execution,
        repository,
        PortfolioRiskManager(settings),  # type: ignore[arg-type]
    )
    trade_signal = TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )
    with pytest.raises(SafetyInterlockError) as error:
        await coordinator.execute(trade_signal)
    assert await repository.is_kill_switch_active()
    if isinstance(execution, ProtectionFailure):
        assert "protection rejected" in str(error.value)
        kill_switch = await repository.get_state("kill_switch")
        assert kill_switch is not None
        assert "protection rejected" in str(kill_switch["reason"])
