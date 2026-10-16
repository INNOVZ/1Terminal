from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from algotrader.domain.models import Direction


@dataclass(frozen=True, slots=True)
class FearGreedReading:
    """Fear & Greed index reading from Alternative.me."""

    value: int
    classification: str
    observed_at: datetime
    previous_value: int | None = None
    seven_day_average: Decimal | None = None

    @property
    def is_extreme_fear(self) -> bool:
        return self.value <= 25

    @property
    def is_extreme_greed(self) -> bool:
        return self.value >= 75

    @property
    def is_neutral(self) -> bool:
        return 45 <= self.value <= 55


@dataclass(frozen=True, slots=True)
class StablecoinSupplyMetric:
    """Aggregated stablecoin circulating supply metrics from DeFiLlama."""

    total_circulating_usd: Decimal
    change_1d_pct: Decimal
    change_7d_pct: Decimal
    change_30d_pct: Decimal
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_expanding_macro_liquidity(self) -> bool:
        """Net new fiat capital entering crypto ecosystem (> 0.5% in 7d)."""
        return self.change_7d_pct >= Decimal("0.5")

    @property
    def is_contracting_macro_liquidity(self) -> bool:
        """Net capital redemption or flight from crypto (< -0.5% in 7d)."""
        return self.change_7d_pct <= Decimal("-0.5")


@dataclass(frozen=True, slots=True)
class GlobalMarketMetrics:
    """Global crypto breadth from CoinGecko."""

    btc_dominance_pct: Decimal
    btc_dominance_24h_delta: Decimal
    total_market_cap_usd: Decimal
    total_volume_24h_usd: Decimal
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_altseason_tailwind(self) -> bool:
        """BTC dominance dropping while total market cap expanding."""
        return self.btc_dominance_24h_delta < Decimal("-0.2")


@dataclass(frozen=True, slots=True)
class MacroSentimentContext:
    """Point-in-time composite macro & sentiment context."""

    fear_greed: FearGreedReading | None = None
    stablecoin_supply: StablecoinSupplyMetric | None = None
    market_breadth: GlobalMarketMetrics | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def multiplier_for(self, direction: Direction) -> Decimal:
        """Calculate score adjustment multiplier (0.85 to 1.15) based on institutional macro context."""
        multiplier = Decimal("1.0")

        # 1. Fear & Greed Contrarian & Trend Impact
        if self.fear_greed is not None:
            val = self.fear_greed.value
            if direction is Direction.LONG:
                if val <= 25:
                    # Contrarian upside: extreme fear provides high reward/risk for long reversals
                    multiplier += Decimal("0.05")
                elif val >= 75:
                    # Euphoria exhaustion risk: breakout longs are prone to liquidation wicks
                    multiplier -= Decimal("0.08")
                elif 40 <= val <= 65:
                    # Healthy trending momentum
                    multiplier += Decimal("0.03")
            elif direction is Direction.SHORT:
                if val >= 75:
                    # Euphoria gives asymmetric payoff to short squeezes fading out
                    multiplier += Decimal("0.05")
                elif val <= 25:
                    # Crowded panic shorts have poor payoff (bear trap risk)
                    multiplier -= Decimal("0.08")

        # 2. DeFiLlama Macro Liquidity (Fiat inflows/outflows)
        if self.stablecoin_supply is not None:
            if self.stablecoin_supply.is_expanding_macro_liquidity:
                if direction is Direction.LONG:
                    multiplier += Decimal("0.08")
                else:
                    multiplier -= Decimal("0.06")
            elif self.stablecoin_supply.is_contracting_macro_liquidity:
                if direction is Direction.LONG:
                    multiplier -= Decimal("0.08")
                else:
                    multiplier += Decimal("0.08")

        # 3. BTC Dominance / Market Breadth
        if self.market_breadth is not None:
            if self.market_breadth.is_altseason_tailwind:
                if direction is Direction.LONG:
                    multiplier += Decimal("0.04")
            elif self.market_breadth.btc_dominance_24h_delta > Decimal("0.3"):
                # BTC sucking liquidity from alts
                if direction is Direction.LONG:
                    multiplier -= Decimal("0.04")
                else:
                    multiplier += Decimal("0.03")

        # Clamp between 0.85 and 1.15
        if multiplier < Decimal("0.85"):
            return Decimal("0.85")
        if multiplier > Decimal("1.15"):
            return Decimal("1.15")
        return multiplier

    def narrative(self) -> str:
        parts: list[str] = []
        if self.fear_greed is not None:
            parts.append(f"F&G: {self.fear_greed.value}/100 ({self.fear_greed.classification})")
        if self.stablecoin_supply is not None:
            chg7d = self.stablecoin_supply.change_7d_pct
            trend = "Inflow" if chg7d > 0 else "Outflow"
            parts.append(f"Stablecoins 7d: {chg7d:+.2f}% ({trend})")
        if self.market_breadth is not None:
            parts.append(f"BTC.D: {self.market_breadth.btc_dominance_pct:.1f}%")
        return " | ".join(parts) if parts else "Neutral (Macro Offline)"

    def as_dict(self) -> dict[str, Any]:
        fg_score = self.fear_greed.value if self.fear_greed else 50
        fg_class = self.fear_greed.classification if self.fear_greed else "Neutral"
        btc_dom = float(self.market_breadth.btc_dominance_pct) if self.market_breadth else 59.0
        st_flow = float(self.stablecoin_supply.change_7d_pct) if self.stablecoin_supply else 0.0
        st_total = float(self.stablecoin_supply.total_circulating_usd) if self.stablecoin_supply else 302.0e9
        return {
            "fear_greed_score": fg_score,
            "fear_greed_classification": fg_class,
            "btc_dominance": btc_dom,
            "stablecoin_net_flow_7d": st_flow,
            "stablecoin_total_usd": st_total,
            "narrative": self.narrative(),
            "observed_at": self.observed_at.isoformat(),
        }
