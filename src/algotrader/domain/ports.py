from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    ExecutionLifecycle,
    Instrument,
    InstrumentId,
    OpportunityObservation,
    OrderIntent,
    OrderResult,
    PositionManagementState,
    Quote,
    TrackedSignal,
    TradeRecord,
    UnresolvedOrderIntent,
    Venue,
)


class MarketDataPort(Protocol):
    venue: Venue

    async def instruments(self) -> Sequence[Instrument]: ...

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]: ...

    async def candles(
        self, instrument: Instrument, interval: str, limit: int
    ) -> Sequence[Candle]: ...


class DerivativesDataPort(Protocol):
    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot: ...


class SafetyStateStore(Protocol):
    async def initialize(self) -> None: ...

    async def is_kill_switch_active(self) -> bool: ...

    async def set_kill_switch(self, active: bool, reason: str) -> None: ...

    async def get_state(self, key: str) -> dict[str, object] | None: ...

    async def set_state(self, key: str, value: dict[str, object]) -> None: ...

    async def delete_state(self, key: str) -> None: ...


class LeaseStore(Protocol):
    async def try_acquire_lease(
        self,
        name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool: ...

    async def release_lease(self, name: str, owner_id: str) -> None: ...


class ExecutionJournal(Protocol):
    async def has_order_intent(self, client_order_id: str) -> bool: ...

    async def record_order_intent(self, intent: OrderIntent) -> None: ...

    async def get_order_intent_payload(self, client_order_id: str) -> dict[str, object] | None:
        """The persisted intent as stored.

        Recovery needs the protective prices that were decided *before* the order was
        sent. They are already durable in the intent metadata -- invariant 1 exists so
        that a fill can always be traced back to its intended bracket.
        """
        ...

    async def record_order_result(self, result: OrderResult) -> None: ...

    async def get_latest_order_result(self, client_order_id: str) -> OrderResult | None: ...

    async def list_unresolved_order_intents(
        self,
    ) -> Sequence[UnresolvedOrderIntent]: ...

    async def record_execution_lifecycle(self, lifecycle: ExecutionLifecycle) -> None: ...

    async def list_incomplete_execution_lifecycles(
        self,
    ) -> Sequence[ExecutionLifecycle]: ...


class TradeLedger(Protocol):
    async def record_trade_open(self, trade: TradeRecord) -> None: ...

    async def close_trade(
        self,
        signal_id: str,
        *,
        exit_price: Decimal,
        closed_at: datetime,
        gross_realized_pnl: Decimal,
        fees: Decimal,
        exit_reason: str,
    ) -> None: ...

    async def list_open_trades(self) -> Sequence[TradeRecord]: ...

    async def realized_pnl_since(self, since: datetime) -> Decimal: ...

    async def latest_trade_opened_at(self) -> datetime | None: ...


class SignalStore(Protocol):
    async def record_signal_observation(self, signal: TrackedSignal) -> TrackedSignal: ...

    async def expire_signals(self, now: datetime) -> int: ...

    async def list_active_signals(self, now: datetime) -> Sequence[TrackedSignal]: ...

    async def mark_signal_executed(
        self, instrument_id: InstrumentId, direction: Direction, executed_at: datetime
    ) -> None: ...


class PositionManagementStore(Protocol):
    """Durable exit-management state.

    This must survive restarts: the original stop and size define every R multiple, and once
    the stop has ratcheted they cannot be recovered from the live bracket.
    """

    async def upsert_position_management(self, state: PositionManagementState) -> None: ...

    async def get_position_management(self, signal_id: str) -> PositionManagementState | None: ...

    async def list_position_management(self) -> Sequence[PositionManagementState]: ...

    async def delete_position_management(self, signal_id: str) -> None: ...


class EventStore(Protocol):
    async def append_event(self, event_type: str, payload: dict[str, object]) -> None: ...


class OpportunityStore(Protocol):
    async def record_opportunities(self, observations: Sequence[OpportunityObservation]) -> int: ...

    async def list_opportunities(
        self, *, since: datetime | None = None
    ) -> Sequence[OpportunityObservation]: ...


class StateRepository(
    SafetyStateStore,
    LeaseStore,
    ExecutionJournal,
    TradeLedger,
    SignalStore,
    PositionManagementStore,
    OpportunityStore,
    EventStore,
    Protocol,
):
    """Compatibility composition for services that orchestrate every state boundary."""
