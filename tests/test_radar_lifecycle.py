from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from algotrader.api.portal import reconcile_radar_cohort
from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.models import Candle
from algotrader.domain.radar_lifecycle import (
    RadarLifecycleState,
    evaluate_radar_candidate_lifecycle,
)
from tests.conftest import make_instrument


def test_long_candidate_pulling_back_to_limit_entry_is_retained_at_entry_zone() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "HOMEUSDT",
        "direction": "long",
        "setup_label": "COIL_SPRING_LONG",
        "current_price": "0.005463",
        "suggested_entry": "0.005338",
        "pullback_entry": "0.005338",
        "breakout_trigger": "0.005649",
        "suggested_stop": "0.004933",
        "suggested_targets": ["0.005946", "0.006351", "0.006877"],
        "poc_price": "0.005203",
        "val_price": "0.005024",
        "vah_price": "0.005596",
        "evaluated_at": (now - timedelta(hours=16)).isoformat(),
    }
    candles = [
        {"time": int((now - timedelta(hours=2)).timestamp()), "open": 0.00542, "high": 0.00544, "low": 0.005344, "close": 0.00538, "volume": 1000},
        {"time": int((now - timedelta(hours=1)).timestamp()), "open": 0.00538, "high": 0.00541, "low": 0.00536, "close": 0.00539, "volume": 1200},
    ]

    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("0.005390"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is True
    assert decision.state is RadarLifecycleState.AT_ENTRY_ZONE
    assert "AT ENTRY ZONE" in decision.label


def test_long_candidate_that_bounced_from_limit_entry_is_in_active_trade() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "ZECUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "1313.98",
        "suggested_entry": "1257.18",
        "breakout_trigger": "1325.66",
        "suggested_stop": "1096.55",
        "suggested_targets": ["1484.75", "1590.81", "1728.69"],
        "poc_price": "1227.20",
        "val_price": "1192.25",
        "vah_price": "1323.31",
        "evaluated_at": (now - timedelta(hours=16)).isoformat(),
    }
    candles = [
        {"time": int((now - timedelta(hours=6)).timestamp()), "open": 1310.0, "high": 1387.98, "low": 1247.56, "close": 1311.79, "volume": 5000},
    ]

    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("1311.79"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is True
    assert decision.state is RadarLifecycleState.IN_ACTIVE_TRADE
    assert decision.pnl_from_entry_pct == Decimal("4.3")
    assert "+4.3%" in decision.label


def test_candidate_breaching_stop_loss_is_eliminated() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    long_cand = {
        "symbol": "FAILUSDT",
        "direction": "long",
        "suggested_entry": "10.0",
        "suggested_stop": "9.20",
        "suggested_targets": ["11.5", "12.5", "14.0"],
        "evaluated_at": (now - timedelta(hours=8)).isoformat(),
    }
    candles = [
        {"time": int((now - timedelta(hours=2)).timestamp()), "open": 9.8, "high": 9.9, "low": 9.15, "close": 9.5, "volume": 100},
    ]

    decision = evaluate_radar_candidate_lifecycle(
        long_cand,
        current_price=Decimal("9.50"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is False
    assert decision.state is RadarLifecycleState.INVALIDATED

    # Short candidate squeezing >6% above entry (like TAUSDT / LYNUSDT) is also eliminated
    short_cand = {
        "symbol": "LYNUSDT",
        "direction": "short",
        "suggested_entry": "0.02037",
        "suggested_stop": "0.02250",
        "suggested_targets": ["0.01800", "0.01650"],
        "evaluated_at": (now - timedelta(hours=12)).isoformat(),
    }
    short_decision = evaluate_radar_candidate_lifecycle(
        short_cand,
        current_price=Decimal("0.03017"),
        recent_candles_1h=[],
        now=now,
    )
    assert short_decision.retained is False
    assert short_decision.state is RadarLifecycleState.INVALIDATED


def test_wyckoff_spring_48h_memory_retains_coil_spring_archetype() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("0.005398"),
        low_24h=Decimal("0.005344"),  # 24h low is above EMA50 because the shakeout wick was 30h ago
        low_48h=Decimal("0.005120"),  # 48h low captured the Wyckoff Spring shakeout wick below POC & EMA50
        ema50_4h=Decimal("0.005342"),
        poc_30d=Decimal("0.005213"),
        vah_30d=Decimal("0.005596"),
        atr14_4h=Decimal("0.00012"),
        vcr_4h=Decimal("0.82"),
        oi_delta_7d=Decimal("0.091"),
        oi_delta_4h=Decimal("0.0"),
        ret_7d=Decimal("0.02"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.077"),
        local_swing_high_4h=Decimal("0.005649"),
        rvol_4h=Decimal("0.95"),
        norm_momentum_1h=Decimal("0.1"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("-0.00001"),
        macd_hist_4h_prev=Decimal("0.0"),
        macd_hist_1h=Decimal("0.00001"),
        taker_buy_ratio_4h=Decimal("0.92"),
        val_30d=Decimal("0.005007"),
    )

    assert classify_long_archetype(features) is SetupLabel.COIL_SPRING_LONG


@pytest.mark.asyncio
async def test_reconcile_radar_cohort_retains_active_and_eliminates_stopped() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    home_inst = make_instrument(symbol="HOMEUSDT")
    lyn_inst = make_instrument(symbol="LYNUSDT")

    class FakeFutures:
        async def instruments(self) -> list[Any]:
            return [home_inst, lyn_inst]

        async def candles(self, inst: Any, interval: str, limit: int) -> list[Candle]:
            del interval, limit
            if inst.id.symbol == "HOMEUSDT":
                return [
                    Candle(
                        opened_at=now - timedelta(hours=1),
                        closed_at=now,
                        open=Decimal("0.00536"),
                        high=Decimal("0.00541"),
                        low=Decimal("0.00535"),
                        close=Decimal("0.00539"),
                        volume=Decimal("10000"),
                    )
                ]
            return [
                Candle(
                    opened_at=now - timedelta(hours=1),
                    closed_at=now,
                    open=Decimal("0.025"),
                    high=Decimal("0.031"),
                    low=Decimal("0.024"),
                    close=Decimal("0.03017"),
                    volume=Decimal("10000"),
                )
            ]

    prior = [
        {
            "symbol": "HOMEUSDT",
            "direction": "long",
            "setup_label": "COIL_SPRING_LONG",
            "current_price": "0.005463",
            "suggested_entry": "0.005338",
            "breakout_trigger": "0.005649",
            "suggested_stop": "0.004933",
            "suggested_targets": ["0.005946", "0.006351", "0.006877"],
            "poc_price": "0.005203",
            "val_price": "0.005024",
            "evaluated_at": (now - timedelta(hours=14)).isoformat(),
        },
        {
            "symbol": "LYNUSDT",
            "direction": "short",
            "setup_label": "UTAD_DIVERGENCE_SHORT",
            "current_price": "0.02037",
            "suggested_entry": "0.02037",
            "suggested_stop": "0.02250",
            "suggested_targets": ["0.01800", "0.01650"],
            "evaluated_at": (now - timedelta(hours=14)).isoformat(),
        },
    ]

    seen_bases: set[str] = set()
    retained = await reconcile_radar_cohort(prior, seen_bases, FakeFutures(), now=now)

    assert [r["symbol"] for r in retained] == ["HOMEUSDT"]
    assert retained[0]["current_price"] == "0.00539"
    assert retained[0]["radar_status"] == "AT_ENTRY_ZONE"
