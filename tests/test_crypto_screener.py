from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from algotrader.adapters.external.models import (
    FearGreedReading,
    GlobalMarketMetrics,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import (
    CryptoMarketRegime,
    ScreenerMode,
    SetupLabel,
)
from algotrader.domain.indicators import IndicatorSnapshot
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    MarketKind,
    Quote,
    Venue,
)
from tests.conftest import make_instrument, trending_candles


def _make_snapshot(
    *,
    close: Decimal = Decimal("100"),
    ema9: Decimal = Decimal("102"),
    ema20: Decimal = Decimal("100"),
    ema50: Decimal = Decimal("95"),
    ema200: Decimal = Decimal("85"),
    adx14: Decimal = Decimal("28"),
    plus_di: Decimal = Decimal("30"),
    minus_di: Decimal = Decimal("15"),
    choppiness: Decimal = Decimal("40"),
    rsi14: Decimal = Decimal("60"),
    momentum_normalized: Decimal = Decimal("1.2"),
) -> IndicatorSnapshot:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    return IndicatorSnapshot(
        closed_at=now,
        momentum_reference_closed_at=now,
        close=close,
        momentum_reference_close=close - Decimal("2"),
        ema9=ema9,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi14=rsi14,
        momentum_reference_rsi14=rsi14 - Decimal("2"),
        atr14=Decimal("2.0"),
        adx14=adx14,
        momentum_reference_adx14=adx14 - Decimal("1"),
        plus_di=plus_di,
        minus_di=minus_di,
        macd_line=Decimal("1.5"),
        macd_signal_line=Decimal("1.0"),
        macd_hist=Decimal("0.5"),
        prev_macd_line=Decimal("1.2"),
        prev_macd_signal_line=Decimal("0.9"),
        momentum_reference_macd_hist=Decimal("0.3"),
        relative_volume=Decimal("1.5"),
        choppiness=choppiness,
        momentum_normalized=momentum_normalized,
        is_bullish_candle=True,
    )


class FakeCryptoMarketData:
    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        instruments: list[Instrument],
        candles_1h_map: dict[str, list[Candle]],
        candles_4h_map: dict[str, list[Candle]],
    ) -> None:
        self._instruments = instruments
        self._candles_1h = candles_1h_map
        self._candles_4h = candles_4h_map

    async def instruments(self) -> Sequence[Instrument]:
        return self._instruments

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        return [
            Quote(
                instrument_id=inst.id,
                bid=Decimal("99.95"),
                ask=Decimal("100.05"),
                last=Decimal("100.00"),
                quote_volume_24h=Decimal("15000000"),
            )
            for inst in instruments
        ]

    async def candles(
        self, instrument: Instrument, interval: str, limit: int
    ) -> Sequence[Candle]:
        symbol = instrument.id.symbol
        if interval == "1h":
            return self._candles_1h.get(symbol, [])[-limit:]
        return self._candles_4h.get(symbol, [])[-limit:]


class FakeDerivativesData:
    def __init__(self, snapshots: dict[str, DerivativesSnapshot]) -> None:
        self._snapshots = snapshots

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        symbol = instrument.id.symbol
        if symbol in self._snapshots:
            return self._snapshots[symbol]
        return DerivativesSnapshot(
            funding_rate=Decimal("0.0001"),
            basis_rate=Decimal("0.0005"),
            open_interest=Decimal("50000000"),
            open_interest_change_1h=Decimal("0.01"),
            taker_buy_sell_ratio=Decimal("1.05"),
            bid_depth_notional=Decimal("1000000"),
            ask_depth_notional=Decimal("900000"),
        )


