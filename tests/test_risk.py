from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from algotrader.application.risk import PortfolioRiskManager, floor_to_step
from algotrader.config import StrategyProfile
from algotrader.domain.models import (
    AccountSnapshot,
    Direction,
    InstrumentId,
    MarketKind,
    MomentumState,
    Position,
    TradeSignal,
    Venue,
)
from tests.conftest import make_instrument


def signal() -> TradeSignal:
    return TradeSignal(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("85"),
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


def account() -> AccountSnapshot:
    return AccountSnapshot(
        venue=Venue.BINANCE_SPOT,
        equity=Decimal("100000"),
        available_balance=Decimal("100000"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100000"),
    )


def futures_signal(*, entry: str = "10", stop: str = "9.999") -> TradeSignal:
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol="NEWUSDT",
    )
    return replace(
        signal(),
        instrument=instrument,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=Decimal(entry) + (Decimal(entry) - Decimal(stop)) * Decimal("2"),
    )


def futures_account(*, positions: tuple[Position, ...] = ()) -> AccountSnapshot:
    return AccountSnapshot(
        venue=Venue.BINANCE_USDM,
        equity=Decimal("100"),
        available_balance=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        peak_equity=Decimal("100"),
        positions=positions,
    )


def test_floor_to_venue_step_never_rounds_up() -> None:
    assert floor_to_step(Decimal("1.239"), Decimal("0.01")) == Decimal("1.23")


def test_fixed_fractional_risk_sizes_from_cost_adjusted_stop_distance(settings: object) -> None:
    """Sizing charges round-trip fees and stop slippage against the risk budget.

    Sizing on the bare 5.00 stop distance yields 50 units, but the stop is a trigger: the
    exit prints past it and both legs pay taker fees. Realized loss would then exceed the
    0.25% budget. The cost-adjusted denominator keeps realized risk inside the budget.
    """
    manager = PortfolioRiskManager(settings)  # type: ignore[arg-type]
    decision = manager.evaluate(signal(), account())
    assert decision.approved

    budget = Decimal("100000") * settings.account_risk_per_trade  # type: ignore[attr-defined]
    fee = settings.taker_fee_bps / Decimal("10000")  # type: ignore[attr-defined]
    slip = settings.stop_slippage_bps / Decimal("10000")  # type: ignore[attr-defined]
    loss_per_unit = Decimal("5") + Decimal("100") * fee + Decimal("95") * (fee + slip)

    assert decision.quantity * loss_per_unit <= budget
    # Naive sizing would have overshot the budget by the round-trip cost.
    assert Decimal("50") * loss_per_unit > budget


def test_daily_loss_limit_blocks_new_risk(settings: object) -> None:
    losing = replace(account(), realized_pnl_today=Decimal("-2500"))
    decision = PortfolioRiskManager(settings).evaluate(signal(), losing)  # type: ignore[arg-type]
    assert not decision.approved
    assert "daily loss" in decision.reason


def test_mark_to_market_loss_has_separate_circuit_breaker(settings: object) -> None:
    losing = replace(account(), mark_to_market_pnl_today=Decimal("-3500"))
    decision = PortfolioRiskManager(settings).evaluate(signal(), losing)  # type: ignore[arg-type]
    assert not decision.approved
    assert "mark-to-market" in decision.reason


def test_risk_manager_rejects_signal_without_gaining_momentum(settings: object) -> None:
    stale_signal = replace(signal(), momentum_state=MomentumState.LOSING)
    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        stale_signal, account()
    )
    assert not decision.approved
    assert "momentum" in decision.reason


def test_risk_manager_defends_optional_pullback_gate(settings: object) -> None:
    strict = settings.model_copy(  # type: ignore[attr-defined]
        update={"require_pullback_confirmation": True}
    )
    unconfirmed = replace(signal(), continuation_trigger_passed=False)

    decision = PortfolioRiskManager(strict).evaluate(unconfirmed, account())

    assert not decision.approved
    assert "pullback" in decision.reason


