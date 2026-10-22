from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from algotrader.domain.errors import ConfigurationError


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ExecutionMode(StrEnum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


class StrategyProfile(StrEnum):
    """Explicit alpha policy selected before market data is evaluated.

    Keeping the legacy policy available makes every comparison reproducible. The newer
    profile deliberately uses MACD/RSI as diagnostics and small ranking inputs rather than
    stacking them as correlated hard gates.
    """

    LEGACY_MOMENTUM = "legacy_momentum_v1"
    REGIME_BREAKOUT = "regime_breakout_v2"
    TREND_QUALITY = "trend_quality_v3"
    SLOW_TREND = "slow_tsmom_v4"
    EMPIRICAL_TREND = "empirical_tsmom_v5"
    LAYERED_MOMENTUM = "layered_tsmom_v6"
    EARLY_MOMENTUM = "early_momentum_v7"


DAILY_REGIME_PROFILES = frozenset(
    {
        StrategyProfile.SLOW_TREND,
        StrategyProfile.EMPIRICAL_TREND,
        StrategyProfile.LAYERED_MOMENTUM,
    }
)
FOUR_HOUR_DECISION_PROFILES = DAILY_REGIME_PROFILES
EMPIRICAL_ALPHA_PROFILES = frozenset(
    {StrategyProfile.EMPIRICAL_TREND, StrategyProfile.LAYERED_MOMENTUM}
)
SOFT_STRUCTURE_PROFILES = frozenset(
    {
        StrategyProfile.EMPIRICAL_TREND,
        StrategyProfile.LAYERED_MOMENTUM,
        StrategyProfile.EARLY_MOMENTUM,
    }
)
CROSS_SECTIONAL_RANKING_PROFILES = frozenset(
    profile for profile in StrategyProfile if profile is not StrategyProfile.LEGACY_MOMENTUM
)
RESEARCH_PROFILE_VALUES = frozenset(profile.value for profile in CROSS_SECTIONAL_RANKING_PROFILES)


class EmpiricalSetup(StrEnum):
    """One pre-registered entry setup for the empirical trend profile."""

    BREAKOUT = "breakout"
    PULLBACK = "pullback"


LIVE_ACKNOWLEDGEMENT = "I_UNDERSTAND_LIVE_ORDERS_USE_REAL_FUNDS"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    live_trading_enabled: bool = False
    live_trading_ack: SecretStr = SecretStr("")

    operator_token: SecretStr = SecretStr("")
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("localhost", "127.0.0.1")

    automation_enabled: bool = False
    signal_discovery_interval_hours: int = Field(default=1, ge=1, le=24)
    execution_cooldown_hours: int = Field(default=8, ge=1, le=168)
    # Deprecated compatibility input. Remove after deployments migrate to
    # ATLAS_EXECUTION_COOLDOWN_HOURS.
    screening_interval_hours: int | None = Field(default=None, ge=1, le=168)
    signal_expiry_bars: int = Field(default=2, ge=1, le=8)
    execution_lease_seconds: int = Field(default=300, ge=60, le=900)
    bar_close_grace_seconds: int = Field(default=60, ge=5, le=300)
    scheduler_failure_limit: int = Field(default=3, ge=1, le=10)

    binance_spot_api_key: SecretStr = SecretStr("")
    binance_spot_api_secret: SecretStr = SecretStr("")
    binance_futures_api_key: SecretStr = SecretStr("")
    binance_futures_api_secret: SecretStr = SecretStr("")
    binance_futures_hedge_mode: bool = True
    testnet_uses_live_market_data: bool = True
    """Price testnet runs from the live public API.

    Spot Testnet serves ~5 days of 4h klines against a 480-candle requirement, so screening
    cannot run there at all, and its synthetic prices would make any signal meaningless.
    Only unauthenticated market-data endpoints are used; keys and orders stay on testnet.
    Set False to screen on testnet data, accepting that Spot will not produce candidates.
    """
    ignored_spot_balance_assets: Annotated[tuple[str, ...], NoDecode] = ()

    account_risk_per_trade: Decimal = Field(default=Decimal("0.0025"), gt=0, le=Decimal("0.01"))
    max_daily_loss: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.10"))
    max_intraday_mark_to_market_loss: Decimal = Field(
        default=Decimal("0.03"), gt=0, le=Decimal("0.15")
    )
    max_drawdown: Decimal = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.25"))
    max_open_positions: int = Field(default=3, ge=1, le=20)
    max_same_direction_crypto_positions: int = Field(default=2, ge=1, le=10)
    max_abs_btc_beta: Decimal = Field(default=Decimal("2.0"), gt=0, le=Decimal("5"))
    max_btc_correlation: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    max_btc_correlated_positions: int = Field(default=2, ge=1, le=10)
    max_gross_exposure: Decimal = Field(default=Decimal("1.0"), gt=0, le=Decimal("3"))
    max_futures_leverage: int = Field(default=3, ge=1, le=5)
    futures_margin_fraction_per_position: Decimal = Field(
        default=Decimal("0.33333333"), gt=0, le=Decimal("1")
    )
    paper_starting_equity: Decimal = Field(default=Decimal("100000"), gt=0)
    paper_spot_allocation: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    strategy_profile: StrategyProfile = StrategyProfile.LEGACY_MOMENTUM
    min_candidate_score: Decimal = Field(default=Decimal("70"), ge=0, le=100)
    max_symbols_to_analyze: int = Field(default=40, ge=5, le=100)
    collect_derivatives_features: bool = True
    min_4h_history_bars: int = Field(default=480, ge=210, le=990)
    min_quote_volume_24h: Decimal = Field(default=Decimal("10000000"), ge=0)
    max_spread_bps: Decimal = Field(default=Decimal("15"), gt=0, le=Decimal("100"))
    # Regime-breakout profile. These are intentionally few, interpretable parameters rather
    # than a wide hyper-parameter surface. Promote changes only through walk-forward tests.
    breakout_lookback_hours: int = Field(default=40, ge=20, le=168)
    breakout_shorts_enabled: bool = False
    volatility_regime_lookback_hours: int = Field(default=120, ge=60, le=240)
    min_volatility_percentile: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    max_volatility_percentile: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    breakout_min_relative_volume: Decimal = Field(default=Decimal("0.80"), gt=0, le=Decimal("4"))
    # Research-only quality layer for trend_quality_v3. EMA, MACD, RSI and ADX/DMI are
    # correlated transformations of price, so they form one consensus layer rather than
    # four independent hard gates. Two confirmations is intentionally the only tunable
    # threshold; wider grids would create a new data-mining surface.
    trend_quality_min_confirmations: int = Field(default=2, ge=1, le=4)
    slow_momentum_shorts_enabled: bool = True
    slow_breakout_4h_bars: int = Field(default=30, ge=12, le=120)
    # Research-only empirical profile. The setup choice is deliberately singular: a run
    # tests either breakout timing or pullback timing, never an opaque conjunction of both.
    empirical_setup: EmpiricalSetup = EmpiricalSetup.BREAKOUT
    empirical_shorts_enabled: bool = False
    empirical_breakout_4h_bars: int = Field(default=30, ge=12, le=120)
    # Research-only v6 policy. Keep these settings separate from empirical_tsmom_v5 so an
    # operator can reproduce the v5 baseline after v6 experiments change. The short sleeve
    # requires an explicit authority switch even though its formulas are direction-symmetric.
    layered_setup: EmpiricalSetup = EmpiricalSetup.BREAKOUT
    layered_shorts_enabled: bool = False
    layered_breakout_4h_bars: int = Field(default=30, ge=12, le=120)
    # Preregistered v7 impulse -> retest -> continuation policy. These thresholds describe
    # one event sequence and must not be grid-searched against the promotion holdout.
    early_momentum_shorts_enabled: bool = False
    early_breakout_lookback_hours: int = Field(default=20, ge=12, le=72)
    early_breakout_max_age_hours: int = Field(default=8, ge=2, le=24)
    early_breakout_min_relative_volume: Decimal = Field(
        default=Decimal("1.25"), ge=Decimal("1"), le=Decimal("4")
    )
    early_retest_touch_buffer_atr: Decimal = Field(default=Decimal("0.50"), ge=0, le=Decimal("2"))
    early_retest_hold_buffer_atr: Decimal = Field(default=Decimal("0.25"), ge=0, le=Decimal("1"))
    rsi_divergence_lookback_hours: int = Field(default=72, ge=24, le=240)
    rsi_divergence_max_age_hours: int = Field(default=12, ge=2, le=72)
    rsi_divergence_min_delta: Decimal = Field(default=Decimal("3"), ge=0, le=Decimal("20"))
    excluded_base_assets: Annotated[tuple[str, ...], NoDecode] = (
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "USDP",
        "DAI",
        "EUR",
        "PAXG",
        "XAU",
        "XAG",
        "COPPER",
        "SPY",
        "QQQ",
        "WLFI",
    )
    exclude_multiplier_contracts: bool = True
    support_resistance_lookback_bars: int = Field(default=48, ge=20, le=250)
    key_level_buffer_atr: Decimal = Field(default=Decimal("0.5"), gt=0, le=Decimal("5"))
    max_entry_chase_atr: Decimal = Field(default=Decimal("0.5"), gt=0, le=Decimal("3"))
    entry_limit_offset_bps: Decimal = Field(default=Decimal("5"), ge=0, le=Decimal("50"))
    momentum_lookback_bars: int = Field(default=3, ge=1, le=12)
    momentum_min_rising_checks: int = Field(default=3, ge=1, le=4)
    """How many of the four momentum checks must agree to call a move `gaining`.

    The four are price, MACD histogram, RSI, and ADX, each compared against the bar
    ``momentum_lookback_bars`` ago. `losing` has always required 3 of 4; requiring 4 of 4 for
    `gaining` made the classifier asymmetric, so a move had to be far cleaner to qualify as
    strengthening than as weakening. Measured across the liquid USD-M universe that left only
    5.4% of instruments `gaining` against 47.3% `losing`, which is a property of the
    threshold as much as of the market. Set to 4 to restore the stricter original behaviour.
    """
    # RSI exhaustion guards — blocks entries when the move is already spent.
    # 1h cap: prevents chasing a candle with RSI already in overbought territory.
    # 4h cap: rejects instruments where the higher-timeframe RSI is also extended —
    # the strongest signal of an imminent reversal (e.g. SYNUSDT Jun-2026 top).
    rsi_long_max: Decimal = Field(default=Decimal("72"), ge=Decimal("60"), le=Decimal("95"))
    rsi_short_min: Decimal = Field(default=Decimal("28"), ge=Decimal("5"), le=Decimal("40"))
    four_hour_rsi_long_max: Decimal = Field(
        default=Decimal("70"), ge=Decimal("55"), le=Decimal("90")
    )
    four_hour_rsi_short_min: Decimal = Field(
        default=Decimal("30"), ge=Decimal("10"), le=Decimal("45")
    )
    # Researchable regime controls. Permissive defaults preserve the validated baseline;
    # promote tighter values only after walk-forward, multi-asset evidence.
    min_4h_adx: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100"))
    require_4h_di_alignment: bool = False
    max_entry_extension_from_ema20_atr: Decimal = Field(
        default=Decimal("2.5"), gt=0, le=Decimal("20")
    )
    # Optional setup -> trigger separation. The higher-timeframe filters establish the
    # trend regime; this gate waits for a shallow 1h pullback and a confirmed continuation
    # instead of entering any mature three-bar momentum burst. It stays opt-in until the
    # walk-forward replay demonstrates positive expectancy after costs.
    require_pullback_confirmation: bool = False
    pullback_lookback_bars: int = Field(default=6, ge=2, le=24)
    pullback_touch_buffer_atr: Decimal = Field(default=Decimal("0.25"), ge=0, le=Decimal("2"))
    pullback_require_previous_bar_break: bool = False
    max_atr_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("1"))
    # Choppiness filter — prevents entries in ranging/choppy markets. V10 uses < 58.
    choppiness_length: int = Field(default=14, ge=5, le=80)
    max_choppiness: Decimal = Field(default=Decimal("58"), ge=Decimal("35"), le=Decimal("75"))
    # ATR-normalized momentum displacement lookback. V10 uses 10 bars.
    momentum_displacement_lookback: int = Field(default=10, ge=3, le=50)
    # MACD cross window — allows the EMA21 cross and MACD cross to be within N bars.
    macd_cross_window_bars: int = Field(default=3, ge=0, le=12)
    # Minimum relative volume — V10 uses 0.8x average.
    min_relative_volume: Decimal = Field(
        default=Decimal("0.8"), ge=Decimal("0.3"), le=Decimal("4")
    )
    atr_stop_multiple: Decimal = Field(default=Decimal("1.5"), gt=0, le=Decimal("10"))
    protective_level_buffer_atr: Decimal = Field(default=Decimal("0.25"), ge=0, le=Decimal("2"))
    max_structural_stop_distance_atr: Decimal = Field(default=Decimal("3"), gt=0, le=Decimal("10"))
    reward_risk_multiple: Decimal = Field(default=Decimal("2"), ge=Decimal("1"), le=Decimal("10"))
    min_reward_risk_multiple: Decimal = Field(
        default=Decimal("1.5"), ge=Decimal("1"), le=Decimal("10")
    )

    # Execution costs. Position sizing must charge these against the risk budget, otherwise
    # realized risk per trade exceeds account_risk_per_trade by the round-trip cost.
    taker_fee_bps: Decimal = Field(default=Decimal("5"), ge=0, le=Decimal("100"))
    stop_slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=Decimal("200"))

    # Market structure. `right` bars of forward confirmation means structure lags by that
    # many closed bars; it is the cost of pivots that do not repaint.
    swing_pivot_left: int = Field(default=2, ge=1, le=10)
    swing_pivot_right: int = Field(default=2, ge=1, le=10)
    require_structure_alignment: bool = True

    # Exit management. Scale out at +1R, move the stop to breakeven at the same time, then
    # trail the remainder behind confirmed structure.
    partial_take_profit_r: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("5"))
    partial_take_profit_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=Decimal("0.9"))
    breakeven_buffer_atr: Decimal = Field(default=Decimal("0.05"), ge=0, le=Decimal("1"))
    trail_activation_r: Decimal = Field(default=Decimal("2"), gt=0, le=Decimal("10"))
    trail_buffer_atr: Decimal = Field(default=Decimal("0.25"), ge=0, le=Decimal("3"))

    state_db_path: Path = Path("atlas_state.sqlite3")
    research_retention_days: int = Field(default=730, ge=30, le=3650)
    event_retention_days: int = Field(default=730, ge=30, le=3650)
    research_report_lookback_days: int = Field(default=180, ge=30, le=730)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("excluded_base_assets", mode="before")
    @classmethod
    def parse_excluded_assets(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().upper() for item in value.split(",") if item.strip())
        return value

    @field_validator("ignored_spot_balance_assets", mode="before")
    @classmethod
    def parse_ignored_spot_balance_assets(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("signal_discovery_interval_hours")
    @classmethod
    def validate_screening_interval(cls, value: int) -> int:
        if 24 % value != 0:
            raise ValueError("scheduler intervals must divide evenly into 24 hours")
        return value

    @model_validator(mode="after")
    def validate_live_interlock(self) -> Settings:
        if (
            self.screening_interval_hours is not None
            and "execution_cooldown_hours" not in self.model_fields_set
        ):
            self.execution_cooldown_hours = self.screening_interval_hours
        if self.max_structural_stop_distance_atr < self.atr_stop_multiple:
            raise ValueError(
                "maximum structural stop distance must be at least the ATR stop multiple"
            )
        if self.min_reward_risk_multiple > self.reward_risk_multiple:
            raise ValueError(
                "minimum reward/risk multiple cannot exceed the planned reward/risk multiple"
            )
        if self.trail_activation_r < self.partial_take_profit_r:
            raise ValueError("trailing must activate at or after the partial take-profit level")
        if self.min_volatility_percentile >= self.max_volatility_percentile:
            raise ValueError(
                "minimum volatility percentile must be below maximum volatility percentile"
            )
        if self.rsi_divergence_max_age_hours > self.rsi_divergence_lookback_hours:
            raise ValueError("RSI divergence maximum age cannot exceed its lookback")
        if self.early_retest_hold_buffer_atr > self.early_retest_touch_buffer_atr:
            raise ValueError("early retest hold buffer cannot exceed the touch buffer")
        if self.research_report_lookback_days > self.research_retention_days:
            raise ValueError("research report lookback cannot exceed research retention")
        if self.environment is Environment.PRODUCTION and "*" in self.allowed_hosts:
            raise ValueError("production allowed hosts cannot contain a wildcard")
        if self.execution_mode is not ExecutionMode.LIVE:
            return self
        if self.strategy_profile is not StrategyProfile.LEGACY_MOMENTUM:
            raise ValueError("research-only strategy profiles cannot be selected in live mode")
        if not self.live_trading_enabled:
            raise ValueError("live mode requires ATLAS_LIVE_TRADING_ENABLED=true")
        if self.live_trading_ack.get_secret_value() != LIVE_ACKNOWLEDGEMENT:
            raise ValueError("live mode requires the exact live-trading acknowledgement")
        if self.environment is not Environment.PRODUCTION:
            raise ValueError("live mode requires ATLAS_ENVIRONMENT=production")
        return self

    @property
    def strategy_shorts_enabled(self) -> bool:
        """Return the explicit short authority for the selected strategy profile."""
        profile = self.strategy_profile
        if profile is StrategyProfile.LEGACY_MOMENTUM:
            return True
        if profile in {StrategyProfile.REGIME_BREAKOUT, StrategyProfile.TREND_QUALITY}:
            return self.breakout_shorts_enabled
        if profile is StrategyProfile.SLOW_TREND:
            return self.slow_momentum_shorts_enabled
        if profile is StrategyProfile.EMPIRICAL_TREND:
            return self.empirical_shorts_enabled
        if profile is StrategyProfile.LAYERED_MOMENTUM:
            return self.layered_shorts_enabled
        if profile is StrategyProfile.EARLY_MOMENTUM:
            return self.early_momentum_shorts_enabled
        return False

    @property
    def required_opposing_room_atr(self) -> Decimal:
        """ATR distance to the opposing level below which a trade can never clear min RR.

        The stop is at least ``atr_stop_multiple`` ATR away, and the target is capped at
        ``protective_level_buffer_atr`` ATR before the opposing level. So clearing
        ``min_reward_risk_multiple`` requires::

            opposing - entry >= (min_rr * atr_stop_multiple + protective_buffer) * ATR

        Screening on the raw ``key_level_buffer_atr`` instead admits candidates that score,
        persist, and then fail bracket construction every single time. Gating on the derived
        distance keeps the screener and the strategy consistent by construction.
        """
        derived = (
            self.min_reward_risk_multiple * self.atr_stop_multiple
            + self.protective_level_buffer_atr
        )
        return max(self.key_level_buffer_atr, derived)

    def require_operator_token(self) -> str:
        token = self.operator_token.get_secret_value()
        if len(token) < 32:
            raise ConfigurationError("ATLAS_OPERATOR_TOKEN must contain at least 32 characters")
        return token


def load_settings() -> Settings:
    return Settings()
