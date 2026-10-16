from dataclasses import replace
from decimal import Decimal

import pytest

from algotrader.application.strategy import TrendMomentumStrategy
from algotrader.config import Settings
from algotrader.domain.models import Candidate, Direction, MarketKind, MomentumState, Venue
from tests.conftest import make_instrument


def test_strategy_normalizes_bracket_before_risk_sizing(settings: Settings) -> None:
    instrument = replace(make_instrument(), price_tick=Decimal("0.05"))
    candidate = Candidate(
        instrument=instrument,
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("0.333"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
    )

    signal = TrendMomentumStrategy(settings).signal(candidate)

    assert signal.stop_price == Decimal("99.55")
    assert signal.target_price == Decimal("100.95")
    assert signal.risk_per_unit == Decimal("0.45")


def test_strategy_refuses_long_entry_near_resistance(settings: Settings) -> None:
    candidate = Candidate(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal("95"),
        resistance_price=Decimal("100.4"),
    )

    with pytest.raises(ValueError, match="resistance"):
        TrendMomentumStrategy(settings).signal(candidate)


def test_strategy_refuses_entry_without_gaining_momentum(settings: Settings) -> None:
    candidate = Candidate(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("100"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.MIXED,
        reasons=("momentum mixed",),
        support_price=Decimal("95"),
        resistance_price=Decimal("110"),
    )

    with pytest.raises(ValueError, match="gaining"):
        TrendMomentumStrategy(settings).signal(candidate)


def test_strategy_defends_optional_pullback_gate(settings: Settings) -> None:
    strict = settings.model_copy(update={"require_pullback_confirmation": True})
    candidate = Candidate(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal("95"),
        resistance_price=Decimal("110"),
        continuation_trigger_passed=False,
    )

    with pytest.raises(ValueError, match="pullback"):
        TrendMomentumStrategy(strict).signal(candidate)


@pytest.mark.parametrize(
    ("direction", "support", "resistance", "expected_stop", "expected_target"),
    [
        (Direction.LONG, "97.5", "120", "97.25", "105.50"),
        (Direction.SHORT, "80", "102.5", "102.75", "94.50"),
    ],
)
def test_strategy_uses_nearby_structure_beyond_atr_stop_symmetrically(
    settings: Settings,
    direction: Direction,
    support: str,
    resistance: str,
    expected_stop: str,
    expected_target: str,
) -> None:
    instrument = make_instrument()
    if direction is Direction.SHORT:
        instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    candidate = Candidate(
        instrument=instrument,
        direction=direction,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal(support),
        resistance_price=Decimal(resistance),
    )

    signal = TrendMomentumStrategy(settings).signal(candidate)

    assert signal.stop_price == Decimal(expected_stop)
    assert signal.target_price == Decimal(expected_target)
    assert signal.reward_risk_ratio == Decimal("2")
    expected_level = "support" if direction is Direction.LONG else "resistance"
    expected_prefix = "below" if direction is Direction.LONG else "above"
    assert any(f"stop: {expected_prefix} {expected_level}" in reason for reason in signal.rationale)


@pytest.mark.parametrize(
    ("direction", "support", "resistance", "expected_stop"),
    [
        (Direction.LONG, "90", "120", "98.50"),
        (Direction.SHORT, "80", "110", "101.50"),
    ],
)
def test_strategy_ignores_structure_beyond_distance_cap(
    settings: Settings,
    direction: Direction,
    support: str,
    resistance: str,
    expected_stop: str,
) -> None:
    instrument = make_instrument()
    if direction is Direction.SHORT:
        instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    candidate = Candidate(
        instrument=instrument,
        direction=direction,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal(support),
        resistance_price=Decimal(resistance),
    )

    signal = TrendMomentumStrategy(settings).signal(candidate)

    assert signal.stop_price == Decimal(expected_stop)


@pytest.mark.parametrize(
    ("direction", "support", "resistance", "expected_target"),
    [
        (Direction.LONG, "90", "102.80", "102.55"),
        (Direction.SHORT, "97.20", "110", "97.45"),
    ],
)
def test_target_exits_before_opposing_structure_symmetrically(
    settings: Settings,
    direction: Direction,
    support: str,
    resistance: str,
    expected_target: str,
) -> None:
    instrument = make_instrument()
    if direction is Direction.SHORT:
        instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    candidate = Candidate(
        instrument=instrument,
        direction=direction,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal(support),
        resistance_price=Decimal(resistance),
    )

    signal = TrendMomentumStrategy(settings).signal(candidate)

    assert signal.target_price == Decimal(expected_target)
    assert signal.reward_risk_ratio == Decimal("1.7")
    expected_level = "resistance" if direction is Direction.LONG else "support"
    assert f"target: before {expected_level}, above minimum reward/risk" in signal.rationale


@pytest.mark.parametrize(
    ("direction", "support", "resistance"),
    [
        (Direction.LONG, "90", "102.20"),
        (Direction.SHORT, "97.80", "110"),
    ],
)
def test_strategy_rejects_opposing_structure_below_minimum_reward_risk(
    settings: Settings,
    direction: Direction,
    support: str,
    resistance: str,
) -> None:
    instrument = make_instrument()
    if direction is Direction.SHORT:
        instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    candidate = Candidate(
        instrument=instrument,
        direction=direction,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.GAINING,
        reasons=("trend",),
        support_price=Decimal(support),
        resistance_price=Decimal(resistance),
    )

    with pytest.raises(ValueError, match="insufficient reward/risk"):
        TrendMomentumStrategy(settings).signal(candidate)
