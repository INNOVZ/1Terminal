from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.backtest import (
    BacktestAssumptions,
    StrategyBacktester,
    TradeSimulationRequest,
)
from algotrader.config import Settings
from algotrader.domain.models import Candle, Direction, MarketKind, Venue
from tests.conftest import make_instrument, trending_candles


def test_backtest_assumes_stop_first_when_stop_and_target_share_a_bar(
    settings: Settings,
) -> None:
    backtester = StrategyBacktester(
        settings,
        BacktestAssumptions(
            initial_equity=Decimal("100"),
            fee_bps_per_side=Decimal("0"),
            stop_slippage_bps=Decimal("0"),
            funding_bps_per_8h=Decimal("0"),
        ),
    )
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        Candle(
            opened_at=opened,
            closed_at=opened + timedelta(hours=1),
            open=Decimal("100"),
            high=Decimal("111"),
            low=Decimal("94"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
    ]

    trade, exit_index = backtester._simulate_trade(
        TradeSimulationRequest(
            Direction.LONG,
            opened,
            Decimal("95"),
            Decimal("110"),
            Decimal("100"),
            Decimal("100"),
            Decimal("5"),
            Decimal("0.01"),
            Decimal("1"),
            Decimal("0.001"),
            bars,
            0,
            MarketKind.SPOT,
        )
    )

    assert trade is not None
    assert exit_index == 0
    assert trade.exit_reason == "stop"
    assert trade.entry_price == Decimal("100.05")
    assert trade.exit_price == Decimal("95")
    assert trade.net_pnl == Decimal("-5.05")
    assert trade.max_favorable_excursion_r == Decimal("0")
    assert trade.max_adverse_excursion_r > Decimal("0.9")


def test_backtest_records_excursion_without_using_prefill_entry_bar_extremes(
    settings: Settings,
) -> None:
    backtester = StrategyBacktester(
        settings,
        BacktestAssumptions(
            spread_bps=Decimal("0"),
            fee_bps_per_side=Decimal("0"),
            stop_slippage_bps=Decimal("0"),
            funding_bps_per_8h=Decimal("0"),
        ),
    )
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        Candle(
            opened_at=opened,
            closed_at=opened + timedelta(hours=1),
            open=Decimal("100"),
            high=Decimal("104"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=Decimal("1000"),
        ),
        Candle(
            opened_at=opened + timedelta(hours=1),
            closed_at=opened + timedelta(hours=2),
            open=Decimal("101"),
            high=Decimal("103"),
            low=Decimal("99.5"),
            close=Decimal("102"),
            volume=Decimal("1000"),
        ),
    ]

    trade, _ = backtester._simulate_trade(
        TradeSimulationRequest(
            Direction.LONG,
            opened,
            Decimal("95"),
            Decimal("110"),
            Decimal("100"),
            Decimal("100"),
            Decimal("5"),
            Decimal("0.01"),
            Decimal("1"),
            Decimal("0.001"),
            bars,
            0,
            MarketKind.SPOT,
        )
    )

    assert trade is not None
    assert trade.max_favorable_excursion_r == Decimal("0.6")
    assert trade.max_adverse_excursion_r == Decimal("0.1")


def test_research_stagnation_exit_closes_a_trade_that_makes_no_progress(
    settings: Settings,
) -> None:
    backtester = StrategyBacktester(
        settings,
        BacktestAssumptions(
            spread_bps=Decimal("0"),
            fee_bps_per_side=Decimal("0"),
            stop_slippage_bps=Decimal("0"),
            funding_bps_per_8h=Decimal("0"),
            stagnation_exit_bars=2,
            stagnation_min_favorable_r=Decimal("0.5"),
        ),
    )
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        Candle(
            opened_at=opened + timedelta(hours=index),
            closed_at=opened + timedelta(hours=index + 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.2"),
            volume=Decimal("1000"),
        )
        for index in range(3)
    ]

    trade, exit_index = backtester._simulate_trade(
        TradeSimulationRequest(
            Direction.LONG,
            opened,
            Decimal("95"),
            Decimal("110"),
            Decimal("100"),
            Decimal("100"),
            Decimal("5"),
            Decimal("0.01"),
            Decimal("1"),
            Decimal("0.001"),
            bars,
            0,
            MarketKind.SPOT,
        )
    )

    assert trade is not None
    assert exit_index == 1
    assert trade.exit_reason == "stagnation"
    assert trade.exit_price == Decimal("100.2")


def test_backtest_ioc_does_not_fill_when_next_bar_never_reaches_limit(
    settings: Settings,
) -> None:
    backtester = StrategyBacktester(
        settings,
        BacktestAssumptions(spread_bps=Decimal("10")),
    )
    opened = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
        Candle(
            opened_at=opened,
            closed_at=opened + timedelta(hours=1),
            open=Decimal("101"),
            high=Decimal("102"),
            low=Decimal("100.50"),
            close=Decimal("101.50"),
            volume=Decimal("1000"),
        )
    ]

    trade, exit_index = backtester._simulate_trade(
        TradeSimulationRequest(
            Direction.LONG,
            opened,
            Decimal("95"),
            Decimal("110"),
            Decimal("100"),
            Decimal("100"),
            Decimal("5"),
            Decimal("0.01"),
            Decimal("1"),
            Decimal("0.001"),
            bars,
            0,
            MarketKind.SPOT,
        )
    )

    assert trade is None
    assert exit_index == 0


def test_backtest_rejects_unaligned_intervals(settings: Settings) -> None:
    instrument = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
    )
    backtester = StrategyBacktester(settings, BacktestAssumptions())

    with pytest.raises(ValueError, match="divide evenly"):
        backtester.run(
            instrument,
            trending_candles(count=261),
            trending_candles(count=210),
            interval_hours=5,
        )
