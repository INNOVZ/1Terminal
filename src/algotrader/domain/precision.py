from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from algotrader.domain.models import Direction


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    units = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    return units * increment


def ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("increment must be positive")
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def decimal_places_from_tick_size(tick_size: Decimal) -> int:
    """Derive display decimal precision dynamically from instrument tick_size:
    decimals = max(2, -int(math.floor(math.log10(tick_size))))
    """
    if tick_size <= Decimal("0"):
        return 4
    import math

    try:
        return max(2, -int(math.floor(math.log10(float(tick_size)))))
    except Exception:
        return 4


def normalize_price(raw_price: Decimal, tick_size: Decimal) -> Decimal:
    """Normalize raw price to exchange tick precision: round(raw_price / tick_size) * tick_size."""
    if tick_size <= Decimal("0"):
        raise ValueError("tick_size must be positive")
    if raw_price <= Decimal("0"):
        return Decimal("0")
    from decimal import ROUND_HALF_UP

    units = (raw_price / tick_size).to_integral_value(rounding=ROUND_HALF_UP)
    return units * tick_size


def normalize_quantity(raw_qty: Decimal, step_size: Decimal) -> Decimal:
    """Normalize raw quantity to exchange step size: floor(raw_qty / step_size) * step_size."""
    return floor_to_increment(raw_qty, step_size)


def entry_limit_price(
    *,
    direction: Direction,
    reference_price: Decimal,
    tick_size: Decimal,
    offset_bps: Decimal,
) -> Decimal:
    if reference_price <= 0 or tick_size <= 0 or offset_bps < 0:
        raise ValueError("entry limit inputs must be positive")
    offset = offset_bps / Decimal("10000")
    if direction is Direction.LONG:
        return floor_to_increment(reference_price * (Decimal("1") + offset), tick_size)
    return ceil_to_increment(reference_price * (Decimal("1") - offset), tick_size)


def capped_entry_limit_price(
    *,
    direction: Direction,
    reference_price: Decimal,
    signal_bar_close: Decimal,
    atr: Decimal,
    tick_size: Decimal,
    offset_bps: Decimal,
    max_entry_chase_atr: Decimal,
) -> Decimal:
    """Cap a marketable IOC limit by both quote tolerance and signal-bar ATR distance."""
    if signal_bar_close <= 0 or atr <= 0 or max_entry_chase_atr <= 0:
        raise ValueError("ATR entry cap inputs must be positive")
    quote_limit = entry_limit_price(
        direction=direction,
        reference_price=reference_price,
        tick_size=tick_size,
        offset_bps=offset_bps,
    )
    atr_boundary = (
        signal_bar_close + atr * max_entry_chase_atr
        if direction is Direction.LONG
        else signal_bar_close - atr * max_entry_chase_atr
    )
    atr_limit = entry_limit_price(
        direction=direction,
        reference_price=atr_boundary,
        tick_size=tick_size,
        offset_bps=Decimal("0"),
    )
    return (
        min(quote_limit, atr_limit) if direction is Direction.LONG else max(quote_limit, atr_limit)
    )


def normalize_bracket_prices(
    *,
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    tick_size: Decimal,
) -> tuple[Decimal, Decimal]:
    """Snap a bracket toward entry so exchange precision never increases planned risk."""
    if min(entry_price, stop_price, target_price, tick_size) <= 0:
        raise ValueError("entry, stop, target, and tick size must be positive")

    if direction is Direction.LONG:
        normalized_stop = ceil_to_increment(stop_price, tick_size)
        normalized_target = floor_to_increment(target_price, tick_size)
        if not normalized_stop < entry_price < normalized_target:
            raise ValueError("rounded long bracket must satisfy stop < entry < target")
    else:
        normalized_stop = floor_to_increment(stop_price, tick_size)
        normalized_target = ceil_to_increment(target_price, tick_size)
        if not normalized_target < entry_price < normalized_stop:
            raise ValueError("rounded short bracket must satisfy target < entry < stop")

    return normalized_stop, normalized_target


def normalize_stop_price(
    *, direction: Direction, stop_price: Decimal, tick_size: Decimal
) -> Decimal:
    """Normalize an amended stop without requiring it to remain beyond entry.

    A profitable trailing stop legitimately crosses the entry price. Rounding away from
    the current market preserves the intended stop distance instead of tightening it.
    """
    if stop_price <= 0 or tick_size <= 0:
        raise ValueError("stop and tick size must be positive")
    if direction is Direction.LONG:
        return floor_to_increment(stop_price, tick_size)
    return ceil_to_increment(stop_price, tick_size)
