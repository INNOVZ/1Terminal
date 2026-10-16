from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from math import sqrt
from statistics import mean, stdev
from uuid import uuid4

from algotrader.application.position_manager import PositionManager, TakePartialProfit
from algotrader.application.risk import PortfolioRiskManager
from algotrader.application.screener import MultiFactorScreener
from algotrader.application.strategy import TrendMomentumStrategy
from algotrader.config import Settings
from algotrader.domain.fills import bar_limit_fill_price
from algotrader.domain.indicators import atr as atr_series
from algotrader.domain.models import (
    AccountSnapshot,
    Candle,
    Direction,
    Instrument,
    InstrumentId,
    ManagementStage,
    MarketKind,
    PositionManagementState,
    Quote,
    Venue,
)
from algotrader.domain.precision import capped_entry_limit_price
from algotrader.domain.structure import classify


@dataclass(frozen=True, slots=True)
class BacktestAssumptions:
    initial_equity: Decimal = Decimal("10000")
    screening_spread_bps: Decimal = Decimal("10")
    spread_bps: Decimal = Decimal("10")
    fee_bps_per_side: Decimal = Decimal("5")
    stop_slippage_bps: Decimal = Decimal("5")
    funding_bps_per_8h: Decimal = Decimal("1")
    manage_exits: bool = True
    """Run the live exit policy (scale out, breakeven, structural trail) during replay.

    Set ``False`` to replay the same signals under the old static bracket. Running both and
    diffing is the only honest way to decide whether the exit policy pays for itself, rather
    than assuming a trailing stop must be an improvement.
    """
    stagnation_exit_bars: int | None = None
    stagnation_min_favorable_r: Decimal = Decimal("0.5")

    def __post_init__(self) -> None:
        if self.stagnation_exit_bars is not None and self.stagnation_exit_bars < 2:
            raise ValueError("stagnation exit must wait at least two bars")
        if self.stagnation_min_favorable_r <= 0:
            raise ValueError("stagnation progress threshold must be positive")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    direction: Direction
    signal_at: datetime
    opened_at: datetime
    closed_at: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    initial_risk: Decimal
    exit_reason: str
    scaled_out_quantity: Decimal = Decimal("0")
    scaled_out_price: Decimal = Decimal("0")
    final_stop_price: Decimal = Decimal("0")
    max_favorable_excursion_r: Decimal = Decimal("0")
    max_adverse_excursion_r: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class TradeSimulationRequest:
    direction: Direction
    signal_at: datetime
    stop_price: Decimal
    target_price: Decimal
    reference_price: Decimal
    signal_bar_close: Decimal
    atr: Decimal
    tick_size: Decimal
    quantity: Decimal
    quantity_step: Decimal
    bars: list[Candle]
    entry_index: int
    market_kind: MarketKind


