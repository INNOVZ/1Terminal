from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from algotrader.application.position_manager import (
    ManagementPlan,
    MoveStop,
    PositionManager,
    TakePartialProfit,
)
from algotrader.domain.models import (
    Direction,
    InstrumentId,
    ManagementStage,
    PositionManagementState,
    Venue,
)
from algotrader.domain.structure import classify
from tests.test_structure import UPTREND_PATH, candles

TICK = Decimal("0.01")
STEP = Decimal("0.001")
ATR = Decimal("4")


def state(
    *,
    direction: Direction = Direction.LONG,
    entry: str = "100",
    stop: str = "97",
    quantity: str = "10",
) -> PositionManagementState:
    return PositionManagementState(
        signal_id=uuid4(),
        instrument_id=InstrumentId(Venue.BINANCE_USDM, "TESTUSDT"),
        direction=direction,
        stage=ManagementStage.INITIAL,
        entry_price=Decimal(entry),
        initial_stop_price=Decimal(stop),
        initial_quantity=Decimal(quantity),
        remaining_quantity=Decimal(quantity),
        current_stop_price=Decimal(stop),
        target_price=Decimal("106"),
        best_price=Decimal(entry),
    )


def plan_at(
    manager: PositionManager,
    current: PositionManagementState,
    price: str,
    *,
    structure: object = None,
) -> ManagementPlan:
    return manager.plan(
        current,
        mark_price=Decimal(price),
        atr=ATR,
        quantity_step=STEP,
        tick_size=TICK,
        structure=structure,  # type: ignore[arg-type]
    )


def stops(plan: ManagementPlan) -> list[MoveStop]:
    return [action for action in plan.actions if isinstance(action, MoveStop)]


def partials(plan: ManagementPlan) -> list[TakePartialProfit]:
    return [action for action in plan.actions if isinstance(action, TakePartialProfit)]


def test_no_action_before_the_partial_take_profit_level(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    plan = plan_at(manager, state(), "102.9")  # +0.97R
    assert not plan.changed
    assert plan.state.stage is ManagementStage.INITIAL


def test_scales_out_half_and_moves_to_breakeven_at_one_r(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    plan = plan_at(manager, state(), "103")  # exactly +1R

    taken = partials(plan)
    assert len(taken) == 1
    assert taken[0].quantity == Decimal("5")

    moved = stops(plan)
    assert len(moved) == 1
    assert moved[0].stop_price > Decimal("100"), "breakeven must clear round-trip fees"
    assert plan.state.stage is ManagementStage.PARTIAL_TAKEN
    assert plan.state.remaining_quantity == Decimal("5")


def test_breakeven_stop_covers_round_trip_fees(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    plan = plan_at(manager, state(), "103")
    fee = settings.taker_fee_bps / Decimal("10000")  # type: ignore[attr-defined]
    round_trip = Decimal("100") * fee * Decimal("2")
    assert plan.state.current_stop_price >= Decimal("100") + round_trip


def test_trade_cannot_return_to_a_loss_after_scaling_out(settings: object) -> None:
    """The core guarantee: past +1R the position is structurally unable to lose money."""
    manager = PositionManager(settings)  # type: ignore[arg-type]
    after = plan_at(manager, state(), "103").state
    assert after.current_stop_price > after.entry_price


def test_partial_is_taken_only_once(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    first = plan_at(manager, state(), "103").state
    second = plan_at(manager, first, "104")
    assert not partials(second)
    assert second.state.remaining_quantity == Decimal("5")


def test_stop_never_moves_backwards(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    structure = classify(candles(UPTREND_PATH))
    advanced = plan_at(manager, state(), "300", structure=structure).state
    high_water = advanced.current_stop_price

    # Price retraces hard; the ratchet must hold.
    retraced = plan_at(manager, advanced, "200", structure=structure)
    assert not stops(retraced)
    assert retraced.state.current_stop_price == high_water


def test_stop_is_never_placed_on_the_wrong_side_of_price(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    structure = classify(candles(UPTREND_PATH))
    current = state()
    for price in ("103", "150", "200", "300"):
        current = plan_at(manager, current, price, structure=structure).state
        assert current.current_stop_price < Decimal(price)


def test_trailing_follows_the_last_confirmed_higher_low(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    structure = classify(candles(UPTREND_PATH))
    result = plan_at(manager, state(), "300", structure=structure)

    reference = structure.trailing_reference(Direction.LONG, Decimal("300"))
    assert reference is not None
    expected = reference.price - ATR * settings.trail_buffer_atr  # type: ignore[attr-defined]
    assert abs(result.state.current_stop_price - expected) <= TICK
    assert result.state.stage is ManagementStage.TRAILING


def test_flash_crash_after_a_3x_keeps_most_of_the_gain(settings: object) -> None:
    """Regression for the live incident: a 3x that gave back nearly all of its profit.

    With a static bracket the stop sat at 97 for the whole move, so a crash back through
    entry returned the position to roughly flat. Walking the same path through the manager,
    the stop ratchets behind each confirmed higher low and the crash exits far above entry.
    """
    manager = PositionManager(settings)  # type: ignore[arg-type]
    series = candles(UPTREND_PATH)
    current = state()
    banked = Decimal("0")

    for index, bar in enumerate(series):
        structure = classify(series[: index + 1])
        plan = manager.plan(
            current,
            mark_price=bar.close,
            atr=ATR,
            quantity_step=STEP,
            tick_size=TICK,
            structure=structure,
        )
        for action in plan.actions:
            if isinstance(action, TakePartialProfit):
                banked += (bar.close - current.entry_price) * action.quantity
        current = plan.state

    # A flash crash now fills at the trailed stop rather than back at entry.
    exit_price = current.current_stop_price
    runner_pnl = (exit_price - current.entry_price) * current.remaining_quantity
    total = banked + runner_pnl

    assert current.stage is ManagementStage.TRAILING
    assert banked > 0, "half the position should already be realized at +1R"

    # The incident itself: price returned to roughly entry. Under the old static bracket the
    # stop was still sitting at 97, so the round trip ended flat. It now exits far above.
    assert exit_price > Decimal("150")
    crash_outcome_before = (Decimal("100") - Decimal("100")) * Decimal("10")
    assert total > crash_outcome_before + Decimal("300")

    # And against the best case the old bracket could ever produce -- a clean fill at the
    # fixed 2R target -- managing the exit is several times better on the same price path.
    static_target_outcome = (Decimal("106") - Decimal("100")) * Decimal("10")
    assert total > static_target_outcome * 4


def test_short_position_ratchets_downward(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    short = state(direction=Direction.SHORT, entry="100", stop="103")
    plan = plan_at(manager, short, "97")  # +1R for a short

    assert partials(plan)
    assert plan.state.current_stop_price < Decimal("100")
    assert plan.state.stage is ManagementStage.PARTIAL_TAKEN


def test_partial_never_closes_the_entire_runner(settings: object) -> None:
    """A position too small to split must keep its size and still gain breakeven cover."""
    manager = PositionManager(settings)  # type: ignore[arg-type]
    tiny = state(quantity="0.001")
    plan = plan_at(manager, tiny, "103")
    assert not partials(plan)
    assert plan.state.remaining_quantity == Decimal("0.001")
    assert plan.state.current_stop_price > plan.state.entry_price


def test_zero_risk_state_is_inert(settings: object) -> None:
    manager = PositionManager(settings)  # type: ignore[arg-type]
    broken = replace(state(), initial_stop_price=Decimal("100"))
    assert not plan_at(manager, broken, "150").changed
