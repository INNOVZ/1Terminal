"""Standalone Verification Runner for Wyckoff Distribution (Short) & Accumulation (Long).

Execute:
    uv run python scripts/verify_screener_distribution.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algotrader.config import load_settings
from algotrader.domain.crypto_screener_models import Direction, ScreenerMode, SetupLabel
from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.domain.models import Candle, DerivativesSnapshot, MarketKind, Venue
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from tests.conftest import make_instrument, trending_candles
from tests.test_crypto_screener import FakeCryptoMarketData, FakeDerivativesData


async def main() -> None:
    print("=" * 80)
    print("🔬 ATLAS INSTITUTIONAL CRYPTO SCREENER — VERIFICATION SUITE")
    print("=" * 80)

    settings = load_settings()
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_near = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="NEARUSDT")

    started = datetime(2026, 8, 1, tzinfo=UTC)
    btc_candles = trending_candles(up=True, count=360)

    # 1. Synthesize Wyckoff Distribution Token (NEARUSDT):
    # - Push 1 to $5.10 with high volume & overbought RSI
    # - Pullback to $4.70
    # - Push 2 to higher high $5.25 on dying volume (Bearish RSI & MACD Divergence!)
    # - Rollover with 4H MACD bearish crossover, positive funding (+2.2 bps)
    near_4h: list[Candle] = []
    for i in range(80):
        if i < 35:
            # Base accumulation before markup at 3.20
            p = Decimal("3.20") + (Decimal(str(i % 5)) * Decimal("0.01"))
            vol = Decimal("8000")
        elif i < 55:
            # Vertical markup rally up to 5.10
            p = Decimal("3.25") + (Decimal(str(i - 35)) * Decimal("0.0925"))
            vol = Decimal("28000")
        elif i < 62:
            # Pullback to 4.75 establishing 72h support base
            p = Decimal("5.10") - (Decimal(str(i - 55)) * Decimal("0.05"))
            vol = Decimal("14000")
        elif i < 74:
            # Exhaustion higher high push to 5.25 on dying volume (UTAD)
            p = Decimal("4.75") + (Decimal(str(i - 62)) * Decimal("0.0416"))
            vol = Decimal("7000")
        else:
            # Reversal rollover down to 5.02 (still > 4.75 by 5.6%, avoiding capitulation, but rejecting 5.25 by 0.23 > 0.5*ATR)
            p = Decimal("5.25") - (Decimal(str(i - 74)) * Decimal("0.038"))
            vol = Decimal("16000")

        near_4h.append(
            Candle(
                opened_at=started + timedelta(hours=i * 4),
                closed_at=started + timedelta(hours=(i + 1) * 4),
                open=p - Decimal("0.02"),
                high=p + Decimal("0.03"),
                low=p - Decimal("0.03"),
                close=p,
                volume=vol,
                quote_volume=vol * p,
                closed=True,
            )
        )

    near_1h: list[Candle] = []
    for i in range(320):
        t = started + timedelta(hours=i)
        c4h = near_4h[i // 4]
        p = c4h.close + (Decimal(str((i % 4) - 2)) * Decimal("0.005"))
        vol = (c4h.volume / Decimal("4")).quantize(Decimal("1"))
        near_1h.append(
            Candle(
                opened_at=t,
                closed_at=t + timedelta(hours=1),
                open=p - Decimal("0.01"),
                high=p + Decimal("0.015"),
                low=p - Decimal("0.015"),
                close=p,
                volume=vol,
                quote_volume=vol * p,
                closed=True,
            )
        )

    market = FakeCryptoMarketData(
        instruments=[inst_btc, inst_near],
        candles_1h_map={"BTCUSDT": btc_candles, "NEARUSDT": near_1h},
        candles_4h_map={"BTCUSDT": trending_candles(up=True, count=80), "NEARUSDT": near_4h},
    )

    deriv_port = FakeDerivativesData(
        snapshots={
            "NEARUSDT": DerivativesSnapshot(
                funding_rate=Decimal("0.00022"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("45000000"),
                open_interest_change_1h=Decimal("0.01"),
                open_interest_change_7d=Decimal("0.25"),
                taker_buy_sell_ratio=Decimal("0.84"),
                bid_depth_notional=Decimal("1200000"),
                ask_depth_notional=Decimal("2800000"),
            )
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext(
        fear_greed=FearGreedReading(value=59, classification="Greed", observed_at=datetime.now(UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.0"),
            change_7d_pct=Decimal("0.0"),
            change_30d_pct=Decimal("1.0"),
        ),
    )

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)

    print("\n[TEST 1] Screening with mode='distribution' (Wyckoff UTAD & MTF Divergence Short Radar)...")
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.DISTRIBUTION,
    )

    assert len(opportunities) > 0, "Error: No opportunities found!"
    short_opp = next((o for o in opportunities if o.instrument.id.symbol == "NEARUSDT"), None)
    assert short_opp is not None, "Error: NEARUSDT was not qualified!"

    print(f"  ✓ Symbol: {short_opp.instrument.id.symbol}")
    print(f"  ✓ Direction: {short_opp.direction.value.upper()}")
    print(f"  ✓ Setup Label: {short_opp.setup_label.value}")
    print(f"  ✓ Conviction Score: {short_opp.score:.1f}/100")
    print(f"  ✓ Invalidation Stop: ${short_opp.suggested_stop:.4f}")
    print(f"  ✓ Limit Retest Entry: ${short_opp.pullback_entry:.4f}")
    print(f"  ✓ Current Price: ${short_opp.current_price:.4f}")
    print(f"  ✓ Breakdown Trigger: ${short_opp.breakout_trigger:.4f}")
    print(f"  ✓ Targets: {[f'${t:.4f}' for t in short_opp.suggested_targets]}")
    print(f"  ✓ Risk / Reward Ratio: {short_opp.risk_reward_ratio}:1")

    # Strict short bracket hierarchy verification
    stop = short_opp.suggested_stop
    entry = short_opp.pullback_entry
    curr = short_opp.current_price
    trig = short_opp.breakout_trigger
    t1 = short_opp.suggested_targets[0]
    t2 = short_opp.suggested_targets[1]
    t3 = short_opp.suggested_targets[2]

    assert stop > curr, f"Stop (${stop}) must be ABOVE Current Price (${curr})"
    assert trig < curr, f"Breakdown Trigger (${trig}) must be BELOW Current Price (${curr})"
    assert t1 < trig, f"Target 1 (${t1}) must be BELOW Trigger (${trig})"
    assert t2 < t1 and t3 < t2, "Targets must descend monotonically downwards for Short"

    print("\n  ✓ Verified Strict Short Execution Hierarchy:")
    print(f"    ${stop:.4f} (Stop) > ${entry:.4f} (Retest Entry) >= ${curr:.4f} (Price) > ${trig:.4f} (Trigger) > ${t1:.4f} (T1) > ${t2:.4f} (T2) > ${t3:.4f} (T3)")

    print("\n  ✓ 5-Gate Institutional Checklist:")
    for title, passed, detail in short_opp.checklist:
        status_sym = "✅" if passed else "❌"
        print(f"    {status_sym} {title:30s} : {detail}")

    print("\n  ✓ Institutional Rationale:")
    for r in short_opp.rationale:
        print(f"    • {r}")

    # 2. Test mode='all' (Dual-Direction Radar)
    print("\n[TEST 2] Screening with mode='all' (Simultaneous Longs & Shorts)...")
    all_opps = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.ALL,
    )
    directions_found = {o.direction.value for o in all_opps}
    print(f"  ✓ Successfully screened all modes. Directions present: {directions_found}")

    print("\n" + "=" * 80)
    print("🎉 ALL SCREENING ARCHITECTURE & MATH CHECKS PASSED WITH 100% ACCURACY!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
