from decimal import Decimal

import pytest

from algotrader.domain.models import Direction
from algotrader.domain.precision import (
    capped_entry_limit_price,
    normalize_bracket_prices,
    normalize_stop_price,
)


def test_long_bracket_rounds_toward_entry_on_contract_tick() -> None:
    stop, target = normalize_bracket_prices(
        direction=Direction.LONG,
        entry_price=Decimal("0.0202700"),
        stop_price=Decimal("0.019641234"),
        target_price=Decimal("0.021527891"),
        tick_size=Decimal("0.0000100"),
    )

    assert stop == Decimal("0.0196500")
    assert target == Decimal("0.0215200")


def test_short_bracket_rounds_toward_entry_on_non_decimal_tick() -> None:
    stop, target = normalize_bracket_prices(
        direction=Direction.SHORT,
        entry_price=Decimal("100"),
        stop_price=Decimal("103.47"),
        target_price=Decimal("93.06"),
        tick_size=Decimal("0.05"),
    )

    assert stop == Decimal("103.45")
    assert target == Decimal("93.10")


def test_rounding_rejects_bracket_that_collapses_onto_entry() -> None:
    with pytest.raises(ValueError, match="rounded long bracket"):
        normalize_bracket_prices(
            direction=Direction.LONG,
            entry_price=Decimal("100"),
            stop_price=Decimal("99.99"),
            target_price=Decimal("100.01"),
            tick_size=Decimal("1"),
        )


def test_normalize_stop_allows_profitable_long_trailing_level() -> None:
    assert normalize_stop_price(
        direction=Direction.LONG,
        stop_price=Decimal("102.019"),
        tick_size=Decimal("0.01"),
    ) == Decimal("102.01")


def test_normalize_stop_allows_profitable_short_trailing_level() -> None:
    assert normalize_stop_price(
        direction=Direction.SHORT,
        stop_price=Decimal("97.981"),
        tick_size=Decimal("0.01"),
    ) == Decimal("97.99")


@pytest.mark.parametrize(
    ("direction", "reference", "expected"),
    [
        (Direction.LONG, Decimal("103"), Decimal("102.50")),
        (Direction.SHORT, Decimal("97"), Decimal("97.50")),
    ],
)
def test_entry_limit_never_crosses_half_atr_signal_boundary(
    direction: Direction, reference: Decimal, expected: Decimal
) -> None:
    price = capped_entry_limit_price(
        direction=direction,
        reference_price=reference,
        signal_bar_close=Decimal("100"),
        atr=Decimal("5"),
        tick_size=Decimal("0.01"),
        offset_bps=Decimal("5"),
        max_entry_chase_atr=Decimal("0.5"),
    )

    assert price == expected
