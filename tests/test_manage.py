from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from algotrader.application.manage import PositionManagementCoordinator
from algotrader.application.position_manager import PositionManager
from algotrader.domain.errors import SafetyInterlockError, UnknownExecutionStatus, VenueError
from algotrader.domain.models import (
    Candle,
    Direction,
    Instrument,
    ManagementStage,
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
    TradeRecord,
    Venue,
)
from algotrader.infrastructure.sqlite_state import SqliteStateRepository
from tests.conftest import make_instrument, trending_candles


class ManagementMarketData:
    venue = Venue.BINANCE_USDM

    def __init__(self, instrument: Instrument, mark_price: Decimal) -> None:
        self._instrument = instrument
        self._mark_price = mark_price

    async def instruments(self) -> tuple[Instrument, ...]:
        return (self._instrument,)

    async def candles(
        self, instrument: Instrument, interval: str, limit: int
    ) -> tuple[Candle, ...]:
        del instrument, interval
        return tuple(trending_candles(count=limit))

    async def quotes(self, instruments: object) -> tuple[Quote, ...]:
        del instruments
        return (
            Quote(
                instrument_id=self._instrument.id,
                bid=self._mark_price - Decimal("0.01"),
                ask=self._mark_price + Decimal("0.01"),
                last=self._mark_price,
                quote_volume_24h=Decimal("10000000"),
                observed_at=datetime(2026, 8, 10, tzinfo=UTC),
            ),
        )


class ManagementExecution:
    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        reduction: OrderResult | Exception,
        *,
        recovered: OrderResult | None = None,
    ) -> None:
        self._reduction = reduction
        self._recovered = recovered
        self.reduce_calls = 0
        self.replacements: list[tuple[Decimal, bool, Decimal]] = []

    async def reduce_position(
        self,
        instrument: Instrument,
        direction: Direction,
        quantity: Decimal,
        *,
        client_order_id: str,
    ) -> OrderResult:
        del instrument, direction, quantity, client_order_id
        self.reduce_calls += 1
        if isinstance(self._reduction, Exception):
            raise self._reduction
        return self._reduction

    async def query_order(self, instrument_id: object, client_order_id: str) -> OrderResult | None:
        del instrument_id, client_order_id
        return self._recovered

    async def replace_protection(
        self,
        signal: object,
        quantity: Decimal,
        *,
        existing: ProtectionSet,
        include_target: bool = True,
    ) -> ProtectionSet:
        del existing
        stop_price = signal.stop_price  # type: ignore[attr-defined]
        target_price = signal.target_price  # type: ignore[attr-defined]
        self.replacements.append((quantity, include_target, stop_price))

        def order(role: ProtectionRole, trigger: Decimal) -> ProtectionOrder:
            result = OrderResult(
                venue=self.venue,
                symbol="BTCUSDT",
                client_order_id=f"management-{role.value}-{len(self.replacements)}",
                exchange_order_id=f"exchange-{role.value}-{len(self.replacements)}",
                status=OrderStatus.NEW,
                executed_quantity=Decimal("0"),
                average_price=None,
                raw_status="NEW",
            )
            return ProtectionOrder(role, result, trigger, quantity)

        return ProtectionSet(
            stop=order(ProtectionRole.STOP, stop_price),
            target=(order(ProtectionRole.TARGET, target_price) if include_target else None),
        )


