from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from algotrader.domain.models import Direction, Instrument


class CryptoMarketRegime(StrEnum):
    """Microstructure & trend regime classification."""

    STRONG_TREND_UP = "strong_trend_up"
    STRONG_TREND_DOWN = "strong_trend_down"
    EMERGING_TREND_UP = "emerging_trend_up"
    EMERGING_TREND_DOWN = "emerging_trend_down"
    MEAN_REVERTING_RANGE = "mean_reverting_range"
    VOLATILITY_SQUEEZE = "volatility_squeeze"
    CHOPPY_NOISE = "choppy_noise"


class ScreenerMode(StrEnum):
    """Screening objective: pre-breakout early accumulation coil, distribution breakdown, or active expansion."""

    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    BREAKOUT = "breakout"
    ALL = "all"


class SetupLabel(StrEnum):
    """High-conviction institutional crypto setup labels."""

    # Pre-Breakout & Accumulation Setups (BEFORE the pump)
    SHORT_SQUEEZE_ABSORPTION_LONG = "SHORT_SQUEEZE_ABSORPTION_LONG"
    COIL_SPRING_LONG = "COIL_SPRING_LONG"
    OI_ACCUMULATION_LONG = "OI_ACCUMULATION_LONG"
    VCP_COMPRESSION_LONG = "VCP_COMPRESSION_LONG"
    RANGE_BOTTOM_ACCUMULATION_LONG = "RANGE_BOTTOM_ACCUMULATION_LONG"

    # Pre-Breakdown & Distribution Setups (BEFORE the dump)
    COIL_BREAKDOWN_SHORT = "COIL_BREAKDOWN_SHORT"
    DISTRIBUTION_SHORT = "DISTRIBUTION_SHORT"
    VCP_DISTRIBUTION_SHORT = "VCP_DISTRIBUTION_SHORT"
    UTAD_DIVERGENCE_SHORT = "UTAD_DIVERGENCE_SHORT"
    EXHAUSTION_REVERSAL_SHORT = "EXHAUSTION_REVERSAL_SHORT"

    # Active Expansion / Trend Setups (AFTER ignition)
    BREAKOUT_LONG = "BREAKOUT_LONG"
    BREAKOUT_SHORT = "BREAKOUT_SHORT"
    CONTINUATION_LONG = "CONTINUATION_LONG"
    CONTINUATION_SHORT = "CONTINUATION_SHORT"
    SQUEEZE_LONG = "SQUEEZE_LONG"
    SQUEEZE_SHORT = "SQUEEZE_SHORT"
    MOMENTUM_EMERGING_LONG = "MOMENTUM_EMERGING_LONG"
    MOMENTUM_EMERGING_SHORT = "MOMENTUM_EMERGING_SHORT"
    RANGE_FADE_LONG = "RANGE_FADE_LONG"
    RANGE_FADE_SHORT = "RANGE_FADE_SHORT"
    NO_SETUP = "NO_SETUP"


@dataclass(frozen=True, slots=True)
class ChecklistVerification:
    """Deterministic 5-point Institutional Checklist badges (Section 8 of AGENT.md)."""

    value_acceptance: bool
    volatility_structure: bool
    momentum_alignment: bool
    volume_microstructure: bool
    derivatives_positioning: bool

    @property
    def passed_count(self) -> int:
        return sum(
            [
                self.value_acceptance,
                self.volatility_structure,
                self.momentum_alignment,
                self.volume_microstructure,
                self.derivatives_positioning,
            ]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "value_acceptance": self.value_acceptance,
            "volatility_structure": self.volatility_structure,
            "momentum_alignment": self.momentum_alignment,
            "volume_microstructure": self.volume_microstructure,
            "derivatives_positioning": self.derivatives_positioning,
            "passed_count": self.passed_count,
        }


@dataclass(frozen=True, slots=True)
class CryptoScreenerScoreBreakdown:
    """Component factor scores (each 0 to 100) and macro multiplier."""

    trend_score: Decimal
    momentum_score: Decimal
    derivatives_score: Decimal
    alpha_score: Decimal
    volume_score: Decimal
    structure_score: Decimal
    macro_multiplier: Decimal
    final_composite_score: Decimal


