from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.application.crypto_screener_backtest import (
    CryptoScreenerBacktestAssumptions,
    CryptoScreenerBacktester,
    ScreenerBacktestResult,
    ScreenerBacktestTrade,
)
from algotrader.domain.models import Candle, Direction, Instrument, InstrumentId, MarketKind, Venue


def test_screener_backtest_assumptions_defaults() -> None:
    assumptions = CryptoScreenerBacktestAssumptions()
    assert assumptions.initial_equity == Decimal("10000")
    assert assumptions.risk_per_trade_pct == Decimal("0.01")
    assert assumptions.max_open_trades == 5
    assert assumptions.entry_mode == "pullback"
    assert assumptions.scale_out_at_target1 is True


def test_screener_backtest_trade_and_result_as_dict() -> None:
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    trade = ScreenerBacktestTrade(
        symbol="AXLUSDT",
        direction=Direction.LONG,
        archetype="SHORT_SQUEEZE_ABSORPTION_LONG",
        score=Decimal("88.5"),
        conviction=Decimal("80.0"),
        opened_at=now,
        closed_at=now + timedelta(hours=12),
        entry_price=Decimal("0.055"),
        exit_price=Decimal("0.065"),
        stop_loss=Decimal("0.050"),
        target1=Decimal("0.060"),
        target2=Decimal("0.065"),
        quantity=Decimal("20000"),
        gross_pnl=Decimal("200"),
        fees=Decimal("2.4"),
        funding=Decimal("0.5"),
        net_pnl=Decimal("197.1"),
        return_r=Decimal("1.97"),
        exit_reason="TARGET_2",
        mfe_r=Decimal("2.0"),
        mae_r=Decimal("0.2"),
        scaled_out=True,
    )

    result = ScreenerBacktestResult(
        symbols=["AXLUSDT"],
        started_at=now - timedelta(days=30),
        ended_at=now,
        history_days=30,
        initial_equity=Decimal("10000"),
        final_equity=Decimal("10197.1"),
        total_return_pct=Decimal("1.971"),
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate_pct=Decimal("100"),
        profit_factor=Decimal("99.9"),
        expectancy_r=Decimal("1.97"),
        max_drawdown_pct=Decimal("0"),
        max_drawdown_usd=Decimal("0"),
        sharpe_ratio=Decimal("2.5"),
        avg_holding_hours=Decimal("12"),
        trades=[trade],
        archetype_performance={
            "SHORT_SQUEEZE_ABSORPTION_LONG": {
                "trades": 1,
                "wins": 1,
                "losses": 0,
                "win_rate": "100.0%",
                "profit_factor": "99.9",
                "net_pnl": "+$197.10",
                "avg_r": "+1.97R",
            }
        },
        symbol_performance={
            "AXLUSDT": {
                "trades": 1,
                "win_rate": "100.0%",
                "net_pnl": "+$197.10",
                "avg_r": "+1.97R",
            }
        },
    )

    data = result.as_dict()
    assert data["summary"]["total_trades"] == 1
    assert data["summary"]["win_rate_pct"] == "100.00%"
    assert "SHORT_SQUEEZE_ABSORPTION_LONG" in data["archetype_performance"]
    assert len(data["trades"]) == 1
    assert data["trades"][0]["exit_reason"] == "TARGET_2"
