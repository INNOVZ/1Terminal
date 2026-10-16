from __future__ import annotations

from decimal import Decimal

from algotrader.domain.models import Candle, Direction, OrderSide, Quote


def quote_limit_fill_price(
    *, side: OrderSide, quote: Quote, limit_price: Decimal
) -> Decimal | None:
    """Return a live-quote IOC fill only when the limit is currently marketable."""
    if limit_price <= 0:
        raise ValueError("limit price must be positive")
    market_price = quote.ask if side is OrderSide.BUY else quote.bid
    marketable = (
        market_price <= limit_price if side is OrderSide.BUY else market_price >= limit_price
    )
    return market_price if marketable else None


def bar_limit_fill_price(
    *,
    direction: Direction,
    limit_price: Decimal,
    bar: Candle,
    spread_bps: Decimal,
) -> Decimal | None:
    """Conservative OHLC fill model for an IOC submitted at the next bar open.

    A marketable order fills at the spread-adjusted open. If the limit is touched only
    intrabar, it fills at the limit because OHLC data cannot prove price improvement.
    """
    if limit_price <= 0 or spread_bps < 0:
        raise ValueError("fill-model prices must be positive")
    half_spread = spread_bps / Decimal("20000")
    if direction is Direction.LONG:
        opening_ask = bar.open * (Decimal("1") + half_spread)
        if opening_ask <= limit_price:
            return opening_ask
        intrabar_ask_low = bar.low * (Decimal("1") + half_spread)
        return limit_price if intrabar_ask_low <= limit_price else None

    opening_bid = bar.open * (Decimal("1") - half_spread)
    if opening_bid >= limit_price:
        return opening_bid
    intrabar_bid_high = bar.high * (Decimal("1") - half_spread)
    return limit_price if intrabar_bid_high >= limit_price else None
