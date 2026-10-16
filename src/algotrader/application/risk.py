from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from algotrader.config import RESEARCH_PROFILE_VALUES, Settings
from algotrader.domain.models import (
    AccountSnapshot,
    AssetClass,
    Direction,
    MomentumState,
    RiskDecision,
    TradeSignal,
    Venue,
)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


class PortfolioRiskManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def max_futures_leverage(self) -> int:
        return self._settings.max_futures_leverage

    @property
    def entry_limit_offset_bps(self) -> Decimal:
        return self._settings.entry_limit_offset_bps

    @property
    def max_entry_chase_atr(self) -> Decimal:
        return self._settings.max_entry_chase_atr

    def evaluate(
        self,
        signal: TradeSignal,
        account: AccountSnapshot,
        venue_account: AccountSnapshot | None = None,
        *,
        executable_entry_price: Decimal | None = None,
    ) -> RiskDecision:
        sizing_account = venue_account or account
        entry_price = executable_entry_price or signal.entry_price
        portfolio_violation = self._portfolio_violation(signal, account, sizing_account)
        if portfolio_violation is not None:
            return RiskDecision(False, portfolio_violation)
        risk_per_unit = abs(entry_price - signal.stop_price)
        reward_per_unit = abs(signal.target_price - entry_price)
        reward_risk_ratio = reward_per_unit / risk_per_unit if risk_per_unit > 0 else Decimal("0")
        signal_violation = self._signal_violation(signal, entry_price, reward_risk_ratio)
        if signal_violation is not None:
            return RiskDecision(False, signal_violation)
        return self._size_position(
            signal,
            sizing_account,
            entry_price,
            risk_per_unit,
            reward_risk_ratio,
        )

    def _portfolio_violation(
        self,
        signal: TradeSignal,
        account: AccountSnapshot,
        sizing_account: AccountSnapshot,
    ) -> str | None:
        btc_exposure_required = signal.strategy in RESEARCH_PROFILE_VALUES
        correlated_positions = sum(
            1
            for position in account.positions
            if position.instrument_id.venue in {Venue.BINANCE_SPOT, Venue.BINANCE_USDM}
            and position.direction is signal.direction
        )
        checks = (
            (
                min(
                    account.equity,
                    account.available_balance,
                    sizing_account.equity,
                    sizing_account.available_balance,
                )
                <= 0,
                "no available equity",
            ),
            (
                # Daily limits are denominated on day-start equity; only the drawdown gate
                # below is measured against the all-time peak.
                account.realized_pnl_today
                <= -(account.daily_risk_base * self._settings.max_daily_loss),
                "daily loss limit reached",
            ),
            (
                account.mark_to_market_pnl_today
                <= -(account.daily_risk_base * self._settings.max_intraday_mark_to_market_loss),
                "intraday mark-to-market loss limit reached",
            ),
            (account.drawdown >= self._settings.max_drawdown, "portfolio drawdown limit reached"),
            (
                len(account.positions) >= self._settings.max_open_positions,
                "maximum open positions reached",
            ),
            (
                any(
                    position.instrument_id == signal.instrument.id for position in account.positions
                ),
                "instrument already has an open position",
            ),
            (
                signal.instrument.asset_class is AssetClass.CRYPTO
                and correlated_positions >= self._settings.max_same_direction_crypto_positions,
                "correlated crypto direction limit reached",
            ),
            (
                signal.instrument.asset_class is AssetClass.CRYPTO
                and btc_exposure_required
                and (signal.btc_beta is None or signal.btc_correlation is None),
                "BTC beta/correlation data is unavailable for this research profile",
            ),
            (
                signal.btc_beta is not None
                and abs(signal.btc_beta) > self._settings.max_abs_btc_beta,
                f"absolute BTC beta {signal.btc_beta} exceeds {self._settings.max_abs_btc_beta}",
            ),
            (
                signal.instrument.asset_class is AssetClass.CRYPTO
                and signal.btc_correlation is not None
                and abs(signal.btc_correlation) >= self._settings.max_btc_correlation
                and correlated_positions >= self._settings.max_btc_correlated_positions,
                "BTC-correlated portfolio position limit reached",
            ),
        )
        return next((message for failed, message in checks if failed), None)

    def _signal_violation(
        self,
        signal: TradeSignal,
        entry_price: Decimal,
        reward_risk_ratio: Decimal,
    ) -> str | None:
        legacy = signal.strategy not in RESEARCH_PROFILE_VALUES
        long = signal.direction is Direction.LONG
        valid_bracket = (
            signal.stop_price < entry_price < signal.target_price
            if long
            else signal.target_price < entry_price < signal.stop_price
        )
        chase_limit = signal.atr * self._settings.max_entry_chase_atr
        checks = (
            (abs(entry_price - signal.stop_price) <= 0, "invalid stop distance"),
            (not valid_bracket, f"invalid {signal.direction.value} bracket geometry"),
            (
                reward_risk_ratio < self._settings.min_reward_risk_multiple,
                f"reward/risk {reward_risk_ratio:.2f} is below minimum "
                f"{self._settings.min_reward_risk_multiple}",
            ),
            (
                legacy and signal.momentum_state is not MomentumState.GAINING,
                "directional momentum is not gaining",
            ),
            (
                legacy
                and self._settings.require_pullback_confirmation
                and not signal.continuation_trigger_passed,
                "EMA20 pullback continuation is not confirmed",
            ),
            (
                legacy and long and signal.rsi_1h > self._settings.rsi_long_max,
                f"1h RSI {signal.rsi_1h:.1f} is above the exhaustion threshold "
                f"({self._settings.rsi_long_max})",
            ),
            (
                legacy and not long and signal.rsi_1h < self._settings.rsi_short_min,
                f"1h RSI {signal.rsi_1h:.1f} is below the exhaustion threshold "
                f"({self._settings.rsi_short_min})",
            ),
            (
                legacy and long and signal.rsi_4h > self._settings.four_hour_rsi_long_max,
                f"4h RSI {signal.rsi_4h:.1f} is exhausted above threshold "
                f"({self._settings.four_hour_rsi_long_max})",
            ),
            (
                legacy and not long and signal.rsi_4h < self._settings.four_hour_rsi_short_min,
                f"4h RSI {signal.rsi_4h:.1f} is exhausted below threshold "
                f"({self._settings.four_hour_rsi_short_min})",
            ),
            (
                (long and signal.entry_price > signal.signal_bar_close + chase_limit)
                or (not long and signal.entry_price < signal.signal_bar_close - chase_limit),
                "entry price is beyond the ATR chase limit",
            ),
        )
        return next((message for failed, message in checks if failed), None)

    def _cost_adjusted_risk_per_unit(
        self,
        signal: TradeSignal,
        entry_price: Decimal,
        risk_per_unit: Decimal,
    ) -> Decimal:
        """Charge round-trip fees and expected stop slippage against the risk budget.

        Sizing on the bare stop distance understates the loss actually realized when the
        stop fires: the exchange takes a taker fee on both legs, and a stop is a *trigger*,
        so the exit prints past it. Ignoring both makes every trade risk more than
        ``account_risk_per_trade`` claims.
        """
        fee_rate = self._settings.taker_fee_bps / Decimal("10000")
        slippage_rate = self._settings.stop_slippage_bps / Decimal("10000")
        entry_fee = entry_price * fee_rate
        exit_fee = signal.stop_price * fee_rate
        slippage = signal.stop_price * slippage_rate
        return risk_per_unit + entry_fee + exit_fee + slippage

    def _size_position(
        self,
        signal: TradeSignal,
        sizing_account: AccountSnapshot,
        entry_price: Decimal,
        risk_per_unit: Decimal,
        reward_risk_ratio: Decimal,
    ) -> RiskDecision:
        risk_budget = sizing_account.equity * self._settings.account_risk_per_trade
        raw_quantity = risk_budget / self._cost_adjusted_risk_per_unit(
            signal, entry_price, risk_per_unit
        )
        quantity = floor_to_step(raw_quantity, signal.instrument.quantity_step)
        if quantity < signal.instrument.min_quantity:
            return RiskDecision(False, "risk-sized quantity is below venue minimum")

        notional = quantity * entry_price
        futures = signal.instrument.market_kind.value == "futures"
        leverage = Decimal(self._settings.max_futures_leverage) if futures else Decimal("1")
        total_exposure_cap = sizing_account.equity * self._settings.max_gross_exposure * leverage
        current_gross_exposure = sum(
            (
                abs(position.quantity)
                * (position.mark_price if position.mark_price > 0 else position.entry_price)
                for position in sizing_account.positions
            ),
            Decimal("0"),
        )
        remaining_exposure = max(Decimal("0"), total_exposure_cap - current_gross_exposure)
        exposure_cap = min(
            remaining_exposure,
            sizing_account.available_balance * leverage,
        )
        if futures:
            per_position_cap = (
                sizing_account.equity
                * self._settings.futures_margin_fraction_per_position
                * leverage
            )
            exposure_cap = min(exposure_cap, per_position_cap)
        if exposure_cap <= 0:
            return RiskDecision(False, "maximum gross exposure reached")
        if notional > exposure_cap:
            quantity = floor_to_step(exposure_cap / entry_price, signal.instrument.quantity_step)
            notional = quantity * entry_price
        if notional < signal.instrument.min_notional:
            return RiskDecision(False, "order notional is below venue minimum")
        if notional > sizing_account.available_balance * leverage:
            return RiskDecision(False, "insufficient available balance for exposure")
        return RiskDecision(
            True,
            "approved",
            quantity=quantity,
            notional=notional,
            executable_entry_price=entry_price,
            risk_per_unit=risk_per_unit,
            reward_risk_ratio=reward_risk_ratio,
        )
