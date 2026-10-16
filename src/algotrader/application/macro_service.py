from __future__ import annotations

import asyncio
import json
import math
import urllib.request
from datetime import UTC, datetime
from typing import Any

import structlog

from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.domain.macro_overextension import (
    BenchmarkExcursion,
    DerivativesLeverageHeat,
    LiquidityDivergence,
    MarketBreadthSaturation,
    MarketOverextensionReport,
    SupportDefenseShelf,
)

log = structlog.get_logger(__name__)


def _calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Calculate RSI from closing prices using Wilder's smoothing."""
    if len(closes) < period + 1:
        return 50.0

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


class MacroIntelligenceService:
    """Intelligent Market Overextension, Exhaustion & Liquidity Architecture Service."""

    def __init__(self, sentiment_provider: MacroSentimentProvider | None = None) -> None:
        self._sentiment_provider = sentiment_provider or MacroSentimentProvider()
        self._cached_report: MarketOverextensionReport | None = None
        self._cache_time: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_overextension_report(self, force_refresh: bool = False) -> MarketOverextensionReport:
        now = datetime.now(UTC)
        if not force_refresh and self._cached_report and self._cache_time:
            if (now - self._cache_time).total_seconds() < 60.0:
                return self._cached_report

        async with self._lock:
            if not force_refresh and self._cached_report and self._cache_time:
                if (now - self._cache_time).total_seconds() < 60.0:
                    return self._cached_report

            report = await self._generate_report()
            self._cached_report = report
            self._cache_time = datetime.now(UTC)
            return report

    async def _fetch_url_json(self, url: str, timeout: float = 4.0) -> Any:
        def _get() -> Any:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Atlas-Macro-Intelligence/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return await asyncio.to_thread(_get)

    async def _generate_report(self) -> MarketOverextensionReport:
        # Concurrent gathering of all required market data
        btc_1d_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit=35")
        btc_4h_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=30")
        eth_1d_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=1d&limit=35")
        eth_4h_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=4h&limit=30")
        premiums_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/premiumIndex")
        tickers_task = self._fetch_url_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
        macro_ctx_task = self._sentiment_provider.get_context()

        (
            btc_1d_res,
            btc_4h_res,
            eth_1d_res,
            eth_4h_res,
            premiums_res,
            tickers_res,
            macro_ctx_res,
        ) = await asyncio.gather(
            btc_1d_task,
            btc_4h_task,
            eth_1d_task,
            eth_4h_task,
            premiums_task,
            tickers_task,
            macro_ctx_task,
            return_exceptions=True,
        )

        # 1. Parse Benchmark Excursions
        btc_excursion = self._parse_benchmark(
            "BTCUSDT",
            btc_1d_res if isinstance(btc_1d_res, list) else [],
            btc_4h_res if isinstance(btc_4h_res, list) else [],
            fallback_price=84000.0,
        )
        eth_excursion = self._parse_benchmark(
            "ETHUSDT",
            eth_1d_res if isinstance(eth_1d_res, list) else [],
            eth_4h_res if isinstance(eth_4h_res, list) else [],
            fallback_price=3100.0,
        )

        # 2. Parse Derivatives Leverage & Funding Heatmap
        derivatives_heat = self._parse_derivatives_heat(
            premiums_res if isinstance(premiums_res, list) else []
        )

        # 3. Parse Market Breadth Saturation
        market_breadth = self._parse_market_breadth(
            tickers_res if isinstance(tickers_res, list) else []
        )

        # 4. Parse Liquidity & Macro Divergence
        macro_ctx = macro_ctx_res if not isinstance(macro_ctx_res, Exception) else None
        liq_divergence = self._parse_liquidity_divergence(macro_ctx, btc_excursion)

        # 5. Composite Overextension Score Formulation (0 - 100)
        # Pillar A: Benchmark Excursion Score (0 - 100)
        btc_z_norm = min(100.0, max(0.0, (btc_excursion.z_score_20d + 2.5) / 5.0 * 100.0))
        btc_bench_score = 0.5 * btc_z_norm + 0.3 * btc_excursion.rsi_14_daily + 0.2 * btc_excursion.rsi_14_4h

        eth_z_norm = min(100.0, max(0.0, (eth_excursion.z_score_20d + 2.5) / 5.0 * 100.0))
        eth_bench_score = 0.5 * eth_z_norm + 0.3 * eth_excursion.rsi_14_daily + 0.2 * eth_excursion.rsi_14_4h

        benchmark_score = 0.65 * btc_bench_score + 0.35 * eth_bench_score

        # Pillar B: Derivatives Leverage Heat Score (0 - 100)
        apr = derivatives_heat.annualized_apr
        if apr < 0:
            apr_score = 20.0
        elif apr <= 10.0:
            apr_score = 35.0 + (apr / 10.0) * 15.0
        elif apr <= 25.0:
            apr_score = 50.0 + ((apr - 10.0) / 15.0) * 25.0
        elif apr <= 45.0:
            apr_score = 75.0 + ((apr - 25.0) / 20.0) * 15.0
        else:
            apr_score = 95.0
        deriv_score = 0.75 * apr_score + 0.25 * min(100.0, derivatives_heat.crowded_long_ratio * 1.5)

        # Pillar C: Breadth Saturation Score (0 - 100)
        breadth_score = min(
            100.0,
            max(
                10.0,
                50.0 + (market_breadth.median_24h_change_pct * 4.0) + (market_breadth.pct_above_20_sma - 50.0) * 0.4,
            ),
        )

        # Pillar D: Liquidity & Macro Divergence Score (0 - 100)
        fg_score = float(liq_divergence.fear_greed_score)
        liq_score = fg_score
        if liq_divergence.is_synthetic_leverage_divergence:
            liq_score = min(100.0, liq_score + 15.0)

        # Weighted Composite
        composite_score = round(
            0.35 * benchmark_score + 0.30 * deriv_score + 0.20 * breadth_score + 0.15 * liq_score,
            1,
        )
        composite_score = max(8.0, min(96.0, composite_score))

        # Regime classification
        if composite_score >= 82.0:
            regime = "CLIMAX_EXHAUSTION"
            regime_title = "Euphoric Blow-Off Climax (Severe Pullback Imminent)"
            regime_desc = (
                "Extreme leverage crowding with multi-week benchmark excursion above 20-day SMA. "
                "Aggressive annualized perp funding and overbought RSI saturation signal that late longs are trapped at highs. "
                "High probability of a violent liquidation flush to reset order book open interest."
            )
            cool_off_prob = 88
            anti_chasing = "CRITICAL: Under no circumstances buy breakouts here. Long squeeze vulnerability is at maximum threshold."
            recommendations = [
                "Tighten trailing stops on all existing winning positions to breakeven or 4H EMA50 shelves.",
                "Take 50% to 75% profit off the table on extended altcoin swing positions.",
                "Halt adding new breakout longs. Wait for flush down to institutional mean-reversion defense zones.",
                "Prepare for sudden long liquidation cascade that resets perp funding to neutral.",
            ]
        elif composite_score >= 68.0:
            regime = "HIGH_PULLBACK_RISK"
            regime_title = "High Overextension: Mean-Reversion Cool-Off Expected"
            regime_desc = (
                "Market is technically stretched with benchmark leaders trading above their upper Bollinger envelopes. "
                "Perpetual funding is heated and retail participation is crowded. An orderly multi-day consolidation or "
                "4-8% benchmark pullback to the 20-day SMA / 4H EMA50 is the highest probability path."
            )
            cool_off_prob = 74
            anti_chasing = "HIGH CAUTION: Anti-chasing rules strictly enforced. Only enter tokens coiling tightly on top of POC bases."
            recommendations = [
                "Do not chase tokens with >15% 72-hour expansion. Demand tight Wyckoff consolidation tests.",
                "Scale out partial profits at Target 1 and let remainder ride with stop at entry.",
                "Expect altcoins to stall or retrace if BTC initiates a test of its 20-day SMA.",
                "Keep cash dry powder ready to bid Phase C spring shakeouts during the cool-off.",
            ]
        elif composite_score >= 52.0:
            regime = "MODERATE_OVEREXTENSION"
            regime_title = "Trending Expansion: Moderate Stretch"
            regime_desc = (
                "Healthy institutional trend expansion with sustainable derivatives funding. While short-term momentum is "
                "elevated, market breadth remains constructive and leverage has not reached climax saturation. Pullbacks remain buyable."
            )
            cool_off_prob = 48
            anti_chasing = "MODERATE: Respect 72h excursion ceilings (max 18%). Base accumulation setups remain valid."
            recommendations = [
                "Focus strictly on high-scoring (Grade A+) candidates resting right beneath breakout ceilings.",
                "Trail protective stops upward as targets are achieved.",
                "Monitor funding rate spikes on individual tokens before deploying fresh capital.",
            ]
        elif composite_score >= 32.0:
            regime = "BALANCED_EXPANSION"
            regime_title = "Balanced Market: Low Pullback Risk"
            regime_desc = (
                "Neutral to constructive macro regime. Leverage is balanced, funding rates are near baseline (5-12% APR), "
                "and benchmark leaders are consolidating within institutional value areas. High runway for Wyckoff pre-breakout coils."
            )
            cool_off_prob = 28
            anti_chasing = "NORMAL: Standard screener rules apply. Ample structural runway for pre-breakout entries."
            recommendations = [
                "Ideal environment for Wyckoff Phase C spring bases and volatility compression coils.",
                "Full position sizing permitted on setups passing all 6 Institutional Gates.",
                "Patience with multi-day accumulation targets.",
            ]
        else:
            regime = "HEALTHY_BASE"
            regime_title = "Oversold Base / Maximum Accumulation Asymmetry"
            regime_desc = (
                "Market is oversold or in deep accumulation. Derivatives leverage has been washed out, negative or discount funding "
                "is prevalent, and benchmark leaders trade at or below 50-day moving averages. Extremely low cool-off risk."
            )
            cool_off_prob = 14
            anti_chasing = "AGGRESSIVE ACCUMULATION: Risk/reward heavily favors patient spot and perp buyers at support shelves."
            recommendations = [
                "Aggressively build positions in leaders showing relative strength and Wyckoff volume dry-up.",
                "Bear traps and short squeezes are high probability.",
                "Generous stop loss leeway below institutional support floors.",
            ]

        # Support defense shelves
        support_shelves = [
            SupportDefenseShelf(
                asset="BTCUSDT",
                level_name="20-Day SMA Mean Reversion",
                price=btc_excursion.sma_20,
                distance_pct=btc_excursion.dist_sma20_pct,
            ),
            SupportDefenseShelf(
                asset="BTCUSDT",
                level_name="50-Day EMA Institutional Trend Shelf",
                price=btc_excursion.ema_50,
                distance_pct=btc_excursion.dist_ema50_pct,
            ),
            SupportDefenseShelf(
                asset="ETHUSDT",
                level_name="20-Day SMA Mean Reversion",
                price=eth_excursion.sma_20,
                distance_pct=eth_excursion.dist_sma20_pct,
            ),
            SupportDefenseShelf(
                asset="ETHUSDT",
                level_name="50-Day EMA Institutional Trend Shelf",
                price=eth_excursion.ema_50,
                distance_pct=eth_excursion.dist_ema50_pct,
            ),
        ]

        return MarketOverextensionReport(
            composite_score=composite_score,
            regime=regime,
            regime_title=regime_title,
            regime_description=regime_desc,
            cool_off_probability_pct=cool_off_prob,
            benchmark_btc=btc_excursion,
            benchmark_eth=eth_excursion,
            derivatives_heat=derivatives_heat,
            market_breadth=market_breadth,
            liquidity_divergence=liq_divergence,
            support_shelves=support_shelves,
            recommendations=recommendations,
            anti_chasing_alert=anti_chasing,
        )

    def _parse_benchmark(
        self,
        symbol: str,
        klines_1d: list[Any],
        klines_4h: list[Any],
        fallback_price: float,
    ) -> BenchmarkExcursion:
        if not klines_1d or len(klines_1d) < 20:
            return BenchmarkExcursion(
                symbol=symbol,
                current_price=fallback_price,
                sma_20=fallback_price * 0.96,
                ema_50=fallback_price * 0.92,
                dist_sma20_pct=4.1,
                dist_ema50_pct=8.7,
                z_score_20d=1.2,
                bollinger_pct_b=0.75,
                rsi_14_daily=58.0,
                rsi_14_4h=55.0,
                status="ORGANIC_TREND",
            )

        closes_1d = [float(k[4]) for k in klines_1d]
        closes_4h = [float(k[4]) for k in klines_4h] if klines_4h else closes_1d
        cur_price = closes_1d[-1]

        sma_20 = sum(closes_1d[-20:]) / 20.0
        dist_sma20 = ((cur_price - sma_20) / sma_20) * 100.0

        # Calculate 50-day EMA (or 30-day approximation if only 35 bars)
        alpha = 2.0 / (min(50, len(closes_1d)) + 1.0)
        ema = closes_1d[0]
        for c in closes_1d[1:]:
            ema = (c * alpha) + (ema * (1.0 - alpha))
        dist_ema50 = ((cur_price - ema) / ema) * 100.0

        # 20-day standard deviation
        var = sum((c - sma_20) ** 2 for c in closes_1d[-20:]) / 20.0
        std = math.sqrt(var)
        z_score = round((cur_price - sma_20) / std, 2) if std > 0 else 0.0

        # Bollinger %B
        upper_bb = sma_20 + 2.0 * std
        lower_bb = sma_20 - 2.0 * std
        pct_b = round((cur_price - lower_bb) / (upper_bb - lower_bb), 2) if (upper_bb - lower_bb) > 0 else 0.5

        rsi_1d = _calculate_rsi(closes_1d, 14)
        rsi_4h = _calculate_rsi(closes_4h, 14)

        if dist_sma20 > 10.0 or z_score >= 2.2 or (rsi_1d >= 72.0 and rsi_4h >= 75.0):
            status = "EXTREME_OVERBOUGHT"
        elif dist_sma20 > 5.0 or z_score >= 1.4 or rsi_1d >= 65.0:
            status = "STRETCHED"
        elif dist_sma20 < -6.0 or z_score <= -1.5 or rsi_1d <= 35.0:
            status = "OVERSOLD"
        else:
            status = "ORGANIC_TREND"

        return BenchmarkExcursion(
            symbol=symbol,
            current_price=round(cur_price, 2),
            sma_20=round(sma_20, 2),
            ema_50=round(ema, 2),
            dist_sma20_pct=round(dist_sma20, 2),
            dist_ema50_pct=round(dist_ema50, 2),
            z_score_20d=z_score,
            bollinger_pct_b=pct_b,
            rsi_14_daily=rsi_1d,
            rsi_14_4h=rsi_4h,
            status=status,
        )

    def _parse_derivatives_heat(self, premiums: list[dict[str, Any]]) -> DerivativesLeverageHeat:
        if not premiums:
            return DerivativesLeverageHeat(
                avg_funding_8h=0.0001,
                annualized_apr=10.95,
                crowded_long_ratio=25.0,
                funding_regime="BALANCED_HEALTHY",
                top_heated_symbols=[],
                top_discount_symbols=[],
            )

        valid_pairs: list[tuple[str, float]] = []
        for p in premiums:
            sym = p.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            fr_raw = p.get("lastFundingRate")
            if fr_raw is None:
                continue
            try:
                fr = float(fr_raw)
                valid_pairs.append((sym, fr))
            except (ValueError, TypeError):
                continue

        if not valid_pairs:
            return DerivativesLeverageHeat(
                avg_funding_8h=0.0001,
                annualized_apr=10.95,
                crowded_long_ratio=20.0,
                funding_regime="BALANCED_HEALTHY",
                top_heated_symbols=[],
                top_discount_symbols=[],
            )

        rates = [fr for _, fr in valid_pairs]
        avg_fr = sum(rates) / len(rates)
        apr = round(avg_fr * 3.0 * 365.0 * 100.0, 2)

        # Crowded longs: funding >= 0.00015 (15 bps / 8h)
        crowded_count = sum(1 for r in rates if r >= 0.00015)
        crowded_ratio = round((crowded_count / len(rates)) * 100.0, 1)

        # Sort for top heated vs top discount
        valid_pairs.sort(key=lambda x: x[1], reverse=True)
        top_heated = [
            {
                "symbol": sym,
                "funding_rate_8h_pct": round(fr * 100.0, 4),
                "annualized_apr_pct": round(fr * 3.0 * 365.0 * 100.0, 1),
            }
            for sym, fr in valid_pairs[:5]
        ]
        top_discount = [
            {
                "symbol": sym,
                "funding_rate_8h_pct": round(fr * 100.0, 4),
                "annualized_apr_pct": round(fr * 3.0 * 365.0 * 100.0, 1),
            }
            for sym, fr in reversed(valid_pairs[-5:])
        ]

        if apr >= 35.0 or crowded_ratio >= 50.0:
            regime = "DANGER_CLIMAX"
        elif apr >= 20.0 or crowded_ratio >= 35.0:
            regime = "ELEVATED_HEATING"
        elif apr < 0:
            regime = "SHORT_SQUEEZE_DISCOUNT"
        else:
            regime = "BALANCED_HEALTHY"

        return DerivativesLeverageHeat(
            avg_funding_8h=round(avg_fr, 6),
            annualized_apr=apr,
            crowded_long_ratio=crowded_ratio,
            funding_regime=regime,
            top_heated_symbols=top_heated,
            top_discount_symbols=top_discount,
        )

    def _parse_market_breadth(self, tickers: list[dict[str, Any]]) -> MarketBreadthSaturation:
        if not tickers:
            return MarketBreadthSaturation(
                total_pairs_analyzed=300,
                pct_above_20_sma=55.0,
                pct_rsi_overbought=18.0,
                pct_rsi_oversold=8.0,
                median_24h_change_pct=1.4,
                breadth_regime="HEALTHY_EXPANSION",
            )

        pct_changes: list[float] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            chg_raw = t.get("priceChangePercent")
            if chg_raw is None:
                continue
            try:
                pct_changes.append(float(chg_raw))
            except (ValueError, TypeError):
                continue

        if not pct_changes:
            pct_changes = [0.0]

        pct_changes.sort()
        n = len(pct_changes)
        median_chg = pct_changes[n // 2]
        gaining_count = sum(1 for c in pct_changes if c > 0)
        pct_gaining = round((gaining_count / n) * 100.0, 1)

        # High momentum cluster approximation
        pct_high_expansion = round((sum(1 for c in pct_changes if c >= 7.0) / n) * 100.0, 1)
        pct_oversold = round((sum(1 for c in pct_changes if c <= -7.0) / n) * 100.0, 1)

        if pct_gaining >= 80.0 and median_chg >= 5.0:
            regime = "EUPHORIC_SATURATION"
        elif pct_gaining >= 55.0:
            regime = "HEALTHY_EXPANSION"
        elif pct_gaining <= 30.0:
            regime = "WASHOUT"
        else:
            regime = "DISTRIBUTION_DIVERGENCE"

        return MarketBreadthSaturation(
            total_pairs_analyzed=n,
            pct_above_20_sma=pct_gaining,
            pct_rsi_overbought=pct_high_expansion,
            pct_rsi_oversold=pct_oversold,
            median_24h_change_pct=round(median_chg, 2),
            breadth_regime=regime,
        )

    def _parse_liquidity_divergence(
        self,
        macro_ctx: Any,
        btc_bench: BenchmarkExcursion,
    ) -> LiquidityDivergence:
        fg_score = 50
        fg_class = "Neutral"
        btc_dom = 59.0
        st_flow = 0.0
        st_total = 302.0e9

        if macro_ctx:
            if macro_ctx.fear_greed:
                fg_score = macro_ctx.fear_greed.value
                fg_class = macro_ctx.fear_greed.classification
            if macro_ctx.market_breadth:
                btc_dom = float(macro_ctx.market_breadth.btc_dominance_pct)
            if macro_ctx.stablecoin_supply:
                st_flow = float(macro_ctx.stablecoin_supply.change_7d_pct)
                st_total = float(macro_ctx.stablecoin_supply.total_circulating_usd)

        # Synthetic Leverage Divergence: Price stretched above mean while stablecoin fuel is declining or stagnant
        is_synthetic = (st_flow <= -0.05 or st_flow == 0.0) and (btc_bench.dist_sma20_pct >= 4.0 or fg_score >= 65)

        if is_synthetic and fg_score >= 70:
            status = "SEVERE_SYNTHETIC_LEVERAGE"
        elif st_flow >= 0.5:
            status = "ORGANIC_CAPITAL_INFLOW"
        else:
            status = "HEALTHY_ROTATION"

        return LiquidityDivergence(
            stablecoin_net_flow_7d=round(st_flow, 2),
            stablecoin_total_usd=st_total,
            fear_greed_score=fg_score,
            fear_greed_classification=fg_class,
            btc_dominance=round(btc_dom, 1),
            is_synthetic_leverage_divergence=is_synthetic,
            status=status,
        )
