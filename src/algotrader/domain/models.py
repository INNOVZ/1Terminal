from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    FUTURE = "future"


class Venue(StrEnum):
    BINANCE_SPOT = "binance_spot"
    BINANCE_USDM = "binance_usdm"
    IBKR_PAPER = "ibkr_paper"
    PAPER = "paper"


class MarketKind(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"
    SECURITIES = "securities"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"


class MomentumState(StrEnum):
    GAINING = "gaining"
    LOSING = "losing"
    MIXED = "mixed"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class ProtectionRole(StrEnum):
    STOP = "stop"
    TARGET = "target"


class TradeStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class TrackedSignalStatus(StrEnum):
    ACTIVE = "active"
    EXECUTED = "executed"
    EXPIRED = "expired"


class ExecutionStage(StrEnum):
    INTENT_PERSISTED = "intent_persisted"
    ENTRY_FILLED = "entry_filled"
    STOP_CONFIRMED = "stop_confirmed"
    TARGET_CONFIRMED = "target_confirmed"
    TRACKED = "tracked"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class InstrumentId:
    venue: Venue
    symbol: str

    def __str__(self) -> str:
        return f"{self.venue.value}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class Instrument:
    id: InstrumentId
    base_asset: str
    quote_asset: str
    asset_class: AssetClass
    market_kind: MarketKind
    trading: bool
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    max_leverage: int = 1

    @property
    def supports_short(self) -> bool:
        return self.market_kind in {MarketKind.FUTURES, MarketKind.SECURITIES}


@dataclass(frozen=True, slots=True)
class Candle:
    opened_at: datetime
    closed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal = Decimal("0")
    closed: bool = True
    taker_buy_volume: Decimal = Decimal("0")
    taker_buy_quote_volume: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class Quote:
    instrument_id: InstrumentId
    bid: Decimal
    ask: Decimal
    last: Decimal
    quote_volume_24h: Decimal
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    high_24h: Decimal | None = None
    low_24h: Decimal | None = None
    price_change_pct_24h: Decimal | None = None
    funding_rate: Decimal | None = None
    basis_pct: Decimal | None = None

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        if mid <= 0:
            return Decimal("Infinity")
        return (self.ask - self.bid) / mid * Decimal("10000")

    @property
    def range_24h_pct(self) -> Decimal | None:
        if self.high_24h is not None and self.low_24h is not None and self.last > 0:
            return (self.high_24h - self.low_24h) / self.last
        return None


@dataclass(frozen=True, slots=True)
class Candidate:
    instrument: Instrument
    direction: Direction
    score: Decimal
    entry_price: Decimal
    signal_bar_close: Decimal
    atr: Decimal
    spread_bps: Decimal
    quote_volume_24h: Decimal
    momentum_state: MomentumState
    reasons: tuple[str, ...]
    rsi_1h: Decimal = Decimal("50")
    rsi_4h: Decimal = Decimal("50")
    support_price: Decimal | None = None
    resistance_price: Decimal | None = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    signal_bar_closed_at: datetime | None = None
    momentum_reference_closed_at: datetime | None = None
    expires_at: datetime | None = None
    momentum_reference_close: Decimal | None = None
    macd_hist: Decimal | None = None
    momentum_reference_macd_hist: Decimal | None = None
    momentum_reference_rsi_1h: Decimal | None = None
    adx_1h: Decimal | None = None
    momentum_reference_adx_1h: Decimal | None = None
    ema9_1h: Decimal | None = None
    ema20_1h: Decimal | None = None
    ema50_1h: Decimal | None = None
    ema200_1h: Decimal | None = None
    choppiness_1h: Decimal | None = None
    macd_cross_age_bars: int | None = None
    momentum_normalized_1h: Decimal | None = None
    ema20_4h: Decimal | None = None
    ema50_4h: Decimal | None = None
    ema200_4h: Decimal | None = None
    relative_volume: Decimal | None = None
    structure_trend: str | None = None
    structure_reasons: tuple[str, ...] = ()
    levels_from_structure: bool = False
    continuation_trigger_passed: bool = False
    continuation_trigger_evidence: tuple[str, ...] = ()
    continuation_trigger_failures: tuple[str, ...] = ()
    momentum_lookback_bars: int = 3
    strategy_profile: str = "legacy_momentum_v1"
    market_regime: str = "legacy_trend"
    breakout_level: Decimal | None = None
    return_7d: Decimal | None = None
    return_30d: Decimal | None = None
    return_90d: Decimal | None = None
    return_180d: Decimal | None = None
    return_6h: Decimal | None = None
    return_24h: Decimal | None = None
    volatility_percentile: Decimal | None = None
    cross_sectional_rank: Decimal | None = None
    indicator_confirmations: tuple[str, ...] = ()
    indicator_conflicts: tuple[str, ...] = ()
    macd_cross_1h: str | None = None
    rsi_divergence_1h: str | None = None
    setup_signal: str | None = None
    setup_strength: Decimal | None = None
    aligned_momentum_horizons: int | None = None
    directional_momentum_score: Decimal | None = None
    technical_quality_score: Decimal | None = None
    breakout_relative_volume: Decimal | None = None
    breakout_age_bars: int | None = None
    funding_rate: Decimal | None = None
    basis_rate: Decimal | None = None
    open_interest: Decimal | None = None
    open_interest_change_1h: Decimal | None = None
    taker_buy_sell_ratio: Decimal | None = None
    bid_depth_notional: Decimal | None = None
    ask_depth_notional: Decimal | None = None
    depth_imbalance: Decimal | None = None
    btc_beta: Decimal | None = None
    btc_correlation: Decimal | None = None
    alt_momentum_phase: str | None = None
    residual_return_6h: Decimal | None = None
    residual_return_24h: Decimal | None = None
    residual_return_7d: Decimal | None = None
    momentum_acceleration_6h: Decimal | None = None
    volume_participation_6h: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    """Point-in-time public derivatives data used only as observable research features."""

    funding_rate: Decimal | None
    basis_rate: Decimal
    open_interest: Decimal
    open_interest_change_1h: Decimal | None
    taker_buy_sell_ratio: Decimal
    bid_depth_notional: Decimal
    ask_depth_notional: Decimal
    open_interest_change_4h: Decimal | None = None
    open_interest_change_24h: Decimal | None = None
    open_interest_change_7d: Decimal | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    degraded_telemetry: bool = False

    @property
    def depth_imbalance(self) -> Decimal:
        total = self.bid_depth_notional + self.ask_depth_notional
        if total <= 0:
            return Decimal("0")
        return (self.bid_depth_notional - self.ask_depth_notional) / total


@dataclass(frozen=True, slots=True)
class OpportunityObservation:
    """One direction-specific hourly research row, including rejected opportunities."""

    instrument_id: InstrumentId
    direction: Direction
    strategy_profile: str
    signal_bar_closed_at: datetime
    observed_at: datetime
    eligible: bool
    score: Decimal
    entry_price: Decimal
    features: Mapping[str, str | int | float | bool | None]
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TradeSignal:
    instrument: Instrument
    direction: Direction
    score: Decimal
    entry_price: Decimal
    signal_bar_close: Decimal
    atr: Decimal
    stop_price: Decimal
    target_price: Decimal
    strategy: str
    timeframe: str
    momentum_state: MomentumState
    rationale: tuple[str, ...]
    rsi_1h: Decimal = Decimal("50")
    rsi_4h: Decimal = Decimal("50")
    support_price: Decimal | None = None
    resistance_price: Decimal | None = None
    continuation_trigger_passed: bool = True
    btc_beta: Decimal | None = None
    btc_correlation: Decimal | None = None
    signal_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    signal_bar_closed_at: datetime | None = None
    expires_at: datetime | None = None

    @property
    def risk_per_unit(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_per_unit(self) -> Decimal:
        return abs(self.target_price - self.entry_price)

    @property
    def reward_risk_ratio(self) -> Decimal:
        if self.risk_per_unit <= 0:
            return Decimal("0")
        return self.reward_per_unit / self.risk_per_unit


@dataclass(frozen=True, slots=True)
class TrackedSignal:
    signal_id: UUID
    instrument_id: InstrumentId
    direction: Direction
    first_seen_at: datetime
    last_seen_at: datetime
    signal_bar_closed_at: datetime
    expires_at: datetime
    observations: int
    score: Decimal
    entry_price: Decimal
    status: TrackedSignalStatus = TrackedSignalStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class UnresolvedOrderIntent:
    instrument_id: InstrumentId
    client_order_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionLifecycle:
    signal_id: UUID
    instrument_id: InstrumentId
    direction: Direction
    client_order_id: str
    stage: ExecutionStage
    updated_at: datetime
    filled_quantity: Decimal = Decimal("0")
    fill_price: Decimal | None = None
    stop_order_id: str = ""
    target_order_id: str = ""


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: InstrumentId
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    venue: Venue
    equity: Decimal
    available_balance: Decimal
    realized_pnl_today: Decimal
    peak_equity: Decimal
    positions: tuple[Position, ...] = ()
    mark_to_market_pnl_today: Decimal = Decimal("0")
    day_start_equity: Decimal = Decimal("0")
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def drawdown(self) -> Decimal:
        if self.peak_equity <= 0:
            return Decimal("0")
        return max(Decimal("0"), (self.peak_equity - self.equity) / self.peak_equity)

    @property
    def daily_risk_base(self) -> Decimal:
        """Denominator for *daily* limits.

        Daily stops must be measured against the equity the day started with, not the
        all-time peak. Anchoring them to the peak keeps the dollar allowance fixed while
        equity falls, so the effective percentage stop widens exactly during a drawdown --
        the inverse of what a circuit breaker is for.
        """
        return self.day_start_equity if self.day_start_equity > 0 else self.equity


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    quantity: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")
    executable_entry_price: Decimal = Decimal("0")
    risk_per_unit: Decimal = Decimal("0")
    reward_risk_ratio: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    client_order_id: str
    direction: Direction
    time_in_force: TimeInForce | None = None
    reduce_only: bool = False
    price: Decimal | None = None
    stop_price: Decimal | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrderResult:
    venue: Venue
    symbol: str
    client_order_id: str
    exchange_order_id: str
    status: OrderStatus
    executed_quantity: Decimal
    average_price: Decimal | None
    raw_status: str


@dataclass(frozen=True, slots=True)
class ProtectionOrder:
    role: ProtectionRole
    result: OrderResult
    trigger_price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class ProtectionSet:
    stop: ProtectionOrder | None = None
    target: ProtectionOrder | None = None

    def __iter__(self) -> Iterator[OrderResult]:
        if self.stop is not None:
            yield self.stop.result
        if self.target is not None:
            yield self.target.result

    def __len__(self) -> int:
        return int(self.stop is not None) + int(self.target is not None)

    def __getitem__(self, index: int) -> OrderResult:
        return tuple(self)[index]


class ManagementStage(StrEnum):
    """Lifecycle of an open position's protective bracket."""

    INITIAL = "initial"
    PARTIAL_TAKEN = "partial_taken"
    TRAILING = "trailing"


@dataclass(frozen=True, slots=True)
class PositionManagementState:
    """Durable exit-management state for one open trade.

    ``initial_stop_price`` and ``initial_quantity`` are preserved for the life of the trade
    because every R multiple is measured against the risk taken *at entry*. Once the stop
    ratchets to breakeven the current stop no longer describes the original risk, so R
    cannot be recovered from the live bracket.
    """

    signal_id: UUID
    instrument_id: InstrumentId
    direction: Direction
    stage: ManagementStage
    entry_price: Decimal
    initial_stop_price: Decimal
    initial_quantity: Decimal
    remaining_quantity: Decimal
    current_stop_price: Decimal
    target_price: Decimal
    best_price: Decimal
    realized_partial_pnl: Decimal = Decimal("0")
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def initial_risk_per_unit(self) -> Decimal:
        return abs(self.entry_price - self.initial_stop_price)

    def r_multiple(self, price: Decimal) -> Decimal:
        risk = self.initial_risk_per_unit
        if risk <= 0:
            return Decimal("0")
        advance = (
            price - self.entry_price
            if self.direction is Direction.LONG
            else self.entry_price - price
        )
        return advance / risk

    def is_protective(self, stop_price: Decimal, reference_price: Decimal) -> bool:
        """A stop must sit behind the market, or submitting it closes the position."""
        return (
            stop_price < reference_price
            if self.direction is Direction.LONG
            else stop_price > reference_price
        )

    def tightens(self, stop_price: Decimal) -> bool:
        """True only when the stop moves toward price. The ratchet never runs backwards."""
        return (
            stop_price > self.current_stop_price
            if self.direction is Direction.LONG
            else stop_price < self.current_stop_price
        )


@dataclass(frozen=True, slots=True)
class TradeRecord:
    signal_id: UUID
    instrument_id: InstrumentId
    direction: Direction
    strategy: str
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    entry_order_id: str
    protection_order_ids: tuple[str, ...]
    opened_at: datetime
    status: TradeStatus = TradeStatus.OPEN
    exit_price: Decimal | None = None
    closed_at: datetime | None = None
    gross_realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    exit_reason: str = ""

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.gross_realized_pnl - self.fees


@dataclass(frozen=True, slots=True)
class VenueFill:
    """One executed trade as the venue reports it.

    Used to reconcile a close from exchange truth rather than operator recall.
    """

    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    commission: Decimal
    commission_asset: str
    realized_pnl: Decimal
    traded_at: datetime

    @property
    def is_sell(self) -> bool:
        return self.side.upper() == "SELL"
