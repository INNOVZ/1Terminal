from __future__ import annotations

from decimal import Decimal

from algotrader.config import FOUR_HOUR_DECISION_PROFILES, Settings, StrategyProfile
from algotrader.domain.models import Candidate, Direction, MomentumState, TradeSignal
from algotrader.domain.precision import normalize_bracket_prices


class TrendMomentumStrategy:
    name = "trend_momentum_v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def signal(self, candidate: Candidate) -> TradeSignal:
        if candidate.strategy_profile != self._settings.strategy_profile.value:
            raise ValueError(
                "candidate strategy profile does not match the configured execution profile"
            )
        legacy = self._settings.strategy_profile is StrategyProfile.LEGACY_MOMENTUM
        if legacy and candidate.momentum_state is not MomentumState.GAINING:
            raise ValueError("entry requires gaining directional momentum")
        if (
            legacy
            and self._settings.require_pullback_confirmation
            and not candidate.continuation_trigger_passed
        ):
            raise ValueError("entry requires a confirmed EMA20 pullback continuation")
        chase_limit = candidate.atr * self._settings.max_entry_chase_atr
        if (
            candidate.direction is Direction.LONG
            and candidate.entry_price > candidate.signal_bar_close + chase_limit
        ):
            raise ValueError("long entry is too far above the signal close")
        if (
            candidate.direction is Direction.SHORT
            and candidate.entry_price < candidate.signal_bar_close - chase_limit
        ):
            raise ValueError("short entry is too far below the signal close")
        level_buffer = candidate.atr * self._settings.key_level_buffer_atr
        if (
            legacy
            and candidate.direction is Direction.LONG
            and candidate.resistance_price is not None
            and candidate.entry_price >= candidate.resistance_price - level_buffer
        ):
            raise ValueError("long entry is too close to resistance")
        if (
            legacy
            and candidate.direction is Direction.SHORT
            and candidate.support_price is not None
            and candidate.entry_price <= candidate.support_price + level_buffer
        ):
            raise ValueError("short entry is too close to support")
        if candidate.atr <= 0:
            raise ValueError("ATR must be positive before constructing a protective bracket")

        stop, stop_basis = self._stop_price(candidate)
        target, target_basis = self._target_price(candidate, stop)
        stop, target = normalize_bracket_prices(
            direction=candidate.direction,
            entry_price=candidate.entry_price,
            stop_price=stop,
            target_price=target,
            tick_size=candidate.instrument.price_tick,
        )
        signal = TradeSignal(
            instrument=candidate.instrument,
            direction=candidate.direction,
            score=candidate.score,
            entry_price=candidate.entry_price,
            signal_bar_close=candidate.signal_bar_close,
            atr=candidate.atr,
            stop_price=stop,
            target_price=target,
            strategy=candidate.strategy_profile,
            timeframe=(
                "4h execution / daily 30d-90d-180d regime"
                if self._settings.strategy_profile in FOUR_HOUR_DECISION_PROFILES
                else "1h impulse-retest execution / 4h EMA50 regime"
                if self._settings.strategy_profile is StrategyProfile.EARLY_MOMENTUM
                else "1h execution / 4h 7d-30d regime"
                if not legacy
                else "1h/4h"
            ),
            momentum_state=candidate.momentum_state,
            rationale=(
                *candidate.reasons,
                stop_basis,
                target_basis,
                "position size: fixed-fractional equity risk divided by stop distance",
            ),
            rsi_1h=candidate.rsi_1h,
            rsi_4h=candidate.rsi_4h,
            support_price=candidate.support_price,
            resistance_price=candidate.resistance_price,
            continuation_trigger_passed=candidate.continuation_trigger_passed,
            btc_beta=candidate.btc_beta,
            btc_correlation=candidate.btc_correlation,
            created_at=candidate.evaluated_at,
            signal_bar_closed_at=candidate.signal_bar_closed_at,
            expires_at=candidate.expires_at,
        )
        if signal.reward_risk_ratio < self._settings.min_reward_risk_multiple:
            raise ValueError(
                "opposing structure leaves insufficient reward/risk after price normalization: "
                f"{signal.reward_risk_ratio:.2f} < {self._settings.min_reward_risk_multiple}"
            )
        return signal

    def _stop_price(self, candidate: Candidate) -> tuple[Decimal, str]:
        entry = candidate.entry_price
        atr = candidate.atr
        atr_distance = atr * self._settings.atr_stop_multiple
        structure_buffer = atr * self._settings.protective_level_buffer_atr
        max_structure_distance = atr * self._settings.max_structural_stop_distance_atr

        if candidate.direction is Direction.LONG:
            atr_stop = entry - atr_distance
            support = candidate.support_price
            if support is not None and Decimal("0") < support < entry:
                structural_stop = support - structure_buffer
                structural_distance = entry - structural_stop
                if structural_stop > 0 and structural_distance <= max_structure_distance:
                    return min(atr_stop, structural_stop), (
                        "stop: below support with ATR buffer and minimum ATR distance"
                    )
            return atr_stop, "stop: ATR volatility distance (support outside structural cap)"

        atr_stop = entry + atr_distance
        resistance = candidate.resistance_price
        if resistance is not None and resistance > entry:
            structural_stop = resistance + structure_buffer
            structural_distance = structural_stop - entry
            if structural_distance <= max_structure_distance:
                return max(atr_stop, structural_stop), (
                    "stop: above resistance with ATR buffer and minimum ATR distance"
                )
        return atr_stop, "stop: ATR volatility distance (resistance outside structural cap)"

    def _target_price(self, candidate: Candidate, stop: Decimal) -> tuple[Decimal, str]:
        risk_distance = abs(candidate.entry_price - stop)
        planned_reward = risk_distance * self._settings.reward_risk_multiple
        structure_buffer = candidate.atr * self._settings.protective_level_buffer_atr

        if candidate.direction is Direction.LONG:
            reward_target = candidate.entry_price + planned_reward
            resistance = candidate.resistance_price
            if resistance is not None:
                structural_target = resistance - structure_buffer
                if candidate.entry_price < structural_target < reward_target:
                    return structural_target, "target: before resistance, above minimum reward/risk"
            return reward_target, "target: configured reward/risk multiple"

        reward_target = candidate.entry_price - planned_reward
        support = candidate.support_price
        if support is not None:
            structural_target = support + structure_buffer
            if reward_target < structural_target < candidate.entry_price:
                return structural_target, "target: before support, above minimum reward/risk"
        return reward_target, "target: configured reward/risk multiple"