def test_futures_position_keeps_stop_risk_sizing_below_margin_cap(settings: object) -> None:
    # A 5% stop on a 100-unit account sizes below the 5 USDT minimum notional once costs
    # are charged, so this exercises the margin cap on an account that can actually trade.
    venue = replace(futures_account(), equity=Decimal("1000"), available_balance=Decimal("1000"))
    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        futures_signal(entry="100", stop="95"), venue, venue
    )

    assert decision.approved
    # Stop-risk sizing binds well before the one-third-of-equity margin cap.
    assert decision.notional / Decimal("3") < Decimal("1000") * Decimal("0.33333333")


def test_small_account_is_rejected_when_costs_push_below_min_notional(
    settings: object,
) -> None:
    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        futures_signal(entry="100", stop="95"), futures_account(), futures_account()
    )
    assert not decision.approved
    assert "below venue minimum" in decision.reason


def test_futures_position_margin_is_capped_at_one_third_of_equity(settings: object) -> None:
    venue = futures_account()
    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        futures_signal(), venue, venue
    )

    assert decision.approved
    assert decision.notional == Decimal("99.990")
    assert decision.notional / Decimal("3") == Decimal("33.330")


def test_futures_aggregate_notional_never_exceeds_three_times_equity(
    settings: object,
) -> None:
    positions = tuple(
        Position(
            instrument_id=InstrumentId(Venue.BINANCE_USDM, symbol),
            direction=direction,
            quantity=Decimal("1.25"),
            entry_price=Decimal("100"),
            mark_price=Decimal("100"),
            unrealized_pnl=Decimal("0"),
        )
        for symbol, direction in (
            ("ONEUSDT", Direction.LONG),
            ("TWOUSDT", Direction.SHORT),
        )
    )
    venue = futures_account(positions=positions)
    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        futures_signal(), venue, venue
    )

    current_notional = sum(
        (position.quantity * position.mark_price for position in positions), Decimal("0")
    )
    assert decision.approved
    assert decision.notional == Decimal("50.000")
    assert current_notional + decision.notional == Decimal("300.000")


def test_third_same_direction_crypto_position_is_rejected(settings: object) -> None:
    positions = tuple(
        Position(
            instrument_id=InstrumentId(Venue.BINANCE_USDM, symbol),
            direction=Direction.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("10"),
            mark_price=Decimal("10"),
            unrealized_pnl=Decimal("0"),
        )
        for symbol in ("ONEUSDT", "TWOUSDT")
    )
    venue = futures_account(positions=positions)

    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        futures_signal(), venue, venue
    )

    assert not decision.approved
    assert "correlated" in decision.reason


def test_excessive_btc_beta_is_rejected_for_research_profiles(settings: object) -> None:
    high_beta = replace(
        signal(),
        strategy=StrategyProfile.SLOW_TREND.value,
        btc_beta=Decimal("2.01"),
        btc_correlation=Decimal("0.90"),
    )

    decision = PortfolioRiskManager(settings).evaluate(high_beta, account())  # type: ignore[arg-type]

    assert not decision.approved
    assert "BTC beta" in decision.reason


def test_high_btc_correlation_caps_same_direction_basket(settings: object) -> None:
    positions = tuple(
        Position(
            instrument_id=InstrumentId(Venue.BINANCE_USDM, symbol),
            direction=Direction.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("10"),
            mark_price=Decimal("10"),
            unrealized_pnl=Decimal("0"),
        )
        for symbol in ("ONEUSDT", "TWOUSDT")
    )
    relaxed_direction_cap = settings.model_copy(  # type: ignore[attr-defined]
        update={"max_same_direction_crypto_positions": 3}
    )
    correlated = replace(
        futures_signal(entry="100", stop="95"),
        strategy=StrategyProfile.SLOW_TREND.value,
        btc_beta=Decimal("1.2"),
        btc_correlation=Decimal("0.90"),
    )
    venue = replace(
        futures_account(positions=positions),
        equity=Decimal("1000"),
        available_balance=Decimal("1000"),
    )

    decision = PortfolioRiskManager(relaxed_direction_cap).evaluate(correlated, venue, venue)

    assert not decision.approved
    assert "BTC-correlated" in decision.reason


