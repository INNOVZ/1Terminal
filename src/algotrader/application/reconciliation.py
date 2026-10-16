"""Authoritative local-ledger versus venue-position reconciliation.

This service owns position truth and close booking. Keeping it separate from screening and cycle
orchestration makes safety recovery independently testable and prevents incident tooling from
expanding the main trading coordinator into another venue adapter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal

from algotrader.domain.errors import SafetyInterlockError, VenueError
from algotrader.domain.models import (
    AccountSnapshot,
    Direction,
    Position,
    PositionManagementState,
    TradeRecord,
    Venue,
)
from algotrader.domain.ports import ExecutionPort, StateRepository


class PositionReconciliationService:
    def __init__(
        self,
        repository: StateRepository,
        executions: Mapping[Venue, ExecutionPort],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._executions = dict(executions)
        self._clock = clock

    async def reconcile_trade_close(
        self,
        signal_id: str,
        *,
        exit_price: Decimal,
        fees: Decimal,
        exit_reason: str,
    ) -> tuple[TradeRecord, Decimal]:
        trade = next(
            (
                item
                for item in await self._repository.list_open_trades()
                if str(item.signal_id) == signal_id
            ),
            None,
        )
        if trade is None:
            raise SafetyInterlockError(f"open trade {signal_id!r} was not found")
        execution = self._execution_for(trade.instrument_id.venue)
        account = await execution.account_snapshot()
        still_open = any(
            position.instrument_id == trade.instrument_id
            and position.direction is trade.direction
            and position.quantity > 0
            for position in account.positions
        )
        if still_open:
            raise SafetyInterlockError(
                "exchange position is still open; close reconciliation was refused"
            )
        multiplier = Decimal("1") if trade.direction is Direction.LONG else Decimal("-1")
        management = await self._repository.get_position_management(signal_id)
        remaining_quantity = (
            management.remaining_quantity if management is not None else trade.quantity
        )
        partial_pnl = management.realized_partial_pnl if management is not None else Decimal("0")
        gross_realized_pnl = (
            partial_pnl + (exit_price - trade.entry_price) * remaining_quantity * multiplier
        )
        await self._repository.close_trade(
            signal_id,
            exit_price=exit_price,
            closed_at=self._clock(),
            gross_realized_pnl=gross_realized_pnl,
            fees=fees,
            exit_reason=exit_reason,
        )
        await self._repository.delete_position_management(signal_id)
        await self._repository.append_event(
            "trade_close_reconciled",
            {
                "signal_id": signal_id,
                "instrument": str(trade.instrument_id),
                "exit_price": str(exit_price),
                "gross_realized_pnl": str(gross_realized_pnl),
                "fees": str(fees),
                "exit_reason": exit_reason,
            },
        )
        return trade, gross_realized_pnl

    async def positions_are_consistent(self) -> bool:
        open_trades = tuple(await self._repository.list_open_trades())
        management = tuple(await self._repository.list_position_management())
        accounts, venue_errors = await self.readable_accounts()
        if venue_errors:
            await self._repository.append_event(
                "account_state_unreadable", {"venues": venue_errors}
            )
            return False
        missing = self.missing_tracked_trades(open_trades, accounts, management)
        untracked = self.untracked_exchange_positions(open_trades, accounts, management)
        if not missing and not untracked:
            return True
        reason = "local and exchange positions disagree; authoritative reconciliation is required"
        await self._repository.append_event(
            "position_reconciliation_failed",
            {
                "missing_exchange_positions": [str(trade.instrument_id) for trade in missing],
                "untracked_exchange_positions": [
                    str(position.instrument_id) for position in untracked
                ],
                "count": len(missing) + len(untracked),
            },
        )
        await self._repository.set_kill_switch(True, reason)
        return False

    async def mismatch_detail(self) -> str:
        try:
            open_trades = tuple(await self._repository.list_open_trades())
            management = tuple(await self._repository.list_position_management())
            accounts = tuple(
                await asyncio.gather(
                    *(execution.account_snapshot() for execution in self._executions.values())
                )
            )
        except VenueError as exc:
            return f"account state could not be read: {exc}"
        missing = self.missing_tracked_trades(open_trades, accounts, management)
        untracked = self.untracked_exchange_positions(open_trades, accounts, management)
        parts = []
        if missing:
            parts.append(
                "tracked but absent at venue: "
                + ", ".join(str(trade.instrument_id) for trade in missing[:3])
            )
        if untracked:
            parts.append(
                "at venue but untracked: "
                + ", ".join(str(position.instrument_id) for position in untracked[:3])
            )
        return "; ".join(parts) or "local and exchange positions disagree"

    async def readable_accounts(
        self,
    ) -> tuple[tuple[AccountSnapshot, ...], list[dict[str, str]]]:
        accounts: list[AccountSnapshot] = []
        errors: list[dict[str, str]] = []
        for venue, execution in self._executions.items():
            try:
                accounts.append(await execution.account_snapshot())
            except (VenueError, SafetyInterlockError) as exc:
                errors.append(
                    {
                        "venue": venue.value,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:400],
                    }
                )
        return tuple(accounts), errors

    @staticmethod
    def missing_tracked_trades(
        trades: tuple[TradeRecord, ...],
        accounts: tuple[AccountSnapshot, ...],
        management: tuple[PositionManagementState, ...] = (),
    ) -> tuple[TradeRecord, ...]:
        return tuple(
            trade
            for trade in trades
            if not any(
                PositionReconciliationService.position_matches_trade(position, trade, management)
                for account in accounts
                for position in account.positions
            )
        )

    @staticmethod
    def untracked_exchange_positions(
        open_trades: tuple[TradeRecord, ...],
        accounts: tuple[AccountSnapshot, ...],
        management: tuple[PositionManagementState, ...] = (),
    ) -> tuple[Position, ...]:
        return tuple(
            position
            for account in accounts
            for position in account.positions
            if position.quantity > 0
            and not any(
                PositionReconciliationService.position_matches_trade(position, trade, management)
                for trade in open_trades
            )
        )

    @staticmethod
    def position_matches_trade(
        position: Position,
        trade: TradeRecord,
        management: tuple[PositionManagementState, ...] = (),
    ) -> bool:
        expected_quantity = PositionReconciliationService.expected_quantity(trade, management)
        return (
            position.instrument_id == trade.instrument_id
            and position.direction is trade.direction
            and position.quantity == expected_quantity
        )

    @staticmethod
    def expected_quantity(
        trade: TradeRecord, management: tuple[PositionManagementState, ...]
    ) -> Decimal:
        state = next((item for item in management if item.signal_id == trade.signal_id), None)
        return state.remaining_quantity if state is not None else trade.quantity

    def _execution_for(self, venue: Venue) -> ExecutionPort:
        execution = self._executions.get(venue)
        if execution is None:
            raise RuntimeError(f"no execution adapter registered for {venue.value}")
        return execution