@dataclass(frozen=True, slots=True)
class CryptoOpportunity:
    """A ranked, actionable trade opportunity discovered by the multi-factor crypto screener."""

    instrument: Instrument
    direction: Direction
    regime: CryptoMarketRegime
    setup_label: SetupLabel
    score: Decimal
    score_breakdown: CryptoScreenerScoreBreakdown
    current_price: Decimal
    atr14: Decimal
    suggested_entry: Decimal
    suggested_stop: Decimal
    suggested_targets: tuple[Decimal, ...]
    risk_reward_ratio: Decimal
    btc_beta: Decimal
    residual_return_24h: Decimal
    funding_rate: Decimal | None
    oi_change_1h: Decimal | None
    taker_ratio: Decimal | None
    rationale: tuple[str, ...]
    macro_narrative: str
    poc_price: Decimal | None = None
    vah_price: Decimal | None = None
    val_price: Decimal | None = None
    adv_7d: Decimal | None = None
    adv_14d: Decimal | None = None
    volume_contraction_ratio: Decimal | None = None
    oi_change_7d: Decimal | None = None
    pullback_entry: Decimal | None = None
    breakout_trigger: Decimal | None = None
    checklist: tuple[tuple[str, bool, str], ...] = field(default_factory=tuple)
    checklist_verification: ChecklistVerification | None = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    estimated_win_rate_pct: Decimal = Decimal("65.0")
    expected_value_r: Decimal = Decimal("2.10")
    conviction: Decimal | None = None
    grade: str = "Grade B"
    cvd_24h: Decimal | None = None
    cvd_z_score: Decimal | None = None
    orderflow_absorption: bool = False
    max_recommended_size_usd: Decimal | None = None
    execution_mode: str = "RETEST_ACCUMULATION"
    resistance_absorption_score: Decimal | None = None
    breakout_readiness_status: str = "RETEST_ONLY_WAIT_FOR_DIP"
    candles: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        tick = self.instrument.price_tick
        import math
        try:
            decimals = max(2, -int(math.floor(math.log10(float(tick))))) if tick > Decimal("0") else 4
        except Exception:
            decimals = 4

        def _fmt(val: Decimal | None) -> str:
            if val is None:
                return "N/A"
            return f"{float(val):.{decimals}f}"

        return {
            "symbol": self.instrument.id.symbol,
            "base_asset": self.instrument.base_asset,
            "quote_asset": self.instrument.quote_asset,
            "direction": self.direction.value,
            "regime": self.regime.value,
            "setup_label": self.setup_label.value,
            "execution_mode": self.execution_mode,
            "resistance_absorption_score": str(self.resistance_absorption_score) if self.resistance_absorption_score is not None else "N/A",
            "breakout_readiness_status": self.breakout_readiness_status,
            "score": str(self.score),
            "tick_size": str(tick),
            "current_price": _fmt(self.current_price),
            "suggested_entry": _fmt(self.suggested_entry),
            "pullback_entry": _fmt(self.pullback_entry) if self.pullback_entry is not None else _fmt(self.suggested_entry),
            "breakout_trigger": _fmt(self.breakout_trigger) if self.breakout_trigger is not None else "N/A",
            "suggested_stop": _fmt(self.suggested_stop),
            "suggested_targets": [_fmt(t) for t in self.suggested_targets],
            "risk_reward_ratio": str(self.risk_reward_ratio),
            "estimated_win_rate_pct": str(self.estimated_win_rate_pct),
            "expected_value_r": str(self.expected_value_r),
            "conviction": str(self.conviction) if self.conviction is not None else str(self.score),
            "grade": self.grade,
            "checklist_verification": self.checklist_verification.as_dict() if self.checklist_verification is not None else {},
            "btc_beta": str(self.btc_beta),
            "residual_return_24h_pct": f"{float(self.residual_return_24h * 100):+.2f}%",
            "funding_rate_bps": f"{float((self.funding_rate or Decimal('0')) * 10000):+.2f} bps",
            "oi_change_1h_pct": f"{float((self.oi_change_1h or Decimal('0')) * 100):+.2f}%" if self.oi_change_1h is not None else "N/A",
            "oi_change_7d_pct": f"{float((self.oi_change_7d or Decimal('0')) * 100):+.2f}%" if self.oi_change_7d is not None else "N/A",
            "taker_buy_sell_ratio": str(self.taker_ratio) if self.taker_ratio is not None else "N/A",
            "cvd_24h": str(self.cvd_24h) if self.cvd_24h is not None else "N/A",
            "cvd_z_score": str(self.cvd_z_score) if self.cvd_z_score is not None else "N/A",
            "orderflow_absorption": self.orderflow_absorption,
            "max_recommended_size_usd": str(self.max_recommended_size_usd) if self.max_recommended_size_usd is not None else "N/A",
            "poc_price": str(self.poc_price) if self.poc_price is not None else "N/A",
            "vah_price": str(self.vah_price) if self.vah_price is not None else "N/A",
            "val_price": str(self.val_price) if self.val_price is not None else "N/A",
            "adv_7d": str(self.adv_7d) if self.adv_7d is not None else "N/A",
            "adv_14d": str(self.adv_14d) if self.adv_14d is not None else "N/A",
            "volume_contraction_ratio": str(self.volume_contraction_ratio) if self.volume_contraction_ratio is not None else "N/A",
            "checklist": [
                {"title": item[0], "passed": item[1], "detail": item[2]}
                for item in self.checklist
            ],
            "score_breakdown": {
                "trend": str(self.score_breakdown.trend_score),
                "momentum": str(self.score_breakdown.momentum_score),
                "derivatives": str(self.score_breakdown.derivatives_score),
                "alpha": str(self.score_breakdown.alpha_score),
                "volume": str(self.score_breakdown.volume_score),
                "structure": str(self.score_breakdown.structure_score),
                "macro_multiplier": str(self.score_breakdown.macro_multiplier),
            },
            "rationale": list(self.rationale),
            "macro_narrative": self.macro_narrative,
            "evaluated_at": self.evaluated_at.isoformat(),
            "candles": list(self.candles),
        }