@pytest.mark.parametrize(
    "profile",
    [
        StrategyProfile.EMPIRICAL_TREND,
        StrategyProfile.LAYERED_MOMENTUM,
        StrategyProfile.EARLY_MOMENTUM,
    ],
)
def test_research_profile_fails_closed_without_btc_exposure_data(
    settings: object, profile: StrategyProfile
) -> None:
    missing = replace(signal(), strategy=profile.value)

    decision = PortfolioRiskManager(settings).evaluate(missing, account())  # type: ignore[arg-type]

    assert not decision.approved
    assert "unavailable" in decision.reason


def test_entry_beyond_atr_chase_limit_is_rejected(settings: object) -> None:
    chased = replace(
        futures_signal(entry="11", stop="10.999"),
        signal_bar_close=Decimal("10"),
        atr=Decimal("1"),
    )
    venue = futures_account()

    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        chased, venue, venue
    )

    assert not decision.approved
    assert "chase" in decision.reason


def test_risk_manager_rejects_directionally_invalid_bracket(settings: object) -> None:
    malformed = replace(
        signal(),
        direction=Direction.SHORT,
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
    )

    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        malformed, account()
    )

    assert not decision.approved
    assert "short bracket geometry" in decision.reason


def test_risk_manager_rejects_subminimum_reward_risk(settings: object) -> None:
    poor_payoff = replace(signal(), target_price=Decimal("106"))

    decision = PortfolioRiskManager(settings).evaluate(  # type: ignore[arg-type]
        poor_payoff, account()
    )

    assert not decision.approved
    assert "reward/risk" in decision.reason


def test_daily_loss_limit_is_measured_from_day_start_not_all_time_peak(
    settings: object,
) -> None:
    """Regression: daily stops used to widen exactly when the account was in drawdown.

    Denominating a 2% daily stop on an all-time peak of 100k keeps the dollar allowance at
    2,000 even after equity has fallen to 80k -- an effective 2.5% stop, loosening as losses
    mount. Measuring against day-start equity keeps the percentage honest.
    """
    drawn_down = replace(
        account(),
        equity=Decimal("80000"),
        available_balance=Decimal("80000"),
        peak_equity=Decimal("100000"),
        day_start_equity=Decimal("80000"),
        realized_pnl_today=Decimal("-1700"),  # 2.1% of day start, 1.7% of the peak
    )
    decision = PortfolioRiskManager(settings).evaluate(signal(), drawn_down)  # type: ignore[arg-type]

    assert not decision.approved
    assert "daily loss" in decision.reason


def test_intraday_mark_to_market_limit_uses_day_start_equity(settings: object) -> None:
    drawn_down = replace(
        account(),
        equity=Decimal("80000"),
        available_balance=Decimal("80000"),
        peak_equity=Decimal("100000"),
        day_start_equity=Decimal("80000"),
        mark_to_market_pnl_today=Decimal("-2500"),  # 3.1% of day start, 2.5% of the peak
    )
    decision = PortfolioRiskManager(settings).evaluate(signal(), drawn_down)  # type: ignore[arg-type]

    assert not decision.approved
    assert "mark-to-market" in decision.reason


def test_daily_limits_fall_back_to_equity_when_day_start_is_unset(settings: object) -> None:
    """A snapshot that never passed through the portfolio tracker must still be gated."""
    losing = replace(account(), realized_pnl_today=Decimal("-2500"))
    decision = PortfolioRiskManager(settings).evaluate(signal(), losing)  # type: ignore[arg-type]
    assert not decision.approved
    assert "daily loss" in decision.reason