def test_crypto_market_regime_classification(settings: Settings) -> None:
    screener = CryptoOpportunityScreener(settings)

    # 1. Strong Trend Up
    strong_up = _make_snapshot(
        close=Decimal("105"),
        ema9=Decimal("104"),
        ema20=Decimal("100"),
        ema50=Decimal("90"),
        ema200=Decimal("80"),
        adx14=Decimal("30"),
        plus_di=Decimal("35"),
        minus_di=Decimal("12"),
        choppiness=Decimal("38"),
    )
    regime_up = screener._classify_regime(strong_up, ema20_4h=Decimal("100"), ema50_4h=Decimal("90"))
    assert regime_up is CryptoMarketRegime.STRONG_TREND_UP

    # 2. Strong Trend Down
    strong_down = _make_snapshot(
        close=Decimal("75"),
        ema9=Decimal("78"),
        ema20=Decimal("80"),
        ema50=Decimal("90"),
        ema200=Decimal("100"),
        adx14=Decimal("32"),
        plus_di=Decimal("10"),
        minus_di=Decimal("36"),
        choppiness=Decimal("35"),
    )
    regime_down = screener._classify_regime(strong_down, ema20_4h=Decimal("80"), ema50_4h=Decimal("90"))
    assert regime_down is CryptoMarketRegime.STRONG_TREND_DOWN

    # 3. Volatility Squeeze
    squeeze = _make_snapshot(
        choppiness=Decimal("65"),
        adx14=Decimal("16"),
    )
    regime_sq = screener._classify_regime(squeeze, ema20_4h=Decimal("100"), ema50_4h=Decimal("100"))
    assert regime_sq is CryptoMarketRegime.VOLATILITY_SQUEEZE

    # 4. Mean Reverting Range
    range_snap = _make_snapshot(
        adx14=Decimal("17"),
        choppiness=Decimal("56"),
    )
    regime_range = screener._classify_regime(range_snap, ema20_4h=Decimal("100"), ema50_4h=Decimal("99"))
    assert regime_range is CryptoMarketRegime.MEAN_REVERTING_RANGE


def test_squeeze_long_setup_identification(settings: Settings) -> None:
    screener = CryptoOpportunityScreener(settings)
    inst = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="SOLUSDT")
    quote = Quote(
        instrument_id=inst.id,
        bid=Decimal("99.95"),
        ask=Decimal("100.05"),
        last=Decimal("100.00"),
        quote_volume_24h=Decimal("50000000"),
    )
    snap = _make_snapshot(close=Decimal("100"), ema20=Decimal("98"))
    deriv = DerivativesSnapshot(
        funding_rate=Decimal("-0.0003"),  # Heavy negative funding (-3 bps / 8h)
        basis_rate=Decimal("-0.0001"),
        open_interest=Decimal("80000000"),
        open_interest_change_1h=Decimal("0.04"),  # +4% OI expansion in 1h
        taker_buy_sell_ratio=Decimal("1.25"),  # Aggressive taker buyers
        bid_depth_notional=Decimal("2000000"),
        ask_depth_notional=Decimal("1000000"),
    )
    macro = MacroSentimentContext(
        fear_greed=FearGreedReading(value=50, classification="Neutral", observed_at=datetime.now(UTC)),
    )

    opp = screener._score_setup(
        direction=Direction.LONG,
        inst=inst,
        quote=quote,
        snap_1h=snap,
        ema20_4h=Decimal("99"),
        ema50_4h=Decimal("95"),
        rsi_4h=Decimal("58"),
        regime=CryptoMarketRegime.EMERGING_TREND_UP,
        support=Decimal("96"),
        resistance=Decimal("110"),
        struct_trend="higher_high",
        beta=Decimal("1.1"),
        residual_24h=Decimal("0.025"),
        residual_6h=Decimal("0.01"),
        residual_7d=Decimal("0.05"),
        derivatives=deriv,
        macro_context=macro,
        closed_1h=trending_candles(count=260),
    )

    assert opp is not None
    assert opp.setup_label is SetupLabel.SQUEEZE_LONG
    assert opp.direction is Direction.LONG
    assert opp.score >= Decimal("70")
    assert opp.score_breakdown.derivatives_score >= Decimal("80")
    assert any("Shorts paying longs" in reason for reason in opp.rationale)
    assert any("OI expanding with price" in reason for reason in opp.rationale)