def calculate_opportunity_win_rate_and_ev(
    setup_label: SetupLabel | str,
    score: Decimal,
    checklist_passed: int,
    checklist_total: int,
    risk_reward_ratio: Decimal,
    taker_ratio: Decimal | None = None,
    macro_multiplier: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Calculate institutional estimated win rate % and mathematical expectancy (+R).

    Formulation:
    1. Base rate calibrated from historical Wyckoff/auction market edge.
    2. Quality Gate adjustment (0 to 100% checklist compliance).
    3. Multi-factor screener score confidence tier.
    4. Taker aggression flow confirmation.
    5. Macro liquidity tailwind / headwind multiplier.
    6. Expectancy calculation: E = (P_win * RR) - ((1 - P_win) * 1.0).
    """
    label_str = setup_label.value if hasattr(setup_label, "value") else str(setup_label)

    # 1. Setup base rate
    if "Spring" in label_str or "SPRING" in label_str or "Shakeout" in label_str:
        base_win = Decimal("68.5")
    elif "VCP" in label_str or "COMPRESSION" in label_str or "Compression" in label_str:
        base_win = Decimal("66.0")
    elif "UTAD" in label_str:
        base_win = Decimal("67.0")
    elif "Breakdown Coil" in label_str or "BREAKDOWN" in label_str:
        base_win = Decimal("63.0")
    elif "DISTRIBUTION" in label_str or "Distribution" in label_str:
        base_win = Decimal("65.0")
    elif "POC" in label_str:
        base_win = Decimal("62.5")
    elif "BREAKOUT" in label_str or "Breakout" in label_str:
        base_win = Decimal("62.0")
    else:
        base_win = Decimal("61.0")

    # 2. Quality Gate adjustment
    if checklist_total > 0:
        ratio = checklist_passed / checklist_total
        if ratio >= 1.0:
            base_win += Decimal("3.5")
        elif ratio >= 0.8:
            base_win += Decimal("1.5")
        elif ratio < 0.6:
            base_win -= Decimal("5.0")

    # 3. Composite Screener Score tier
    if score >= Decimal("75.0"):
        base_win += Decimal("2.5")
    elif score >= Decimal("65.0"):
        base_win += Decimal("1.0")
    elif score < Decimal("55.0"):
        base_win -= Decimal("3.0")

    # 4. Taker aggression adjustment
    if taker_ratio is not None:
        if taker_ratio >= Decimal("1.25"):
            base_win += Decimal("2.0")
        elif taker_ratio <= Decimal("0.85"):
            base_win -= Decimal("2.5")

    # 5. Macro multiplier adjustment
    if macro_multiplier is not None:
        if macro_multiplier >= Decimal("1.05"):
            base_win += Decimal("1.5")
        elif macro_multiplier <= Decimal("0.95"):
            base_win -= Decimal("3.0")

    # Institutional bounded range [45.0%, 78.0%]
    win_rate = max(Decimal("45.0"), min(Decimal("78.0"), base_win)).quantize(Decimal("0.1"))

    # Mathematical Expectancy (+R multiples)
    p_win = win_rate / Decimal("100.0")
    p_loss = Decimal("1.0") - p_win
    rr = max(Decimal("1.0"), risk_reward_ratio)
    ev = (p_win * rr) - (p_loss * Decimal("1.0"))
    ev_r = ev.quantize(Decimal("0.01"))

    return win_rate, ev_r

