from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
import pytest

from algotrader.application.testnet_trader import TestnetTrader
from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
from algotrader.domain.crypto_screener_models import SetupLabel


TestnetTrader.__test__ = False


class FakeTestnetTrader(TestnetTrader):
    """TestnetTrader test harness mocking exchange network calls."""
    __test__ = False

    def __init__(self, tmp_path):
        super().__init__(
            api_key="test_key",
            api_secret="test_secret",
            max_trades_24h=5,
            max_trades_per_cycle=3,
            data_dir=tmp_path,
        )
        self.mock_active_positions = []
        self.mock_open_orders = []
        self.executed_calls = []

    async def get_active_positions(self) -> list[dict[str, Any]]:
        return self.mock_active_positions

    async def get_open_orders(self) -> list[dict[str, Any]]:
        return self.mock_open_orders

    async def execute_trade(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        self.executed_calls.append(candidate)
        return {"status": "PLACED", "symbol": candidate["symbol"]}


@pytest.mark.asyncio
async def test_opposing_position_is_blocked(tmp_path):
    trader = FakeTestnetTrader(tmp_path)
    # Simulate an active DOTUSDT SHORT position on Binance Futures
    trader.mock_active_positions = [
        {
            "symbol": "DOTUSDT",
            "position_amt": -85.4,
            "entry_price": 1.1697,
            "mark_price": 1.1491,
            "unrealized_profit": 1.75,
            "side": "SHORT",
            "leverage": 3,
        }
    ]

    # Screener suggests a LONG for DOTUSDT
    opp_long = {
        "symbol": "DOTUSDT",
        "direction": "long",
        "current_price": 1.1491,
        "suggested_entry": 1.1450,
        "suggested_stop": 1.0500,
        "suggested_targets": [1.3000],
        "risk_reward_ratio": 2.5,
        "score": 95.0,
        "conviction": 92.0,
    }

    # Screener also suggests an unrelated valid candidate
    opp_other = {
        "symbol": "SOLUSDT",
        "direction": "long",
        "current_price": 150.0,
        "suggested_entry": 149.0,
        "suggested_stop": 140.0,
        "suggested_targets": [170.0],
        "risk_reward_ratio": 2.5,
        "score": 95.0,
        "conviction": 92.0,
    }

    executed = await trader.evaluate_and_execute_candidates([opp_long, opp_other])

    # DOTUSDT should be BLOCKED because of active SHORT position
    assert all(t["symbol"] != "DOTUSDT" for t in executed)
    assert any(t["symbol"] == "SOLUSDT" for t in executed)


@pytest.mark.asyncio
async def test_open_order_blocks_duplicate(tmp_path):
    trader = FakeTestnetTrader(tmp_path)
    trader.mock_open_orders = [{"symbol": "DOTUSDT", "side": "SELL", "price": 1.1697}]

    opp_dot = {
        "symbol": "DOTUSDT",
        "direction": "short",
        "current_price": 1.1491,
        "suggested_entry": 1.1600,
        "suggested_stop": 1.2500,
        "suggested_targets": [1.0000],
        "risk_reward_ratio": 2.5,
        "score": 95.0,
        "conviction": 92.0,
    }

    executed = await trader.evaluate_and_execute_candidates([opp_dot])
    assert len(executed) == 0


def test_dead_range_consolidation_is_not_accumulation():
    # DOT in dead range without momentum and below POC
    features = LongArchetypeFeatures(
        close=Decimal("1.1491"),
        low_24h=Decimal("1.14"),
        ema50_4h=Decimal("1.1462"),
        poc_30d=Decimal("1.1720"),
        vah_30d=Decimal("1.2438"),
        val_30d=Decimal("1.1301"),
        atr14_4h=Decimal("0.02"),
        vcr_4h=Decimal("0.89"),
        oi_delta_7d=Decimal("-0.026"),
        oi_delta_4h=Decimal("-0.01"),
        ret_7d=Decimal("-0.01"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.10"),
        local_swing_high_4h=Decimal("1.24"),
        rvol_4h=Decimal("0.85"),
        norm_momentum_1h=Decimal("-0.02"),
        btc_residual_alpha_24h=Decimal("0.0"),
        macd_hist_4h=Decimal("-0.002"),
        macd_hist_4h_prev=Decimal("-0.001"),  # Decelerating momentum
        macd_hist_1h=Decimal("-0.001"),
        taker_buy_ratio_4h=Decimal("0.46"),  # Weak taker buying
        range_height_30d=Decimal("0.20"),
        pos_in_range_30d=Decimal("0.53"),
        dist_to_ceiling=Decimal("0.08"),
        funding_rate_8h=Decimal("0.0001"),
        cvd_24h=Decimal("0"),
    )

    archetype = classify_long_archetype(features)
    # Must NOT be classified as RANGE_BOTTOM_ACCUMULATION_LONG
    assert archetype != SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG
    assert archetype is None