def test_residual_alpha_decomposition(settings: Settings) -> None:
    screener = CryptoOpportunityScreener(settings)
    btc_candles = trending_candles(up=True, count=220)
    # Asset is surging faster than BTC
    asset_candles = [
        replace(c, close=c.close * Decimal("1.20"), open=c.open * Decimal("1.20"))
        for c in btc_candles
    ]

    beta, corr, res_24h, res_6h, res_7d = screener._calc_residual_alpha(asset_candles, btc_candles)
    assert beta > Decimal("0")
    assert corr is not None


@pytest.mark.asyncio
async def test_full_crypto_screener_execution(settings: Settings) -> None:
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_eth = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="ETHUSDT")
    inst_sol = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="SOLUSDT")

    btc_candles = trending_candles(up=True, count=260)
    eth_candles = trending_candles(up=True, count=260)
    sol_candles = trending_candles(up=True, count=260)

    # 4h candles
    btc_4h = trending_candles(up=True, count=70)
    eth_4h = trending_candles(up=True, count=70)
    sol_4h = trending_candles(up=True, count=70)

    market = FakeCryptoMarketData(
        instruments=[inst_btc, inst_eth, inst_sol],
        candles_1h_map={"BTCUSDT": btc_candles, "ETHUSDT": eth_candles, "SOLUSDT": sol_candles},
        candles_4h_map={"BTCUSDT": btc_4h, "ETHUSDT": eth_4h, "SOLUSDT": sol_4h},
    )

    deriv_port = FakeDerivativesData(
        snapshots={
            "SOLUSDT": DerivativesSnapshot(
                funding_rate=Decimal("-0.0002"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("100000000"),
                open_interest_change_1h=Decimal("0.05"),
                taker_buy_sell_ratio=Decimal("1.30"),
                bid_depth_notional=Decimal("2000000"),
                ask_depth_notional=Decimal("1000000"),
            )
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext(
        fear_greed=FearGreedReading(value=55, classification="Neutral", observed_at=datetime.now(UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.1"),
            change_7d_pct=Decimal("0.8"),
            change_30d_pct=Decimal("2.0"),
        ),
    )

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.BREAKOUT,
    )

    assert len(opportunities) > 0
    # SOL should rank high due to squeeze setup and negative funding
    sol_opp = next((o for o in opportunities if o.instrument.id.symbol == "SOLUSDT"), None)
    assert sol_opp is not None
    assert sol_opp.setup_label is SetupLabel.SQUEEZE_LONG
    assert sol_opp.score > Decimal("60")
    assert sol_opp.suggested_stop < sol_opp.current_price
    assert len(sol_opp.suggested_targets) == 3
    assert sol_opp.suggested_targets[0] > sol_opp.current_price

    as_dict = sol_opp.as_dict()
    assert as_dict["symbol"] == "SOLUSDT"
    assert "score_breakdown" in as_dict


@pytest.mark.asyncio
async def test_accumulation_screener_anti_chasing_rejects_pumped_tokens(settings: Settings) -> None:
    """Ensure tokens that already had a huge run-up (like RLC +35% or STRK +45%) are rejected."""
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_rlc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="RLCUSDT")

    btc_candles = trending_candles(up=True, count=260)
    # RLC pumped hard over the last 24h
    rlc_candles = trending_candles(up=True, count=260)

    market = FakeCryptoMarketData(
        instruments=[inst_btc, inst_rlc],
        candles_1h_map={"BTCUSDT": btc_candles, "RLCUSDT": rlc_candles},
        candles_4h_map={"BTCUSDT": trending_candles(up=True, count=70), "RLCUSDT": trending_candles(up=True, count=70)},
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext(
        fear_greed=FearGreedReading(value=55, classification="Neutral", observed_at=datetime.now(UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.1"),
            change_7d_pct=Decimal("0.8"),
            change_30d_pct=Decimal("2.0"),
        ),
    )

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)
    # Default mode is ACCUMULATION
    opportunities = await screener.screen(market, min_score=Decimal("30"), mode=ScreenerMode.ACCUMULATION)

    # In accumulation mode, heavily trending / already pumped tokens must be rejected
    rlc_opp = next((o for o in opportunities if o.instrument.id.symbol == "RLCUSDT"), None)
    assert rlc_opp is None, "Pumped token should be rejected by anti-chasing circuit breaker"


@pytest.mark.asyncio
async def test_accumulation_screener_identifies_tight_coil(settings: Settings) -> None:
    """Ensure tokens in tight 30d range coiling right below ceiling with negative funding are detected."""
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_coil = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="COILUSDT")

    btc_candles = trending_candles(up=True, count=260)

    # Generate coiling candles: 30-day base between 9.5 and 10.3 (range height ~8%), current price 10.2 (2% from ceiling)
    started = datetime(2025, 1, 1, tzinfo=UTC)
    coil_1h: list[Candle] = []
    base_price = Decimal("10.00")
    for i in range(260):
        # Sine wave oscillating between -0.30 and +0.30, with last 24h sitting at +0.20
        import math
        mod = 0.20 if i >= 236 else 0.25 * math.sin(i * 0.2)
        p = base_price + Decimal(str(round(mod, 2)))
        coil_1h.append(
            Candle(
                opened_at=started + timedelta(hours=i),
                closed_at=started + timedelta(hours=i + 1),
                open=p,
                high=p + Decimal("0.08"),
                low=p - Decimal("0.08"),
                close=p + Decimal("0.02"),
                volume=Decimal("2500") if i == 259 else Decimal("1500"),
                quote_volume=Decimal("250000") if i == 259 else Decimal("150000"),
                closed=True,
            )
        )

    coil_4h: list[Candle] = []
    for i in range(70):
        p = base_price + Decimal("0.15")
        coil_4h.append(
            Candle(
                opened_at=started + timedelta(hours=i * 4),
                closed_at=started + timedelta(hours=(i + 1) * 4),
                open=p,
                high=p + Decimal("0.10"),
                low=p - Decimal("0.10"),
                close=p,
                volume=Decimal("6000"),
                quote_volume=Decimal("600000"),
                closed=True,
            )
        )

    market = FakeCryptoMarketData(
        instruments=[inst_btc, inst_coil],
        candles_1h_map={"BTCUSDT": btc_candles, "COILUSDT": coil_1h},
        candles_4h_map={"BTCUSDT": trending_candles(up=True, count=70), "COILUSDT": coil_4h},
    )

    # Negative funding = retail shorting the base, smart money accumulating
    deriv_port = FakeDerivativesData(
        snapshots={
            "COILUSDT": DerivativesSnapshot(
                funding_rate=Decimal("-0.00015"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("50000000"),
                open_interest_change_1h=Decimal("0.04"),
                open_interest_change_7d=Decimal("0.18"),
                taker_buy_sell_ratio=Decimal("1.25"),
                bid_depth_notional=Decimal("3000000"),
                ask_depth_notional=Decimal("1500000"),
            )
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext(
        fear_greed=FearGreedReading(value=55, classification="Neutral", observed_at=datetime.now(UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.1"),
            change_7d_pct=Decimal("0.8"),
            change_30d_pct=Decimal("2.0"),
        ),
    )

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.ACCUMULATION,
    )

    assert len(opportunities) > 0
    coil_opp = next((o for o in opportunities if o.instrument.id.symbol == "COILUSDT"), None)
    assert coil_opp is not None
    assert coil_opp.setup_label in (SetupLabel.COIL_SPRING_LONG, SetupLabel.OI_ACCUMULATION_LONG, SetupLabel.VCP_COMPRESSION_LONG)
    assert coil_opp.risk_reward_ratio >= Decimal("2.0")
    assert coil_opp.suggested_stop < coil_opp.current_price
    assert coil_opp.suggested_targets[0] > coil_opp.current_price
    assert coil_opp.poc_price is not None
    assert coil_opp.vah_price is not None
    assert coil_opp.val_price is not None
    assert coil_opp.adv_7d is not None
    assert coil_opp.volume_contraction_ratio is not None
    assert coil_opp.pullback_entry is not None
    assert coil_opp.breakout_trigger is not None
    assert coil_opp.breakout_trigger > coil_opp.current_price
    assert coil_opp.breakout_trigger < coil_opp.suggested_targets[0], "Breakout trigger must be below Target 1"
    assert coil_opp.suggested_entry <= coil_opp.current_price
    assert coil_opp.suggested_entry > coil_opp.suggested_stop
    assert coil_opp.suggested_targets[0] > coil_opp.suggested_entry

    from algotrader.application.chart_visualizer import generate_chart_html

    html = generate_chart_html([coil_opp], {"COILUSDT": coil_1h}, active_symbol="COILUSDT")
    assert "<!DOCTYPE html>" in html
    assert "lightweight-charts" in html
    assert "COILUSDT" in html
    assert "POC" in html
    assert "VAH" in html


@pytest.mark.asyncio
async def test_pre_breakdown_distribution_short_screening(settings: Settings) -> None:
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_dist = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="DISTUSDT")

    started = datetime(2026, 8, 1, tzinfo=UTC)
    btc_candles = trending_candles(up=True, count=360)

    dist_4h: list[Candle] = []
    dist_4h: list[Candle] = []
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
            # Brief pullback to 4.70
            p = Decimal("5.10") - (Decimal(str(i - 55)) * Decimal("0.0571"))
            vol = Decimal("14000")
        elif i < 72:
            # Exhaustion higher high push to 5.25 on dying volume (UTAD)
            p = Decimal("4.70") + (Decimal(str(i - 62)) * Decimal("0.055"))
            vol = Decimal("7000")
        else:
            # Reversal rollover breakdown down to 4.95
            p = Decimal("5.25") - (Decimal(str(i - 72)) * Decimal("0.0375"))
            vol = Decimal("16000")

        dist_4h.append(
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

    dist_1h: list[Candle] = []
    for i in range(320):
        t = started + timedelta(hours=i)
        c4h = dist_4h[i // 4]
        p = c4h.close + (Decimal(str((i % 4) - 2)) * Decimal("0.005"))
        vol = (c4h.volume / Decimal("4")).quantize(Decimal("1"))
        dist_1h.append(
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
        instruments=[inst_btc, inst_dist],
        candles_1h_map={"BTCUSDT": btc_candles, "DISTUSDT": dist_1h},
        candles_4h_map={"BTCUSDT": trending_candles(up=True, count=80), "DISTUSDT": dist_4h},
    )

    # Positive funding = trapped retail longs paying shorts
    deriv_port = FakeDerivativesData(
        snapshots={
            "DISTUSDT": DerivativesSnapshot(
                funding_rate=Decimal("0.00022"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("45000000"),
                open_interest_change_1h=Decimal("0.01"),
                open_interest_change_7d=Decimal("0.184"),
                taker_buy_sell_ratio=Decimal("0.84"),
                bid_depth_notional=Decimal("1200000"),
                ask_depth_notional=Decimal("2800000"),
            )
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext(
        fear_greed=FearGreedReading(value=50, classification="Neutral", observed_at=datetime.now(UTC)),
        stablecoin_supply=StablecoinSupplyMetric(
            total_circulating_usd=Decimal("160000000000"),
            change_1d_pct=Decimal("0.1"),
            change_7d_pct=Decimal("0.5"),
            change_30d_pct=Decimal("1.5"),
        ),
    )

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.DISTRIBUTION,
    )

    assert len(opportunities) > 0
    short_opp = next((o for o in opportunities if o.instrument.id.symbol == "DISTUSDT"), None)
    assert short_opp is not None
    assert short_opp.direction == Direction.SHORT
    assert short_opp.setup_label in (
        SetupLabel.UTAD_DIVERGENCE_SHORT,
        SetupLabel.COIL_BREAKDOWN_SHORT,
        SetupLabel.DISTRIBUTION_SHORT,
        SetupLabel.VCP_DISTRIBUTION_SHORT,
    )
    # Strict Short Price Hierarchy:
    # Target 3 < Target 2 < Target 1 < Breakdown Trigger < Current Price <= Pullback Entry < Stop Loss
    assert short_opp.suggested_stop > short_opp.current_price, "Stop must be above price for short"
    assert short_opp.breakout_trigger < short_opp.current_price, "Breakdown trigger must be below price for short"
    assert short_opp.suggested_targets[0] < short_opp.breakout_trigger, "Target 1 must be below breakdown trigger"
    assert short_opp.suggested_targets[1] < short_opp.suggested_targets[0], "Target 2 must be below Target 1"
    assert short_opp.suggested_targets[2] < short_opp.suggested_targets[1], "Target 3 must be below Target 2"
    assert short_opp.suggested_entry < short_opp.suggested_stop, "Entry must be below stop loss"
    assert short_opp.risk_reward_ratio >= Decimal("2.0")


@pytest.mark.asyncio
async def test_btc_velocity_cascade_guard(settings: Settings) -> None:
    """Ensure sudden BTC sharp drop flags a warning and penalizes long scores."""
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_coil = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="COILUSDT")

    started = datetime(2026, 8, 1, tzinfo=UTC)
    # BTC drops 3.5% on the most recent 1h candle
    btc_candles = list(trending_candles(up=True, count=360))
    last_c = btc_candles[-1]
    dumped_c = Candle(
        opened_at=last_c.opened_at,
        closed_at=last_c.closed_at,
        open=last_c.open,
        high=last_c.open,
        low=last_c.open * Decimal("0.965"),
        close=last_c.open * Decimal("0.965"),
        volume=last_c.volume * Decimal("3.0"),
        quote_volume=last_c.quote_volume * Decimal("3.0"),
        closed=True,
    )
    btc_candles[-1] = dumped_c

    base_price = Decimal("10.00")
    coil_1h: list[Candle] = []
    for i in range(360):
        import math
        mod = 0.20 if i >= 336 else 0.25 * math.sin(i * 0.2)
        p = base_price + Decimal(str(round(mod, 2)))
        coil_1h.append(
            Candle(
                opened_at=started + timedelta(hours=i),
                closed_at=started + timedelta(hours=i + 1),
                open=p,
                high=p + Decimal("0.08"),
                low=p - Decimal("0.08"),
                close=p + Decimal("0.02"),
                volume=Decimal("2500") if i == 359 else Decimal("1500"),
                quote_volume=Decimal("250000") if i == 359 else Decimal("150000"),
                closed=True,
            )
        )

    coil_4h: list[Candle] = []
    for i in range(70):
        p = base_price + Decimal("0.15")
        coil_4h.append(
            Candle(
                opened_at=started + timedelta(hours=i * 4),
                closed_at=started + timedelta(hours=(i + 1) * 4),
                open=p,
                high=p + Decimal("0.10"),
                low=p - Decimal("0.10"),
                close=p,
                volume=Decimal("6000"),
                quote_volume=Decimal("600000"),
                closed=True,
            )
        )

    market = FakeCryptoMarketData(
        instruments=[inst_btc, inst_coil],
        candles_1h_map={"BTCUSDT": btc_candles, "COILUSDT": coil_1h},
        candles_4h_map={"BTCUSDT": trending_candles(up=True, count=70), "COILUSDT": coil_4h},
    )

    deriv_port = FakeDerivativesData(
        snapshots={
            "COILUSDT": DerivativesSnapshot(
                funding_rate=Decimal("-0.00015"),
                basis_rate=Decimal("0"),
                open_interest=Decimal("50000000"),
                open_interest_change_1h=Decimal("0.04"),
                open_interest_change_7d=Decimal("0.18"),
                taker_buy_sell_ratio=Decimal("1.25"),
                bid_depth_notional=Decimal("3000000"),
                ask_depth_notional=Decimal("1500000"),
            )
        }
    )

    mock_macro = AsyncMock()
    mock_macro.get_context.return_value = MacroSentimentContext()

    screener = CryptoOpportunityScreener(settings, macro_provider=mock_macro)
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("20"),
        mode=ScreenerMode.ACCUMULATION,
    )

    coil_opp = next((o for o in opportunities if o.instrument.id.symbol == "COILUSDT"), None)
    assert coil_opp is not None
    assert any("BTC Velocity Cascade Active" in r for r in coil_opp.rationale)


@pytest.mark.asyncio
async def test_utad_mtf_rsi_divergence_short_screening(settings: Settings) -> None:
    """Test short screening on real Binance ticker NEARUSDT with MTF RSI overbought,

    bearish divergence, and MACD crossover.
    """
    inst_btc = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="BTCUSDT")
    inst_near = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="NEARUSDT")

    started = datetime(2026, 8, 1, tzinfo=UTC)
    btc_candles = trending_candles(up=True, count=360)

    # 4H candles for NEAR:
    # 1. Pushes to high $5.10 at bar 45 on high volume (RSI reaches 74)
    # 2. Pulls back to $4.70 at bar 55
    # 3. Pushes to higher high $5.25 at bar 70 on declining volume (RSI lower high: 64 -> Bearish Divergence!)
    # 4. Closes at $4.95 with 4H MACD crossing below signal line
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
            # Brief pullback to 4.70
            p = Decimal("5.10") - (Decimal(str(i - 55)) * Decimal("0.0571"))
            vol = Decimal("14000")
        elif i < 72:
            # Exhaustion higher high push to 5.25 on dying volume (UTAD)
            p = Decimal("4.70") + (Decimal(str(i - 62)) * Decimal("0.055"))
            vol = Decimal("7000")
        else:
            # Reversal rollover breakdown down to 4.95
            p = Decimal("5.25") - (Decimal(str(i - 72)) * Decimal("0.0375"))
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

    # 1H candles corresponding to NEAR
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
                funding_rate=Decimal("0.00022"),  # +2.2 bps trapped longs
                basis_rate=Decimal("0"),
                open_interest=Decimal("45000000"),
                open_interest_change_1h=Decimal("0.01"),
                open_interest_change_7d=Decimal("0.184"),
                taker_buy_sell_ratio=Decimal("0.84"),  # aggressive taker sellers
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
    opportunities = await screener.screen(
        market,
        derivatives_port=deriv_port,
        min_score=Decimal("40"),
        mode=ScreenerMode.DISTRIBUTION,
    )

    assert len(opportunities) > 0
    near_opp = next((o for o in opportunities if o.instrument.id.symbol == "NEARUSDT"), None)
    assert near_opp is not None
    assert near_opp.direction == Direction.SHORT
    assert near_opp.setup_label in (
        SetupLabel.UTAD_DIVERGENCE_SHORT,
        SetupLabel.EXHAUSTION_REVERSAL_SHORT,
        SetupLabel.DISTRIBUTION_SHORT,
        SetupLabel.COIL_BREAKDOWN_SHORT,
    )

    # Verify Checklist contains the 5 institutional gates
    titles = [c[0] for c in near_opp.checklist]
    assert "MTF RSI Overbought" in titles
    assert "Bearish Divergence" in titles
    assert "MACD Bearish Crossover" in titles
    assert "Volume & Momentum Exhaustion" in titles
    assert "Trapped Longs / Funding" in titles

    # Verify Strict Short Price Hierarchy
    assert near_opp.suggested_stop > near_opp.current_price
    assert near_opp.suggested_entry >= near_opp.current_price
    assert near_opp.breakout_trigger < near_opp.current_price
    assert near_opp.suggested_targets[0] < near_opp.breakout_trigger
    assert near_opp.suggested_targets[1] < near_opp.suggested_targets[0]
    assert near_opp.suggested_targets[2] < near_opp.suggested_targets[1]