def trade(instrument: Instrument) -> TradeRecord:
    return TradeRecord(
        signal_id=uuid4(),
        instrument_id=instrument.id,
        direction=Direction.LONG,
        strategy="test",
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        stop_price=Decimal("97"),
        target_price=Decimal("106"),
        entry_order_id="entry-1",
        protection_order_ids=("stop-1", "target-1"),
        opened_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def reduction_result(status: OrderStatus, quantity: str, price: str | None) -> OrderResult:
    return OrderResult(
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        client_order_id="scale-out",
        exchange_order_id="scale-out-1",
        status=status,
        executed_quantity=Decimal(quantity),
        average_price=Decimal(price) if price is not None else None,
        raw_status=status.value,
    )


def coordinator(
    settings: object,
    repository: SqliteStateRepository,
    execution: ManagementExecution,
    instrument: Instrument,
) -> PositionManagementCoordinator:
    return PositionManagementCoordinator(
        execution,  # type: ignore[arg-type]
        ManagementMarketData(instrument, Decimal("103")),
        repository,
        PositionManager(settings),  # type: ignore[arg-type]
        settings,  # type: ignore[arg-type]
    )


async def test_confirmed_partial_advances_actual_quantity_and_removes_fixed_target(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    instrument = make_instrument(venue=Venue.BINANCE_USDM)
    execution = ManagementExecution(reduction_result(OrderStatus.FILLED, "5", "103"))
    manager = coordinator(settings, repository, execution, instrument)
    opened = trade(instrument)

    outcomes = await manager.manage((opened,), {str(instrument.id): instrument})

    assert len(outcomes) == 1
    state = outcomes[0].state
    assert state.stage is ManagementStage.PARTIAL_TAKEN
    assert state.remaining_quantity == Decimal("5")
    assert state.realized_partial_pnl == Decimal("15")
    assert execution.replacements[-1][0:2] == (Decimal("5"), False)
    persisted = await repository.get_position_management(str(opened.signal_id))
    assert persisted is not None and persisted.remaining_quantity == Decimal("5")


async def test_rejected_partial_restores_full_bracket_without_advancing_state(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    instrument = make_instrument(venue=Venue.BINANCE_USDM)
    execution = ManagementExecution(VenueError("order rejected"))
    manager = coordinator(settings, repository, execution, instrument)
    opened = trade(instrument)

    outcomes = await manager.manage((opened,), {str(instrument.id): instrument})

    state = outcomes[0].state
    assert state.stage is ManagementStage.INITIAL
    assert state.remaining_quantity == Decimal("10")
    assert execution.replacements[-1][0:2] == (Decimal("10"), True)


async def test_unknown_partial_status_arms_kill_switch(settings: object) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    instrument = make_instrument(venue=Venue.BINANCE_USDM)
    execution = ManagementExecution(UnknownExecutionStatus("timeout"), recovered=None)
    manager = coordinator(settings, repository, execution, instrument)

    with pytest.raises(SafetyInterlockError, match="status is unknown"):
        await manager.manage((trade(instrument),), {str(instrument.id): instrument})

    assert await repository.is_kill_switch_active()


async def test_restart_recovers_recorded_partial_fill_without_resubmitting(
    settings: object,
) -> None:
    repository = SqliteStateRepository(settings.state_db_path)  # type: ignore[attr-defined]
    await repository.initialize()
    instrument = make_instrument(venue=Venue.BINANCE_USDM)
    opened = trade(instrument)
    client_order_id = f"atl-{opened.signal_id.hex[:20]}-tp1"
    await repository.record_order_intent(
        OrderIntent(
            instrument=instrument,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("5"),
            client_order_id=client_order_id,
            direction=Direction.LONG,
            reduce_only=True,
        )
    )
    await repository.record_order_result(
        OrderResult(
            venue=Venue.BINANCE_USDM,
            symbol=instrument.id.symbol,
            client_order_id=client_order_id,
            exchange_order_id="already-filled",
            status=OrderStatus.FILLED,
            executed_quantity=Decimal("5"),
            average_price=Decimal("103"),
            raw_status="FILLED",
        )
    )
    execution = ManagementExecution(VenueError("must not resubmit"))
    manager = coordinator(settings, repository, execution, instrument)

    outcomes = await manager.manage((opened,), {str(instrument.id): instrument})

    assert execution.reduce_calls == 0
    assert outcomes[0].state.remaining_quantity == Decimal("5")
    assert execution.replacements[-1][0:2] == (Decimal("5"), False)


def test_reconciliation_uses_confirmed_runner_quantity() -> None:
    from algotrader.application.service import TradingSystem
    from algotrader.domain.models import PositionManagementState

    instrument = make_instrument(venue=Venue.BINANCE_USDM)
    opened = trade(instrument)
    state = PositionManagementState(
        signal_id=opened.signal_id,
        instrument_id=opened.instrument_id,
        direction=opened.direction,
        stage=ManagementStage.PARTIAL_TAKEN,
        entry_price=opened.entry_price,
        initial_stop_price=opened.stop_price,
        initial_quantity=opened.quantity,
        remaining_quantity=Decimal("5"),
        current_stop_price=Decimal("100.10"),
        target_price=opened.target_price,
        best_price=Decimal("103"),
    )
    position = Position(
        instrument_id=opened.instrument_id,
        direction=opened.direction,
        quantity=Decimal("5"),
        entry_price=opened.entry_price,
        mark_price=Decimal("103"),
        unrealized_pnl=Decimal("15"),
    )

    assert TradingSystem._position_matches_trade(position, opened, (state,))