@dataclass(frozen=True, slots=True)
class BacktestResult:
    interval_hours: int
    started_at: datetime
    ended_at: datetime
    observation_days: Decimal
    initial_equity: Decimal
    final_equity: Decimal
    trades: tuple[BacktestTrade, ...]
    win_rate: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    daily_sharpe: Decimal | None
    diagnostics: dict[str, int]

    @property
    def total_return(self) -> Decimal:
        if self.initial_equity <= 0:
            return Decimal("0")
        return (self.final_equity - self.initial_equity) / self.initial_equity

    @property
    def trades_per_24h(self) -> Decimal:
        if self.observation_days <= 0:
            return Decimal("0")
        return Decimal(len(self.trades)) / self.observation_days

    @property
    def expectancy(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return sum((trade.net_pnl for trade in self.trades), Decimal("0")) / Decimal(
            len(self.trades)
        )

    @property
    def expectancy_r(self) -> Decimal | None:
        outcomes = [
            trade.net_pnl / trade.initial_risk for trade in self.trades if trade.initial_risk > 0
        ]
        if not outcomes:
            return None
        return sum(outcomes, Decimal("0")) / Decimal(len(outcomes))

    @property
    def payoff_ratio(self) -> Decimal | None:
        wins = [trade.net_pnl for trade in self.trades if trade.net_pnl > 0]
        losses = [-trade.net_pnl for trade in self.trades if trade.net_pnl < 0]
        if not wins or not losses:
            return None
        average_win = sum(wins, Decimal("0")) / Decimal(len(wins))
        average_loss = sum(losses, Decimal("0")) / Decimal(len(losses))
        return average_win / average_loss if average_loss > 0 else None

    @property
    def average_holding_hours(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        total = sum(
            (
                Decimal(str((trade.closed_at - trade.opened_at).total_seconds())) / Decimal("3600")
                for trade in self.trades
            ),
            Decimal("0"),
        )
        return total / Decimal(len(self.trades))

    @property
    def average_favorable_excursion_r(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return sum(
            (trade.max_favorable_excursion_r for trade in self.trades), Decimal("0")
        ) / Decimal(len(self.trades))

    @property
    def average_adverse_excursion_r(self) -> Decimal:
        if not self.trades:
            return Decimal("0")
        return sum(
            (trade.max_adverse_excursion_r for trade in self.trades), Decimal("0")
        ) / Decimal(len(self.trades))

    def _direction_summary(self, direction: Direction) -> dict[str, object]:
        trades = [trade for trade in self.trades if trade.direction is direction]
        wins = sum(trade.net_pnl > 0 for trade in trades)
        return {
            "trade_count": len(trades),
            "win_rate": str(Decimal(wins) / Decimal(len(trades))) if trades else "0",
            "net_pnl": str(sum((trade.net_pnl for trade in trades), Decimal("0"))),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "interval_hours": self.interval_hours,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "observation_days": str(self.observation_days),
            "initial_equity": str(self.initial_equity),
            "final_equity": str(self.final_equity),
            "total_return": str(self.total_return),
            "trade_count": len(self.trades),
            "trades_per_24h": str(self.trades_per_24h),
            "win_rate": str(self.win_rate),
            "profit_factor": str(self.profit_factor) if self.profit_factor is not None else None,
            "payoff_ratio": str(self.payoff_ratio) if self.payoff_ratio is not None else None,
            "expectancy": str(self.expectancy),
            "expectancy_r": str(self.expectancy_r) if self.expectancy_r is not None else None,
            "average_holding_hours": str(self.average_holding_hours),
            "average_favorable_excursion_r": str(self.average_favorable_excursion_r),
            "average_adverse_excursion_r": str(self.average_adverse_excursion_r),
            "never_reached_half_r_count": sum(
                trade.max_favorable_excursion_r < Decimal("0.5") for trade in self.trades
            ),
            "reached_one_r_count": sum(
                trade.max_favorable_excursion_r >= Decimal("1") for trade in self.trades
            ),
            "scaled_out_trade_count": sum(trade.scaled_out_quantity > 0 for trade in self.trades),
            "exit_reasons": {
                reason: sum(trade.exit_reason == reason for trade in self.trades)
                for reason in sorted({trade.exit_reason for trade in self.trades})
            },
            "directions": {
                direction.value: self._direction_summary(direction) for direction in Direction
            },
            "max_drawdown": str(self.max_drawdown),
            "drawdown_basis": "closed-trade equity; excludes intratrade excursions",
            "daily_sharpe": str(self.daily_sharpe) if self.daily_sharpe is not None else None,
            "diagnostics": self.diagnostics,
        }


STRUCTURE_WINDOW_BARS = 200
"""Trailing window for swing detection. Bounds replay cost and matches how far back a
structural stop is plausibly anchored."""


class StrategyBacktester:
    """Closed-candle replay that shares Atlas's screener, strategy, risk, and price caps."""

    def __init__(self, settings: Settings, assumptions: BacktestAssumptions) -> None:
        self._settings = settings
        self._assumptions = assumptions
        self._screener = MultiFactorScreener(settings)
        self._strategy = TrendMomentumStrategy(settings)
        self._risk = PortfolioRiskManager(settings)
        self._position_manager = PositionManager(settings)

    def compare_intervals(
        self,
        instrument: Instrument,
        one_hour: list[Candle],
        four_hour: list[Candle],
        daily: list[Candle] | None = None,
        benchmark_one_hour: list[Candle] | None = None,
        intervals: tuple[int, ...] = (1, 4, 8),
    ) -> tuple[BacktestResult, ...]:
        return tuple(
            self.run(
                instrument,
                one_hour,
                four_hour,
                daily=daily,
                benchmark_one_hour=benchmark_one_hour,
                interval_hours=interval,
            )
            for interval in intervals
        )

    def run(
        self,
        instrument: Instrument,
        one_hour: list[Candle],
        four_hour: list[Candle],
        *,
        daily: list[Candle] | None = None,
        benchmark_one_hour: list[Candle] | None = None,
        interval_hours: int,
    ) -> BacktestResult:
        if interval_hours <= 0 or 24 % interval_hours != 0:
            raise ValueError("backtest interval must divide evenly into 24 hours")
        bars = sorted((bar for bar in one_hour if bar.closed), key=lambda bar: bar.closed_at)
        higher_bars = sorted(
            (bar for bar in four_hour if bar.closed), key=lambda bar: bar.closed_at
        )
        daily_bars = sorted(
            (bar for bar in (daily or []) if bar.closed), key=lambda bar: bar.closed_at
        )
        if len(bars) < 261:
            raise ValueError("backtest requires at least 261 closed 1h candles")

        equity = self._assumptions.initial_equity
        peak_equity = equity
        trades: list[BacktestTrade] = []
        next_available_index = 260
        last_opened_at: datetime | None = None
        diagnostics = {
            "evaluated_bars": 0,
            "aligned_4h_bars": 0,
            "eligible_candidates": 0,
            "signals_constructed": 0,
            "risk_approved": 0,
            "entries_filled": 0,
        }
        for index in range(259, len(bars) - 1):
            if index < next_available_index:
                continue
            signal_bar = bars[index]
            if last_opened_at is not None and signal_bar.closed_at < last_opened_at + timedelta(
                hours=interval_hours
            ):
                continue
            four_history = [bar for bar in higher_bars if bar.closed_at <= signal_bar.closed_at]
            if len(four_history) < self._settings.min_4h_history_bars:
                continue
            one_history = bars[max(0, index - 259) : index + 1]
            four_limit = min(1000, self._settings.min_4h_history_bars + 5)
            four_history = four_history[-four_limit:]
            diagnostics["evaluated_bars"] += 1
            if four_history[-1].closed_at == signal_bar.closed_at:
                diagnostics["aligned_4h_bars"] += 1
            daily_history = [bar for bar in daily_bars if bar.closed_at <= signal_bar.closed_at]
            daily_history = daily_history[-260:]
            benchmark_history = [
                bar
                for bar in (benchmark_one_hour or [])
                if bar.closed and bar.closed_at <= signal_bar.closed_at
            ][-260:]
            quote = self._historical_quote(instrument, one_history)
            if (
                quote.quote_volume_24h < self._settings.min_quote_volume_24h
                or quote.spread_bps > self._settings.max_spread_bps
            ):
                continue
            evaluated_candidates = self._screener.evaluate_history(
                instrument,
                quote,
                one_history,
                four_history,
                daily=daily_history,
                benchmark_one_hour=benchmark_history,
            )
            for candidate in evaluated_candidates:
                if candidate.score > 0:
                    continue
                for reason in candidate.reasons:
                    if reason.startswith("entry blocked: "):
                        key = "rejected:" + reason.removeprefix("entry blocked: ")
                        diagnostics[key] = diagnostics.get(key, 0) + 1
            candidates = [
                candidate
                for candidate in evaluated_candidates
                if candidate.score >= self._settings.min_candidate_score
            ]
            diagnostics["eligible_candidates"] += len(candidates)
            if not candidates:
                continue
            signal = None
            for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
                try:
                    signal = self._strategy.signal(candidate)
                    break
                except ValueError:
                    continue
            if signal is None:
                continue
            diagnostics["signals_constructed"] += 1
            account = AccountSnapshot(
                venue=instrument.id.venue,
                equity=equity,
                available_balance=equity,
                realized_pnl_today=Decimal("0"),
                peak_equity=peak_equity,
            )
            limit_price = capped_entry_limit_price(
                direction=signal.direction,
                reference_price=signal.entry_price,
                signal_bar_close=signal.signal_bar_close,
                atr=signal.atr,
                tick_size=signal.instrument.price_tick,
                offset_bps=self._settings.entry_limit_offset_bps,
                max_entry_chase_atr=self._settings.max_entry_chase_atr,
            )
            decision = self._risk.evaluate(
                signal,
                account,
                account,
                executable_entry_price=limit_price,
            )
            if not decision.approved:
                continue
            diagnostics["risk_approved"] += 1
            trade, exit_index = self._simulate_trade(
                TradeSimulationRequest(
                    direction=signal.direction,
                    signal_at=signal.created_at,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    reference_price=signal.entry_price,
                    signal_bar_close=signal.signal_bar_close,
                    atr=signal.atr,
                    tick_size=signal.instrument.price_tick,
                    quantity=decision.quantity,
                    quantity_step=signal.instrument.quantity_step,
                    bars=bars,
                    entry_index=index + 1,
                    market_kind=instrument.market_kind,
                )
            )
            if trade is None:
                continue
            diagnostics["entries_filled"] += 1
            trades.append(trade)
            equity += trade.net_pnl
            peak_equity = max(peak_equity, equity)
            next_available_index = exit_index + 1
            last_opened_at = trade.opened_at
            if equity <= 0:
                break
        return self._result(
            interval_hours,
            trades,
            started_at=bars[259].closed_at,
            ended_at=bars[-1].closed_at,
            diagnostics=diagnostics,
        )

    def _historical_quote(self, instrument: Instrument, history: list[Candle]) -> Quote:
        close = history[-1].close
        # Eligibility is an observed market property. Cost stress must affect simulated
        # fills, not rewrite the historical quote and silently remove the whole universe.
        half_spread = self._assumptions.screening_spread_bps / Decimal("20000")
        quote_volume = sum(
            (
                bar.quote_volume if bar.quote_volume > 0 else bar.close * bar.volume
                for bar in history[-24:]
            ),
            Decimal("0"),
        )
        return Quote(
            instrument_id=instrument.id,
            bid=close * (Decimal("1") - half_spread),
            ask=close * (Decimal("1") + half_spread),
            last=close,
            quote_volume_24h=quote_volume,
            observed_at=history[-1].closed_at,
        )

    def _simulate_trade(self, request: TradeSimulationRequest) -> tuple[BacktestTrade | None, int]:
        direction = request.direction
        bars = request.bars
        entry_index = request.entry_index
        limit_price = capped_entry_limit_price(
            direction=direction,
            reference_price=request.reference_price,
            signal_bar_close=request.signal_bar_close,
            atr=request.atr,
            tick_size=request.tick_size,
            offset_bps=self._settings.entry_limit_offset_bps,
            max_entry_chase_atr=self._settings.max_entry_chase_atr,
        )
        entry_bar = bars[entry_index]
        fill_price = bar_limit_fill_price(
            direction=direction,
            limit_price=limit_price,
            bar=entry_bar,
            spread_bps=self._assumptions.spread_bps,
        )
        if fill_price is None:
            return None, entry_index
        state = PositionManagementState(
            signal_id=uuid4(),
            instrument_id=InstrumentId(Venue.PAPER, "BACKTEST"),
            direction=direction,
            stage=ManagementStage.INITIAL,
            entry_price=fill_price,
            initial_stop_price=request.stop_price,
            initial_quantity=request.quantity,
            remaining_quantity=request.quantity,
            current_stop_price=request.stop_price,
            target_price=request.target_price,
            best_price=fill_price,
        )
        scaled_quantity = Decimal("0")
        scaled_price = Decimal("0")
        exit_price = bars[-1].close
        exit_reason = "end_of_data"
        exit_index = len(bars) - 1
        slippage = self._assumptions.stop_slippage_bps / Decimal("10000")
        initial_risk_per_unit = abs(fill_price - request.stop_price)
        max_favorable_excursion_r = Decimal("0")
        max_adverse_excursion_r = Decimal("0")

        for index in range(entry_index, len(bars)):
            bar = bars[index]
            # The stop that protects this bar is the one that was in force when the bar
            # opened. Applying a stop derived from this bar's own close would be look-ahead.
            stop_price = state.current_stop_price
            target_active = state.stage is ManagementStage.INITIAL
            if direction is Direction.LONG:
                stop_hit = bar.low <= stop_price
                target_hit = target_active and bar.high >= request.target_price
                if stop_hit:
                    exit_price = stop_price * (Decimal("1") - slippage)
                    exit_reason = "stop"
                elif target_hit:
                    exit_price = request.target_price
                    exit_reason = "target"
            else:
                stop_hit = bar.high >= stop_price
                target_hit = target_active and bar.low <= request.target_price
                if stop_hit:
                    exit_price = stop_price * (Decimal("1") + slippage)
                    exit_reason = "stop"
                elif target_hit:
                    exit_price = request.target_price
                    exit_reason = "target"
            if stop_hit or target_hit:
                if initial_risk_per_unit > 0:
                    if stop_hit:
                        adverse = (
                            fill_price - min(fill_price, stop_price)
                            if direction is Direction.LONG
                            else max(fill_price, stop_price) - fill_price
                        )
                        max_adverse_excursion_r = max(
                            max_adverse_excursion_r, adverse / initial_risk_per_unit
                        )
                    else:
                        favorable = abs(request.target_price - fill_price)
                        max_favorable_excursion_r = max(
                            max_favorable_excursion_r, favorable / initial_risk_per_unit
                        )
                exit_index = index
                break

            if initial_risk_per_unit > 0:
                # The fill can occur anywhere inside the entry candle, so its pre-fill
                # high/low ordering is unknowable. Use its close as a conservative bound;
                # from the next candle onward the full excursion belongs to the open trade.
                if index == entry_index:
                    favorable_price = adverse_price = bar.close
                elif direction is Direction.LONG:
                    favorable_price, adverse_price = bar.high, bar.low
                else:
                    favorable_price, adverse_price = bar.low, bar.high
                favorable = (
                    favorable_price - fill_price
                    if direction is Direction.LONG
                    else fill_price - favorable_price
                )
                adverse = (
                    fill_price - adverse_price
                    if direction is Direction.LONG
                    else adverse_price - fill_price
                )
                max_favorable_excursion_r = max(
                    max_favorable_excursion_r,
                    max(Decimal("0"), favorable) / initial_risk_per_unit,
                )
                max_adverse_excursion_r = max(
                    max_adverse_excursion_r,
                    max(Decimal("0"), adverse) / initial_risk_per_unit,
                )

            stagnation_bars = self._assumptions.stagnation_exit_bars
            bars_held = index - entry_index + 1
            if (
                stagnation_bars is not None
                and bars_held >= stagnation_bars
                and state.stage is ManagementStage.INITIAL
                and max_favorable_excursion_r < self._assumptions.stagnation_min_favorable_r
            ):
                half_spread = self._assumptions.spread_bps / Decimal("20000")
                exit_price = (
                    bar.close * (Decimal("1") - half_spread)
                    if direction is Direction.LONG
                    else bar.close * (Decimal("1") + half_spread)
                )
                exit_reason = "stagnation"
                exit_index = index
                break

            if not self._assumptions.manage_exits:
                continue
            # Management acts on closed-bar information only, matching the live scheduler.
            window = bars[max(0, index - STRUCTURE_WINDOW_BARS + 1) : index + 1]
            plan = self._position_manager.plan(
                state,
                mark_price=bar.close,
                atr=self._atr_at(window),
                quantity_step=request.quantity_step,
                tick_size=request.tick_size,
                structure=classify(
                    window,
                    left=self._settings.swing_pivot_left,
                    right=self._settings.swing_pivot_right,
                ),
            )
            for action in plan.actions:
                if isinstance(action, TakePartialProfit):
                    scaled_quantity += action.quantity
                    scaled_price = bar.close
            state = plan.state

        opened_at = bars[entry_index].opened_at
        closed_at = bars[exit_index].closed_at
        multiplier = Decimal("1") if direction is Direction.LONG else Decimal("-1")
        runner_quantity = request.quantity - scaled_quantity
        gross_pnl = (exit_price - fill_price) * runner_quantity * multiplier + (
            scaled_price - fill_price
        ) * scaled_quantity * multiplier
        entry_notional = fill_price * request.quantity
        exit_notional = exit_price * runner_quantity + scaled_price * scaled_quantity
        fees = (
            (entry_notional + exit_notional) * self._assumptions.fee_bps_per_side / Decimal("10000")
        )
        funding = Decimal("0")
        if request.market_kind is MarketKind.FUTURES:
            held_hours = Decimal(str((closed_at - opened_at).total_seconds())) / Decimal("3600")
            periods = (held_hours / Decimal("8")).to_integral_value(rounding=ROUND_CEILING)
            funding = (
                entry_notional * self._assumptions.funding_bps_per_8h / Decimal("10000") * periods
            )
        trade = BacktestTrade(
            direction=direction,
            signal_at=request.signal_at,
            opened_at=opened_at,
            closed_at=closed_at,
            quantity=request.quantity,
            entry_price=fill_price,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            fees=fees,
            funding=funding,
            net_pnl=gross_pnl - fees - funding,
            initial_risk=abs(fill_price - request.stop_price) * request.quantity,
            exit_reason=exit_reason,
            scaled_out_quantity=scaled_quantity,
            scaled_out_price=scaled_price,
            final_stop_price=state.current_stop_price,
            max_favorable_excursion_r=max_favorable_excursion_r,
            max_adverse_excursion_r=max_adverse_excursion_r,
        )
        return trade, exit_index

    @staticmethod
    def _atr_at(window: list[Candle]) -> Decimal:
        values = atr_series(window, 14)
        return values[-1] if values else Decimal("0")

    def _result(
        self,
        interval_hours: int,
        trades: list[BacktestTrade],
        *,
        started_at: datetime,
        ended_at: datetime,
        diagnostics: dict[str, int] | None = None,
    ) -> BacktestResult:
        equity = self._assumptions.initial_equity
        peak = equity
        max_drawdown = Decimal("0")
        wins = Decimal("0")
        gross_profit = Decimal("0")
        gross_loss = Decimal("0")
        for trade in trades:
            equity += trade.net_pnl
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
            if trade.net_pnl > 0:
                wins += 1
                gross_profit += trade.net_pnl
            elif trade.net_pnl < 0:
                gross_loss += -trade.net_pnl
        win_rate = wins / Decimal(len(trades)) if trades else Decimal("0")
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        observation_days = max(
            Decimal("1"),
            Decimal(str((ended_at - started_at).total_seconds())) / Decimal("86400"),
        )
        return BacktestResult(
            interval_hours=interval_hours,
            started_at=started_at,
            ended_at=ended_at,
            observation_days=observation_days,
            initial_equity=self._assumptions.initial_equity,
            final_equity=equity,
            trades=tuple(trades),
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            daily_sharpe=self._daily_sharpe(
                trades,
                first_day=started_at.astimezone(UTC).date(),
                last_day=ended_at.astimezone(UTC).date(),
            ),
            diagnostics=diagnostics or {},
        )

    def _daily_sharpe(
        self, trades: list[BacktestTrade], *, first_day: date, last_day: date
    ) -> Decimal | None:
        pnl_by_day: dict[date, Decimal] = {}
        for trade in trades:
            day = trade.closed_at.astimezone(UTC).date()
            pnl_by_day[day] = pnl_by_day.get(day, Decimal("0")) + trade.net_pnl
        equity = self._assumptions.initial_equity
        returns: list[float] = []
        day = first_day
        while day <= last_day:
            previous = equity
            equity += pnl_by_day.get(day, Decimal("0"))
            returns.append(float((equity - previous) / previous) if previous > 0 else 0.0)
            day += timedelta(days=1)
        if len(returns) < 2 or stdev(returns) == 0:
            return None
        return Decimal(str(mean(returns) / stdev(returns) * sqrt(365)))
