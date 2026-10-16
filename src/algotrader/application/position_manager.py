"""Exit management: scale out at +1R, move to breakeven, then trail behind structure.

A static bracket has exactly two outcomes: it loses 1R, or it wins the fixed target. It
cannot participate in the move that pays for the losers, and between entry and target it
protects nothing that has already been earned -- an open trade at +40R and an open trade at
+0.1R carry an identical stop.

This module closes that gap with a three-stage ratchet:

===============  ==================================  ====================================
Stage            Trigger                             Effect
===============  ==================================  ====================================
INITIAL          entry                               original stop, full size
PARTIAL_TAKEN    ``+partial_take_profit_r``          sell a fraction, stop -> breakeven
TRAILING         ``+trail_activation_r``             stop trails the last confirmed swing
===============  ==================================  ====================================

Two invariants hold at every stage, and both are enforced here rather than in the adapters:

1. **The stop only ever moves toward price.** Every candidate is filtered through
   ``PositionManagementState.tightens`` before it is emitted.
2. **The stop never crosses price.** A stop submitted on the wrong side of the market is an
   immediate market exit, so candidates that are not protective are discarded.

Decisions are pure: this returns actions, it does not place orders. That keeps the exit
policy testable against a table of prices with no venue in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from algotrader.config import Settings
from algotrader.domain.models import (
    Direction,
    ManagementStage,
    PositionManagementState,
)
from algotrader.domain.precision import ceil_to_increment, floor_to_increment
from algotrader.domain.structure import MarketStructure


@dataclass(frozen=True, slots=True)
class TakePartialProfit:
    """Close ``quantity`` at market, leaving the remainder to run."""

    quantity: Decimal
    r_multiple: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class MoveStop:
    """Cancel/replace the protective stop at a strictly tighter price."""

    stop_price: Decimal
    previous_stop_price: Decimal
    reason: str


PositionAction = TakePartialProfit | MoveStop


@dataclass(frozen=True, slots=True)
class ManagementPlan:
    state: PositionManagementState
    actions: tuple[PositionAction, ...]

    @property
    def changed(self) -> bool:
        return bool(self.actions)


class PositionManager:
    """Derives exit actions for one open position from price and confirmed structure."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def plan(
        self,
        state: PositionManagementState,
        *,
        mark_price: Decimal,
        atr: Decimal,
        quantity_step: Decimal,
        tick_size: Decimal,
        structure: MarketStructure | None = None,
    ) -> ManagementPlan:
        if mark_price <= 0 or state.remaining_quantity <= 0:
            return ManagementPlan(state, ())
        if state.initial_risk_per_unit <= 0 or quantity_step <= 0 or tick_size <= 0:
            return ManagementPlan(state, ())

        state = self._record_excursion(state, mark_price)
        actions: list[PositionAction] = []

        state, scaled = self._scale_out(state, mark_price, atr, quantity_step, tick_size)
        actions.extend(scaled)

        state, trailed = self._trail(state, mark_price, atr, tick_size, structure)
        actions.extend(trailed)

        return ManagementPlan(state, tuple(actions))

    def _record_excursion(
        self, state: PositionManagementState, mark_price: Decimal
    ) -> PositionManagementState:
        best = (
            max(state.best_price, mark_price)
            if state.direction is Direction.LONG
            else min(state.best_price, mark_price)
        )
        if best == state.best_price:
            return state
        return replace(state, best_price=best)

    def _scale_out(
        self,
        state: PositionManagementState,
        mark_price: Decimal,
        atr: Decimal,
        quantity_step: Decimal,
        tick_size: Decimal,
    ) -> tuple[PositionManagementState, tuple[PositionAction, ...]]:
        if state.stage is not ManagementStage.INITIAL:
            return state, ()
        r_multiple = state.r_multiple(mark_price)
        if r_multiple < self._settings.partial_take_profit_r:
            return state, ()

        actions: list[PositionAction] = []
        quantity = self._partial_quantity(state, quantity_step)
        remaining = state.remaining_quantity
        if quantity > 0:
            remaining = state.remaining_quantity - quantity
            actions.append(
                TakePartialProfit(
                    quantity=quantity,
                    r_multiple=r_multiple,
                    reason=(
                        f"scaled out {self._settings.partial_take_profit_fraction} "
                        f"of initial size at +{r_multiple:.2f}R"
                    ),
                )
            )

        stop = state.current_stop_price
        breakeven = quantize_stop(
            direction=state.direction,
            stop_price=self._breakeven_price(state, atr),
            tick_size=tick_size,
        )
        if state.tightens(breakeven) and state.is_protective(breakeven, mark_price):
            actions.append(
                MoveStop(
                    stop_price=breakeven,
                    previous_stop_price=state.current_stop_price,
                    reason="stop moved to cost-adjusted breakeven after partial take-profit",
                )
            )
            stop = breakeven

        return (
            replace(
                state,
                stage=ManagementStage.PARTIAL_TAKEN,
                remaining_quantity=remaining,
                current_stop_price=stop,
            ),
            tuple(actions),
        )

    def _trail(
        self,
        state: PositionManagementState,
        mark_price: Decimal,
        atr: Decimal,
        tick_size: Decimal,
        structure: MarketStructure | None,
    ) -> tuple[PositionManagementState, tuple[PositionAction, ...]]:
        if state.stage is ManagementStage.INITIAL or structure is None:
            return state, ()
        if state.r_multiple(mark_price) < self._settings.trail_activation_r:
            return state, ()

        reference = structure.trailing_reference(state.direction, mark_price)
        if reference is None:
            # No confirmed swing behind price yet. Hold the current stop rather than
            # inventing a level; the ratchet never loosens, so holding is always safe.
            return state, ()

        buffer = atr * self._settings.trail_buffer_atr
        raw = (
            reference.price - buffer
            if state.direction is Direction.LONG
            else reference.price + buffer
        )
        candidate = quantize_stop(direction=state.direction, stop_price=raw, tick_size=tick_size)
        if not state.tightens(candidate) or not state.is_protective(candidate, mark_price):
            return state, ()

        action = MoveStop(
            stop_price=candidate,
            previous_stop_price=state.current_stop_price,
            reason=(
                f"stop trailed behind the confirmed swing at {reference.price} "
                f"less {self._settings.trail_buffer_atr} ATR"
            ),
        )
        return (
            replace(state, stage=ManagementStage.TRAILING, current_stop_price=candidate),
            (action,),
        )

    def _breakeven_price(self, state: PositionManagementState, atr: Decimal) -> Decimal:
        """Breakeven that actually breaks even, after both legs of taker fees.

        Entry price alone is not breakeven: closing there still pays the round trip.
        """
        fee_rate = self._settings.taker_fee_bps / Decimal("10000")
        cost = state.entry_price * fee_rate * Decimal("2")
        buffer = max(atr * self._settings.breakeven_buffer_atr, cost)
        if state.direction is Direction.LONG:
            return state.entry_price + buffer
        return state.entry_price - buffer

    def _partial_quantity(self, state: PositionManagementState, quantity_step: Decimal) -> Decimal:
        raw = state.initial_quantity * self._settings.partial_take_profit_fraction
        quantity = floor_to_increment(min(raw, state.remaining_quantity), quantity_step)
        if quantity >= state.remaining_quantity:
            # Never let the "partial" close the runner; breakeven protection still applies.
            return Decimal("0")
        return quantity


def quantize_stop(*, direction: Direction, stop_price: Decimal, tick_size: Decimal) -> Decimal:
    """Snap a stop away from price so tick rounding never tightens it past intent."""
    if direction is Direction.LONG:
        return floor_to_increment(stop_price, tick_size)
    return ceil_to_increment(stop_price, tick_size)
