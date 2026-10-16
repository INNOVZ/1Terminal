from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from algotrader.adapters.paper.broker import PaperBroker
from algotrader.domain.models import (
    Direction,
    MomentumState,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    ProtectionSet,
    Quote,
    TimeInForce,
    TradeSignal,
    Venue,
)
from tests.conftest import make_instrument


class QuoteMarketData:
    venue = Venue.PAPER

    def __init__(self, quote: Quote) -> None:
        self.quote = quote

    async def quotes(self, instruments: object) -> tuple[Quote, ...]:
        del instruments
        return (self.quote,)


def _intent(limit_price: str) -> OrderIntent:
    instrument = make_instrument(venue=Venue.PAPER)
    return OrderIntent(
        instrument=instrument,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        client_order_id=f"paper-{limit_price}",
        direction=Direction.LONG,
        time_in_force=TimeInForce.IOC,
        price=Decimal(limit_price),
    )


def _quote(ask: str) -> Quote:
    instrument = make_instrument(venue=Venue.PAPER)
    return Quote(
        instrument_id=instrument.id,
        bid=Decimal(ask) - Decimal("0.02"),
        ask=Decimal(ask),
        last=Decimal(ask),
        quote_volume_24h=Decimal("10000000"),
        observed_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


async def test_paper_ioc_expires_when_limit_is_not_marketable() -> None:
    broker = PaperBroker(QuoteMarketData(_quote("100.10")))  # type: ignore[arg-type]

    result = await broker.place_order(_intent("100.05"))

    assert result.status is OrderStatus.EXPIRED
    assert result.executed_quantity == 0
    assert result.average_price is None
    assert (await broker.account_snapshot()).positions == ()


async def test_paper_ioc_fills_at_quote_not_at_worse_limit() -> None:
    broker = PaperBroker(QuoteMarketData(_quote("100.02")))  # type: ignore[arg-type]

    result = await broker.place_order(_intent("100.05"))

    assert result.status is OrderStatus.FILLED
    assert result.average_price == Decimal("100.02")
    assert len((await broker.account_snapshot()).positions) == 1


class MovableMarketData:
    """Market data whose price the test can walk, so brackets can actually trigger."""

    venue = Venue.PAPER

    def __init__(self, price: str) -> None:
        self.instrument = make_instrument(venue=Venue.PAPER)
        self.price = Decimal(price)

    def move(self, price: str) -> None:
        self.price = Decimal(price)

    async def quotes(self, instruments: object) -> tuple[Quote, ...]:
        del instruments
        return (
            Quote(
                instrument_id=self.instrument.id,
                bid=self.price - Decimal("0.02"),
                ask=self.price,
                last=self.price,
                quote_volume_24h=Decimal("10000000"),
                observed_at=datetime(2026, 8, 4, tzinfo=UTC),
            ),
        )


def _signal(stop: str, target: str) -> TradeSignal:
    return TradeSignal(
        instrument=make_instrument(venue=Venue.PAPER),
        direction=Direction.LONG,
        score=Decimal("90"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("2"),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
        strategy="test",
        timeframe="1h/4h",
        momentum_state=MomentumState.GAINING,
        rationale=("trend",),
    )


async def _open_protected_position(
    market: MovableMarketData, stop: str, target: str
) -> tuple[PaperBroker, ProtectionSet]:
    broker = PaperBroker(market, starting_equity=Decimal("10000"))  # type: ignore[arg-type]
    await broker.place_order(
        OrderIntent(
            instrument=market.instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("10"),
            client_order_id="entry-1",
            direction=Direction.LONG,
        )
    )
    protection = await broker.place_protection(_signal(stop, target), Decimal("10"))
    return broker, protection


async def test_paper_marks_position_to_market_instead_of_freezing_at_entry() -> None:
    """Regression: mark_price used to stay at the fill price forever."""
    market = MovableMarketData("100")
    broker, _ = await _open_protected_position(market, stop="90", target="130")

    market.move("120")
    snapshot = await broker.account_snapshot()
    position = snapshot.positions[0]

    assert position.mark_price > Decimal("100")
    assert position.unrealized_pnl > Decimal("0")


async def test_paper_stop_actually_fires_on_a_crash() -> None:
    """Regression for the live incident: paper brackets were inert records.

    A position could ride a move up and give all of it back without either protective leg
    ever triggering, because nothing evaluated them.
    """
    market = MovableMarketData("100")
    broker, _ = await _open_protected_position(market, stop="90", target="200")

    market.move("150")
    assert (await broker.account_snapshot()).positions, "target not hit yet, position stays open"

    market.move("85")  # flash crash straight through the stop
    exits = await broker.settle()

    assert len(exits) == 1
    assert exits[0].reason == "stop"
    assert not (await broker.account_snapshot()).positions, "stop must close the position"


async def test_paper_target_fires_and_realizes_profit() -> None:
    market = MovableMarketData("100")
    broker, _ = await _open_protected_position(market, stop="90", target="130")

    market.move("131")
    exits = await broker.settle()

    assert len(exits) == 1
    assert exits[0].reason == "target"
    assert exits[0].realized_pnl > Decimal("0")
    assert (await broker.account_snapshot()).equity > Decimal("10000")


async def test_trailed_stop_replaces_the_previous_bracket() -> None:
    market = MovableMarketData("100")
    broker, protection = await _open_protected_position(market, stop="90", target="200")

    market.move("150")
    await broker.replace_protection(_signal("140", "200"), Decimal("10"), existing=protection)

    # The superseded stop must not still be live against the same inventory.
    assert protection.stop is not None
    old_stop = await broker.query_order(
        market.instrument.id, protection.stop.result.client_order_id
    )
    assert old_stop is not None and old_stop.status is OrderStatus.CANCELED

    market.move("139")
    exits = await broker.settle()
    assert len(exits) == 1
    assert exits[0].reason == "stop"
    assert exits[0].exit_price > Decimal("130"), "exits at the trailed stop, not the original"


async def test_runner_replacement_removes_fixed_target_but_keeps_stop() -> None:
    market = MovableMarketData("100")
    broker, protection = await _open_protected_position(market, stop="90", target="130")

    runner = await broker.replace_protection(
        _signal("110", "130"),
        Decimal("10"),
        existing=protection,
        include_target=False,
    )

    assert runner.stop is not None
    assert runner.target is None
    market.move("140")
    assert await broker.settle() == (), "runner must be allowed through the old 2R target"

    market.move("109")
    exits = await broker.settle()
    assert len(exits) == 1
    assert exits[0].reason == "stop"


async def test_partial_reduce_leaves_a_runner() -> None:
    market = MovableMarketData("100")
    broker, _ = await _open_protected_position(market, stop="90", target="200")

    market.move("120")
    await broker.reduce_position(
        market.instrument, Direction.LONG, Decimal("5"), client_order_id="scale-1"
    )

    positions = (await broker.account_snapshot()).positions
    assert len(positions) == 1
    assert positions[0].quantity == Decimal("5")
