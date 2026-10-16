from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class BenchmarkExcursion:
    """Quantitative measurement of benchmark asset stretch away from institutional mean."""

    symbol: str
    current_price: float
    sma_20: float
    ema_50: float
    dist_sma20_pct: float
    dist_ema50_pct: float
    z_score_20d: float
    bollinger_pct_b: float
    rsi_14_daily: float
    rsi_14_4h: float
    status: str  # "EXTREME_OVERBOUGHT", "STRETCHED", "ORGANIC_TREND", "OVERSOLD"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "sma_20": self.sma_20,
            "ema_50": self.ema_50,
            "dist_sma20_pct": self.dist_sma20_pct,
            "dist_ema50_pct": self.dist_ema50_pct,
            "z_score_20d": self.z_score_20d,
            "bollinger_pct_b": self.bollinger_pct_b,
            "rsi_14_daily": self.rsi_14_daily,
            "rsi_14_4h": self.rsi_14_4h,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class DerivativesLeverageHeat:
    """Aggregated perpetual futures funding rates and leverage crowding across USD-M markets."""

    avg_funding_8h: float
    annualized_apr: float
    crowded_long_ratio: float  # % of symbols with funding > 15 bps / 8h
    funding_regime: str  # "DANGER_CLIMAX", "ELEVATED_HEATING", "BALANCED_HEALTHY", "SHORT_SQUEEZE_DISCOUNT"
    top_heated_symbols: list[dict[str, Any]]
    top_discount_symbols: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "avg_funding_8h": self.avg_funding_8h,
            "annualized_apr": self.annualized_apr,
            "crowded_long_ratio": self.crowded_long_ratio,
            "funding_regime": self.funding_regime,
            "top_heated_symbols": self.top_heated_symbols,
            "top_discount_symbols": self.top_discount_symbols,
        }


@dataclass(frozen=True, slots=True)
class MarketBreadthSaturation:
    """Universe-wide technical saturation and momentum distribution across USD-M perpetual contracts."""

    total_pairs_analyzed: int
    pct_above_20_sma: float
    pct_rsi_overbought: float
    pct_rsi_oversold: float
    median_24h_change_pct: float
    breadth_regime: str  # "EUPHORIC_SATURATION", "HEALTHY_EXPANSION", "DISTRIBUTION_DIVERGENCE", "WASHOUT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_pairs_analyzed": self.total_pairs_analyzed,
            "pct_above_20_sma": self.pct_above_20_sma,
            "pct_rsi_overbought": self.pct_rsi_overbought,
            "pct_rsi_oversold": self.pct_rsi_oversold,
            "median_24h_change_pct": self.median_24h_change_pct,
            "breadth_regime": self.breadth_regime,
        }


@dataclass(frozen=True, slots=True)
class LiquidityDivergence:
    """Macro fuel vs speculative leverage divergence."""

    stablecoin_net_flow_7d: float
    stablecoin_total_usd: float
    fear_greed_score: int
    fear_greed_classification: str
    btc_dominance: float
    is_synthetic_leverage_divergence: bool
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stablecoin_net_flow_7d": self.stablecoin_net_flow_7d,
            "stablecoin_total_usd": self.stablecoin_total_usd,
            "fear_greed_score": self.fear_greed_score,
            "fear_greed_classification": self.fear_greed_classification,
            "btc_dominance": self.btc_dominance,
            "is_synthetic_leverage_divergence": self.is_synthetic_leverage_divergence,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class SupportDefenseShelf:
    """Key institutional mean-reversion defense level."""

    asset: str
    level_name: str
    price: float
    distance_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "level_name": self.level_name,
            "price": self.price,
            "distance_pct": self.distance_pct,
        }


@dataclass(frozen=True, slots=True)
class MarketOverextensionReport:
    """Holistic market-wide overextension and cool-off risk intelligence report."""

    composite_score: float  # 0 to 100
    regime: str  # "HEALTHY_BASE", "BALANCED_EXPANSION", "MODERATE_OVEREXTENSION", "HIGH_PULLBACK_RISK", "CLIMAX_EXHAUSTION"
    regime_title: str
    regime_description: str
    cool_off_probability_pct: int
    benchmark_btc: BenchmarkExcursion
    benchmark_eth: BenchmarkExcursion
    derivatives_heat: DerivativesLeverageHeat
    market_breadth: MarketBreadthSaturation
    liquidity_divergence: LiquidityDivergence
    support_shelves: list[SupportDefenseShelf]
    recommendations: list[str]
    anti_chasing_alert: str
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "composite_score": self.composite_score,
            "regime": self.regime,
            "regime_title": self.regime_title,
            "regime_description": self.regime_description,
            "cool_off_probability_pct": self.cool_off_probability_pct,
            "benchmark_btc": self.benchmark_btc.as_dict(),
            "benchmark_eth": self.benchmark_eth.as_dict(),
            "derivatives_heat": self.derivatives_heat.as_dict(),
            "market_breadth": self.market_breadth.as_dict(),
            "liquidity_divergence": self.liquidity_divergence.as_dict(),
            "support_shelves": [s.as_dict() for s in self.support_shelves],
            "recommendations": self.recommendations,
            "anti_chasing_alert": self.anti_chasing_alert,
            "analyzed_at": self.analyzed_at.isoformat(),
        }
