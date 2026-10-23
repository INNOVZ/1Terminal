from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.external.models import (
    FearGreedReading,
    GlobalMarketMetrics,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.config import Settings, load_settings
from algotrader.domain.crypto_screener_models import CryptoOpportunity, SetupLabel
from algotrader.domain.indicators import (
    snapshot,
    taker_buy_ratio_from_candles,
)
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    Quote,
)
from algotrader.infrastructure.runtime import FUTURES_LIVE_URL

log = structlog.get_logger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class CryptoScreenerBacktestAssumptions:
    initial_equity: Decimal = Decimal("10000")
    risk_per_trade_pct: Decimal = Decimal("0.01")  # 1% equity risk per trade
    max_open_trades: int = 5
    fee_bps_per_side: Decimal = Decimal("5")  # 0.05%
    slippage_bps: Decimal = Decimal("5")  # 0.05%
    funding_bps_per_8h: Decimal = Decimal("1")
    entry_mode: str = "pullback"  # "pullback", "breakout", or "both"
    target_direction: Direction | None = None  # None for bidirectional, or Direction.LONG / Direction.SHORT
    min_score: Decimal = Decimal("70.0")
    max_holding_bars_4h: int = 42  # 7 days max hold
    order_timeout_bars_4h: int = 6  # 24 hours to fill pending entry
    scale_out_at_target1: bool = True  # Take 50% profit at T1 and move stop to Breakeven


@dataclass(slots=True)
class PendingOrder:
    symbol: str
    instrument: Instrument
    direction: Direction
    archetype: str
    score: Decimal
    conviction: Decimal
    created_at: datetime
    entry_price: Decimal
    stop_loss: Decimal
    target1: Decimal
    target2: Decimal
    target3: Decimal
    risk_reward: Decimal
    order_type: str  # "LIMIT" (pullback) or "STOP_MARKET" (breakout)
    bars_active: int = 0


@dataclass(slots=True)
class ActiveTrade:
    symbol: str
    instrument: Instrument
    direction: Direction
    archetype: str
    score: Decimal
    conviction: Decimal
    opened_at: datetime
    entry_price: Decimal
    current_stop: Decimal
    initial_stop: Decimal
    target1: Decimal
    target2: Decimal
    target3: Decimal
    quantity: Decimal
    initial_quantity: Decimal
    risk_amount: Decimal
    risk_per_unit: Decimal
    scaled_out: bool = False
    realized_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_funding: Decimal = Decimal("0")
    max_price: Decimal = Decimal("0")
    min_price: Decimal = Decimal("0")
    bars_held_4h: int = 0


@dataclass(frozen=True, slots=True)
class ScreenerBacktestTrade:
    symbol: str
    direction: Direction
    archetype: str
    score: Decimal
    conviction: Decimal
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_loss: Decimal
    target1: Decimal
    target2: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    return_r: Decimal
    exit_reason: str
    mfe_r: Decimal
    mae_r: Decimal
    scaled_out: bool


@dataclass(frozen=True, slots=True)
class ScreenerBacktestResult:
    symbols: list[str]
    started_at: datetime
    ended_at: datetime
    history_days: int
    initial_equity: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal | None
    expectancy_r: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_usd: Decimal
    sharpe_ratio: Decimal | None
    avg_holding_hours: Decimal
    trades: list[ScreenerBacktestTrade]
    archetype_performance: dict[str, dict[str, Any]]
    symbol_performance: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "symbols": self.symbols,
                "history_days": self.history_days,
                "started_at": self.started_at.isoformat(),
                "ended_at": self.ended_at.isoformat(),
                "initial_equity": str(self.initial_equity),
                "final_equity": str(self.final_equity),
                "total_return_pct": f"{float(self.total_return_pct):.2f}%",
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate_pct": f"{float(self.win_rate_pct):.2f}%",
                "profit_factor": f"{float(self.profit_factor):.2f}" if self.profit_factor is not None else "N/A",
                "expectancy_r": f"{float(self.expectancy_r):+.2f}R",
                "max_drawdown_pct": f"{float(self.max_drawdown_pct):.2f}%",
                "max_drawdown_usd": f"${float(self.max_drawdown_usd):.2f}",
                "sharpe_ratio": f"{float(self.sharpe_ratio):.2f}" if self.sharpe_ratio is not None else "N/A",
                "avg_holding_hours": f"{float(self.avg_holding_hours):.1f}h",
            },
            "archetype_performance": self.archetype_performance,
            "symbol_performance": self.symbol_performance,
            "trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction.value,
                    "archetype": t.archetype,
                    "opened_at": t.opened_at.isoformat(),
                    "closed_at": t.closed_at.isoformat(),
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price),
                    "net_pnl": f"${float(t.net_pnl):+.2f}",
                    "return_r": f"{float(t.return_r):+.2f}R",
                    "exit_reason": t.exit_reason,
                    "mfe_r": f"{float(t.mfe_r):.2f}R",
                    "mae_r": f"{float(t.mae_r):.2f}R",
                    "scaled_out": t.scaled_out,
                }
                for t in self.trades
            ],
        }


class CryptoScreenerBacktester:
    """Historical multi-symbol event-driven replay engine for the Triple-Confluence Screener."""

    def __init__(
        self,
        settings: Settings | None = None,
        assumptions: CryptoScreenerBacktestAssumptions | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._assumptions = assumptions or CryptoScreenerBacktestAssumptions()
        self._screener = CryptoOpportunityScreener(self._settings)

    async def run(
        self,
        symbols: Sequence[str],
        history_days: int = 60,
        interval_hours: int = 4,
        client: BinanceHttpClient | None = None,
    ) -> ScreenerBacktestResult:
        """Execute the multi-symbol backtest across historical candles."""
        should_close_client = False
        if client is None:
            client = BinanceHttpClient(FUTURES_LIVE_URL)
            should_close_client = True

        try:
            adapter = BinanceUsdmAdapter(client)
            all_instruments = await adapter.instruments()
            instruments_map = {item.id.symbol: item for item in all_instruments if item.trading}

            # Filter valid instruments
            target_instruments: list[Instrument] = []
            for s in symbols:
                sym_upper = s.upper()
                if sym_upper in instruments_map:
                    target_instruments.append(instruments_map[sym_upper])
                else:
                    log.warning("Instrument not found or not trading", symbol=sym_upper)

            if not target_instruments:
                raise ValueError("No valid trading instruments provided for backtest")

            btc_instrument = instruments_map.get("BTCUSDT")
            if not btc_instrument:
                raise ValueError("BTCUSDT instrument not found for benchmark")

            # Calculate candles required:
            # Need 750 1H candles lookback for 30d VPVR, plus history_days * 24
            req_1h_bars = history_days * 24 + 760
            # Need 180 4H candles lookback, plus history_days * 6
            req_4h_bars = history_days * 6 + 190

            log.info(
                "Fetching historical candles from Binance Futures...",
                symbols=[i.id.symbol for i in target_instruments],
                history_days=history_days,
                req_1h=req_1h_bars,
                req_4h=req_4h_bars,
            )

            cache_dir = Path("data/cache/backtest")
            await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
            sem = asyncio.Semaphore(2)

            def _serialize_c(candles: list[Candle]) -> list[dict[str, Any]]:
                return [
                    {
                        "opened_at": c.opened_at.isoformat(),
                        "closed_at": c.closed_at.isoformat(),
                        "open": str(c.open),
                        "high": str(c.high),
                        "low": str(c.low),
                        "close": str(c.close),
                        "volume": str(c.volume),
                        "quote_volume": str(c.quote_volume),
                        "closed": c.closed,
                        "taker_buy_volume": str(c.taker_buy_volume),
                        "taker_buy_quote_volume": str(c.taker_buy_quote_volume),
                    }
                    for c in candles
                ]

            def _deserialize_c(data: list[dict[str, Any]]) -> list[Candle]:
                return [
                    Candle(
                        opened_at=datetime.fromisoformat(d["opened_at"]),
                        closed_at=datetime.fromisoformat(d["closed_at"]),
                        open=Decimal(d["open"]),
                        high=Decimal(d["high"]),
                        low=Decimal(d["low"]),
                        close=Decimal(d["close"]),
                        volume=Decimal(d["volume"]),
                        quote_volume=Decimal(d.get("quote_volume", "0")),
                        closed=bool(d.get("closed", True)),
                        taker_buy_volume=Decimal(d.get("taker_buy_volume", "0")),
                        taker_buy_quote_volume=Decimal(d.get("taker_buy_quote_volume", "0")),
                    )
                    for d in data
                ]

            async def _get_candles(inst: Instrument, interval: str, req_bars: int) -> list[Candle]:
                sym = inst.id.symbol
                cache_file = cache_dir / f"{sym}_{interval}_{req_bars}.json"
                if cache_file.exists() and (time.time() - cache_file.stat().st_mtime < 14400):
                    try:
                        with cache_file.open("r") as f:
                            data = json.load(f)
                        candles = _deserialize_c(data)
                        if len(candles) >= 50:
                            return candles
                    except Exception:
                        pass
                async with sem:
                    candles = list(await adapter.historical_candles(inst, interval, req_bars))
                    await asyncio.sleep(0.1)
                try:
                    with cache_file.open("w") as f:
                        json.dump(_serialize_c(candles), f)
                except Exception:
                    pass
                return candles

            # Fetch BTC benchmark
            btc_1h = await _get_candles(btc_instrument, "1h", req_1h_bars)
            btc_4h = await _get_candles(btc_instrument, "4h", req_4h_bars)

            # Concurrently fetch target instruments candles, funding, and OI history
            symbol_data: dict[str, dict[str, list[Candle]]] = {}
            funding_data: dict[str, list[dict[str, Any]]] = {}
            oi_data: dict[str, list[dict[str, Any]]] = {}

            async def fetch_symbol_bundle(inst: Instrument) -> None:
                sym = inst.id.symbol
                try:
                    c1h = await _get_candles(inst, "1h", req_1h_bars)
                    c4h = await _get_candles(inst, "4h", req_4h_bars)
                    if len(c1h) >= 220 and len(c4h) >= 50:
                        symbol_data[sym] = {
                            "1h": sorted([c for c in c1h if c.closed], key=lambda x: x.closed_at),
                            "4h": sorted([c for c in c4h if c.closed], key=lambda x: x.closed_at),
                        }
                except Exception as exc:
                    log.warning("Could not fetch candles for symbol", symbol=sym, error=str(exc))

                fr_cache = cache_dir / f"{sym}_funding.json"
                if fr_cache.exists() and (time.time() - fr_cache.stat().st_mtime < 14400):
                    try:
                        with fr_cache.open("r") as f:
                            raw = json.load(f)
                        funding_data[sym] = [
                            {"time": datetime.fromisoformat(d["time"]), "rate": Decimal(d["rate"])}
                            for d in raw
                        ]
                    except Exception:
                        pass
                if sym not in funding_data:
                    try:
                        async with sem:
                            fr_payload = await client.request(
                                "GET",
                                "/fapi/v1/fundingRate",
                                params={"symbol": sym, "limit": 1000},
                            )
                            await asyncio.sleep(0.05)
                        if isinstance(fr_payload, list):
                            parsed: list[dict[str, Any]] = [
                                {
                                    "time": datetime.fromtimestamp(int(d["fundingTime"]) / 1000, UTC),
                                    "rate": Decimal(str(d["fundingRate"])),
                                }
                                for d in fr_payload
                                if "fundingTime" in d and "fundingRate" in d
                            ]
                            funding_data[sym] = parsed
                            try:
                                with fr_cache.open("w") as f:
                                    json.dump(
                                        [{"time": d["time"].isoformat(), "rate": str(d["rate"])} for d in parsed],
                                        f,
                                    )
                            except Exception:
                                pass
                    except Exception as exc:
                        log.warning("Could not fetch funding rate history", symbol=sym, error=str(exc))

                oi_cache = cache_dir / f"{sym}_oi.json"
                if oi_cache.exists() and (time.time() - oi_cache.stat().st_mtime < 14400):
                    try:
                        with oi_cache.open("r") as f:
                            raw = json.load(f)
                        oi_data[sym] = [
                            {"time": datetime.fromisoformat(d["time"]), "oi": Decimal(d["oi"])}
                            for d in raw
                        ]
                    except Exception:
                        pass
                if sym not in oi_data:
                    try:
                        async with sem:
                            oi_payload = await client.request(
                                "GET",
                                "/futures/data/openInterestHist",
                                params={"symbol": sym, "period": "4h", "limit": 500},
                            )
                            await asyncio.sleep(0.05)
                        if isinstance(oi_payload, list):
                            parsed = [
                                {
                                    "time": datetime.fromtimestamp(int(d["timestamp"]) / 1000, UTC),
                                    "oi": Decimal(str(d["sumOpenInterestValue"])),
                                }
                                for d in oi_payload
                                if "timestamp" in d and "sumOpenInterestValue" in d
                            ]
                            oi_data[sym] = parsed
                            try:
                                with oi_cache.open("w") as f:
                                    json.dump(
                                        [{"time": d["time"].isoformat(), "oi": str(d["oi"])} for d in parsed],
                                        f,
                                    )
                            except Exception:
                                pass
                    except Exception as exc:
                        log.warning("Could not fetch open interest history", symbol=sym, error=str(exc))

            await asyncio.gather(*(fetch_symbol_bundle(inst) for inst in target_instruments))

            if not symbol_data:
                raise ValueError("No symbols had sufficient candle history for backtesting")

            # Run event-driven simulation
            return self._simulate(
                target_instruments=[inst for inst in target_instruments if inst.id.symbol in symbol_data],
                symbol_data=symbol_data,
                funding_data=funding_data,
                oi_data=oi_data,
                btc_1h=sorted([c for c in btc_1h if c.closed], key=lambda x: x.closed_at),
                btc_4h=sorted([c for c in btc_4h if c.closed], key=lambda x: x.closed_at),
                history_days=history_days,
                interval_hours=interval_hours,
            )

        finally:
            if should_close_client:
                await client.close()

    def _simulate(
        self,
        target_instruments: list[Instrument],
        symbol_data: dict[str, dict[str, list[Candle]]],
        funding_data: dict[str, list[dict[str, Any]]],
        oi_data: dict[str, list[dict[str, Any]]],
        btc_1h: list[Candle],
        btc_4h: list[Candle],
        history_days: int,
        interval_hours: int,
    ) -> ScreenerBacktestResult:
        """Event-driven bar-by-bar walk-forward simulation."""
        # Find global timeline of 4H bars across symbols
        all_4h_times = set()
        for d in symbol_data.values():
            for c in d["4h"]:
                all_4h_times.add(c.closed_at)
        timeline = sorted(all_4h_times)

        # Warmup period: skip first 180 4H bars to ensure full VPVR and indicator warmup
        warmup_bars = 180
        if len(timeline) <= warmup_bars + 10:
            warmup_bars = max(20, len(timeline) // 3)

        eval_timeline = timeline[warmup_bars:]
        if not eval_timeline:
            raise ValueError("Evaluation timeline is empty after warmup period")

        equity = self._assumptions.initial_equity
        peak_equity = equity
        max_drawdown_usd = ZERO
        max_drawdown_pct = ZERO

        pending_orders: list[PendingOrder] = []
        active_trades: list[ActiveTrade] = []
        completed_trades: list[ScreenerBacktestTrade] = []

        fee_rate = self._assumptions.fee_bps_per_side / Decimal("10000")
        slippage_rate = self._assumptions.slippage_bps / Decimal("10000")
        funding_rate_8h = self._assumptions.funding_bps_per_8h / Decimal("10000")

        # Static neutral macro context for screener
        macro_context = MacroSentimentContext(
            fear_greed=FearGreedReading(value=50, classification="Neutral", observed_at=eval_timeline[0]),
            stablecoin_supply=StablecoinSupplyMetric(
                total_circulating_usd=Decimal("160000000000"),
                change_1d_pct=Decimal("0.0"),
                change_7d_pct=Decimal("0.0"),
                change_30d_pct=Decimal("0.0"),
                observed_at=eval_timeline[0],
            ),
            market_breadth=GlobalMarketMetrics(
                btc_dominance_pct=Decimal("58.0"),
                btc_dominance_24h_delta=Decimal("0.0"),
                total_market_cap_usd=Decimal("2500000000000"),
                total_volume_24h_usd=Decimal("80000000000"),
                observed_at=eval_timeline[0],
            ),
        )

        for current_time in eval_timeline:
            # 1. Update Active Trades on current bar
            still_active: list[ActiveTrade] = []
            for trade in active_trades:
                sym = trade.symbol
                candles_4h = symbol_data[sym]["4h"]
                cur_bar = next((c for c in candles_4h if c.closed_at == current_time), None)
                if cur_bar is None:
                    still_active.append(trade)
                    continue

                trade.bars_held_4h += 1
                trade.max_price = max(trade.max_price, cur_bar.high)
                trade.min_price = min(trade.min_price, cur_bar.low) if trade.min_price > ZERO else cur_bar.low

                # Accrue 8h funding if step passed
                if interval_hours >= 8 or (trade.bars_held_4h % 2 == 0):
                    funding_cost = trade.quantity * cur_bar.close * funding_rate_8h
                    trade.total_funding += funding_cost

                # Evaluate exits
                closed = False
                exit_price = ZERO
                exit_reason = ""

                if trade.direction is Direction.LONG:
                    # Check Stop Loss vs Targets
                    stop_hit = cur_bar.low <= trade.current_stop
                    t1_hit = cur_bar.high >= trade.target1
                    t2_hit = cur_bar.high >= trade.target2

                    # Conservative: if both stop and target touched in same bar, assume stop hit first
                    if stop_hit and (t1_hit or t2_hit) or stop_hit:
                        closed = True
                        exit_price = trade.current_stop * (ONE - slippage_rate)
                        exit_reason = "STOP_LOSS" if not trade.scaled_out else "BREAKEVEN_STOP"
                    elif t1_hit and not trade.scaled_out and self._assumptions.scale_out_at_target1:
                        # Scale out 50% at Target 1
                        scale_qty = trade.quantity / Decimal("2")
                        realized = scale_qty * (trade.target1 - trade.entry_price)
                        fee = scale_qty * trade.target1 * fee_rate
                        trade.realized_pnl += (realized - fee)
                        trade.total_fees += fee
                        trade.quantity -= scale_qty
                        trade.scaled_out = True
                        # Move stop to breakeven
                        trade.current_stop = trade.entry_price
                        # Continue holding remaining 50%
                    elif t2_hit:
                        closed = True
                        exit_price = trade.target2
                        exit_reason = "TARGET_2"
                    elif trade.bars_held_4h >= self._assumptions.max_holding_bars_4h:
                        closed = True
                        exit_price = cur_bar.close
                        exit_reason = "TIME_EXPIRY"

                else:  # SHORT
                    stop_hit = cur_bar.high >= trade.current_stop
                    t1_hit = cur_bar.low <= trade.target1
                    t2_hit = cur_bar.low <= trade.target2

                    if stop_hit and (t1_hit or t2_hit) or stop_hit:
                        closed = True
                        exit_price = trade.current_stop * (ONE + slippage_rate)
                        exit_reason = "STOP_LOSS" if not trade.scaled_out else "BREAKEVEN_STOP"
                    elif t1_hit and not trade.scaled_out and self._assumptions.scale_out_at_target1:
                        scale_qty = trade.quantity / Decimal("2")
                        realized = scale_qty * (trade.entry_price - trade.target1)
                        fee = scale_qty * trade.target1 * fee_rate
                        trade.realized_pnl += (realized - fee)
                        trade.total_fees += fee
                        trade.quantity -= scale_qty
                        trade.scaled_out = True
                        trade.current_stop = trade.entry_price
                    elif t2_hit:
                        closed = True
                        exit_price = trade.target2
                        exit_reason = "TARGET_2"
                    elif trade.bars_held_4h >= self._assumptions.max_holding_bars_4h:
                        closed = True
                        exit_price = cur_bar.close
                        exit_reason = "TIME_EXPIRY"

                if closed:
                    # Final exit of remaining quantity
                    exit_fee = trade.quantity * exit_price * fee_rate
                    trade.total_fees += exit_fee
                    remaining_pnl = (
                        trade.quantity * (exit_price - trade.entry_price)
                        if trade.direction is Direction.LONG
                        else trade.quantity * (trade.entry_price - exit_price)
                    )
                    net_pnl = trade.realized_pnl + remaining_pnl - exit_fee - trade.total_funding
                    gross_pnl = trade.realized_pnl + remaining_pnl

                    # Calculate MFE / MAE in R
                    if trade.direction is Direction.LONG:
                        mfe_r = (trade.max_price - trade.entry_price) / max(trade.risk_per_unit, Decimal("1e-8"))
                        mae_r = (trade.entry_price - trade.min_price) / max(trade.risk_per_unit, Decimal("1e-8"))
                    else:
                        mfe_r = (trade.entry_price - trade.min_price) / max(trade.risk_per_unit, Decimal("1e-8"))
                        mae_r = (trade.max_price - trade.entry_price) / max(trade.risk_per_unit, Decimal("1e-8"))

                    return_r = net_pnl / max(trade.risk_amount, Decimal("1e-8"))

                    completed_trades.append(
                        ScreenerBacktestTrade(
                            symbol=trade.symbol,
                            direction=trade.direction,
                            archetype=trade.archetype,
                            score=trade.score,
                            conviction=trade.conviction,
                            opened_at=trade.opened_at,
                            closed_at=current_time,
                            entry_price=trade.entry_price,
                            exit_price=exit_price,
                            stop_loss=trade.initial_stop,
                            target1=trade.target1,
                            target2=trade.target2,
                            quantity=trade.initial_quantity,
                            gross_pnl=gross_pnl,
                            fees=trade.total_fees,
                            funding=trade.total_funding,
                            net_pnl=net_pnl,
                            return_r=return_r,
                            exit_reason=exit_reason,
                            mfe_r=max(ZERO, mfe_r),
                            mae_r=max(ZERO, mae_r),
                            scaled_out=trade.scaled_out,
                        )
                    )
                    equity += net_pnl
                    peak_equity = max(peak_equity, equity)
                    dd_usd = peak_equity - equity
                    dd_pct = (dd_usd / peak_equity * HUNDRED) if peak_equity > ZERO else ZERO
                    max_drawdown_usd = max(max_drawdown_usd, dd_usd)
                    max_drawdown_pct = max(max_drawdown_pct, dd_pct)
                else:
                    still_active.append(trade)

            active_trades = still_active

            # 2. Check Pending Orders for Fills
            still_pending: list[PendingOrder] = []
            for order in pending_orders:
                sym = order.symbol
                candles_4h = symbol_data[sym]["4h"]
                cur_bar = next((c for c in candles_4h if c.closed_at == current_time), None)
                if cur_bar is None:
                    still_pending.append(order)
                    continue

                order.bars_active += 1
                filled = False
                fill_price = ZERO

                if order.direction is Direction.LONG:
                    if order.order_type == "LIMIT":  # Pullback
                        if cur_bar.low <= order.entry_price:
                            filled = True
                            fill_price = min(order.entry_price, cur_bar.open) * (ONE + slippage_rate)
                    else:  # Breakout trigger
                        if cur_bar.high >= order.entry_price:
                            filled = True
                            fill_price = max(order.entry_price, cur_bar.open) * (ONE + slippage_rate)
                else:  # SHORT
                    if order.order_type == "LIMIT":
                        if cur_bar.high >= order.entry_price:
                            filled = True
                            fill_price = max(order.entry_price, cur_bar.open) * (ONE - slippage_rate)
                    else:
                        if cur_bar.low <= order.entry_price:
                            filled = True
                            fill_price = min(order.entry_price, cur_bar.open) * (ONE - slippage_rate)

                if filled:
                    # Size the position
                    risk_per_unit = abs(fill_price - order.stop_loss)
                    if risk_per_unit > ZERO:
                        target_risk = equity * self._assumptions.risk_per_trade_pct
                        raw_qty = target_risk / risk_per_unit
                        entry_fee = raw_qty * fill_price * fee_rate

                        active_trades.append(
                            ActiveTrade(
                                symbol=order.symbol,
                                instrument=order.instrument,
                                direction=order.direction,
                                archetype=order.archetype,
                                score=order.score,
                                conviction=order.conviction,
                                opened_at=current_time,
                                entry_price=fill_price,
                                current_stop=order.stop_loss,
                                initial_stop=order.stop_loss,
                                target1=order.target1,
                                target2=order.target2,
                                target3=order.target3,
                                quantity=raw_qty,
                                initial_quantity=raw_qty,
                                risk_amount=target_risk,
                                risk_per_unit=risk_per_unit,
                                total_fees=entry_fee,
                                max_price=cur_bar.high,
                                min_price=cur_bar.low,
                            )
                        )
                elif order.bars_active < self._assumptions.order_timeout_bars_4h:
                    still_pending.append(order)

            pending_orders = still_pending

            # 3. Screen for New Opportunities if below max open trades
            current_active_symbols = {t.symbol for t in active_trades} | {o.symbol for o in pending_orders}
            available_slots = self._assumptions.max_open_trades - len(current_active_symbols)

            if available_slots > 0:
                # Slices BTC history
                btc_history_1h = [c for c in btc_1h if c.closed_at <= current_time]
                btc_ret_1h = (
                    (btc_history_1h[-1].close - btc_history_1h[-2].close) / btc_history_1h[-2].close
                    if len(btc_history_1h) >= 2
                    else ZERO
                )
                is_btc_cascade = btc_ret_1h < Decimal("-0.015")

                for inst in target_instruments:
                    sym = inst.id.symbol
                    if sym in current_active_symbols:
                        continue

                    c1h_all = symbol_data[sym]["1h"]
                    c4h_all = symbol_data[sym]["4h"]

                    c1h_slice = [c for c in c1h_all if c.closed_at <= current_time][-750:]
                    c4h_slice = [c for c in c4h_all if c.closed_at <= current_time][-180:]

                    if len(c1h_slice) < 210 or len(c4h_slice) < 50:
                        continue

                    # Point-in-time Quote
                    last_c = c1h_slice[-1]
                    quote = Quote(
                        instrument_id=inst.id,
                        bid=last_c.close * Decimal("0.9995"),
                        ask=last_c.close * Decimal("1.0005"),
                        last=last_c.close,
                        quote_volume_24h=sum((c.quote_volume for c in c1h_slice[-24:]), ZERO),
                        observed_at=current_time,
                    )

                    snap_1h = snapshot(c1h_slice)

                    # Point-in-time Taker & Derivatives proxy from candle history
                    taker_ratio = taker_buy_ratio_from_candles(c1h_slice[-24:])
                    # Estimate rolling 7d and 1h OI proxy or volume delta
                    vol_1h = c1h_slice[-1].quote_volume
                    vol_prev = c1h_slice[-2].quote_volume if len(c1h_slice) >= 2 else vol_1h
                    vol_chg_1h = (vol_1h - vol_prev) / max(vol_prev, Decimal("1e-8"))

                    # Point-in-time real funding rate
                    funding_records = funding_data.get(sym, [])
                    pt_funding = next(
                        (f["rate"] for f in reversed(funding_records) if f["time"] <= current_time),
                        Decimal("0.0001"),
                    )

                    # Point-in-time real open interest and 7d delta
                    oi_records = oi_data.get(sym, [])
                    oi_now = next(
                        (o["oi"] for o in reversed(oi_records) if o["time"] <= current_time),
                        None,
                    )
                    cutoff_7d = current_time - timedelta(days=7)
                    oi_7d_ago = next(
                        (o["oi"] for o in reversed(oi_records) if o["time"] <= cutoff_7d),
                        None,
                    )
                    oi_change_7d = (
                        (oi_now - oi_7d_ago) / oi_7d_ago
                        if (oi_now is not None and oi_7d_ago is not None and oi_7d_ago > ZERO)
                        else None
                    )

                    deriv_snapshot = DerivativesSnapshot(
                        funding_rate=pt_funding,
                        basis_rate=Decimal("0.0005"),
                        open_interest=oi_now or (vol_1h * Decimal("10")),
                        open_interest_change_1h=vol_chg_1h,
                        taker_buy_sell_ratio=taker_ratio,
                        bid_depth_notional=Decimal("1000000"),
                        ask_depth_notional=Decimal("1000000"),
                        open_interest_change_7d=oi_change_7d,
                        observed_at=current_time,
                    )

                    # Calculate Residual Alpha vs BTC
                    beta, correlation, residual_24h, residual_6h, residual_7d = (
                        self._screener._calc_residual_alpha(c1h_slice, btc_history_1h)
                    )

                    # Evaluate Accumulation Long
                    accum_opp = None
                    if self._assumptions.target_direction in (None, Direction.LONG):
                        accum_opp = self._screener._score_accumulation_setup(
                            inst=inst,
                            quote=quote,
                            snap_1h=snap_1h,
                            closed_1h=c1h_slice,
                            closed_4h=c4h_slice,
                            derivatives=deriv_snapshot,
                            macro_context=macro_context,
                            is_btc_cascade=is_btc_cascade,
                            btc_ret_1h=btc_ret_1h,
                            residual_24h=residual_24h,
                        )

                    # Evaluate Distribution Short
                    dist_opp = None
                    if self._assumptions.target_direction in (None, Direction.SHORT):
                        dist_opp = self._screener._score_distribution_setup(
                            inst=inst,
                            quote=quote,
                            snap_1h=snap_1h,
                            closed_1h=c1h_slice,
                            closed_4h=c4h_slice,
                            derivatives=deriv_snapshot,
                            macro_context=macro_context,
                            is_btc_cascade=is_btc_cascade,
                            btc_ret_1h=btc_ret_1h,
                            residual_24h=residual_24h,
                        )

                    # Mutual exclusion selection
                    chosen_opp: CryptoOpportunity | None = None
                    if accum_opp and dist_opp:
                        chosen_opp = accum_opp if accum_opp.score >= dist_opp.score else dist_opp
                    elif accum_opp:
                        chosen_opp = accum_opp
                    elif dist_opp:
                        chosen_opp = dist_opp

                    if (
                        chosen_opp
                        and chosen_opp.setup_label is not SetupLabel.NO_SETUP
                        and chosen_opp.score >= self._assumptions.min_score
                        and chosen_opp.suggested_stop is not None
                        and len(chosen_opp.suggested_targets) >= 2
                    ):
                        entry_price = chosen_opp.pullback_entry or chosen_opp.current_price
                        order_type = "LIMIT"
                        if self._assumptions.entry_mode == "breakout" and chosen_opp.breakout_trigger:
                            is_breakout_confirmed = (
                                chosen_opp.breakout_readiness_status == "BREAKOUT_CONFIRMED"
                                or chosen_opp.execution_mode == "CEILING_BREAKOUT"
                                or chosen_opp.setup_label in (SetupLabel.BREAKOUT_LONG, SetupLabel.BREAKOUT_SHORT)
                            )
                            if is_breakout_confirmed:
                                entry_price = chosen_opp.breakout_trigger
                                order_type = "STOP_MARKET"
                            else:
                                entry_price = chosen_opp.pullback_entry or chosen_opp.current_price
                                order_type = "LIMIT"

                        pending_orders.append(
                            PendingOrder(
                                symbol=sym,
                                instrument=inst,
                                direction=chosen_opp.direction,
                                archetype=chosen_opp.setup_label.value,
                                score=chosen_opp.score,
                                conviction=chosen_opp.conviction or Decimal("75"),
                                created_at=current_time,
                                entry_price=entry_price,
                                stop_loss=chosen_opp.suggested_stop,
                                target1=chosen_opp.suggested_targets[0],
                                target2=chosen_opp.suggested_targets[1],
                                target3=(
                                    chosen_opp.suggested_targets[2]
                                    if len(chosen_opp.suggested_targets) > 2
                                    else chosen_opp.suggested_targets[1]
                                ),
                                risk_reward=chosen_opp.risk_reward_ratio or Decimal("2.5"),
                                order_type=order_type,
                            )
                        )
                        current_active_symbols.add(sym)
                        available_slots -= 1
                        if available_slots <= 0:
                            break

        # Compile final performance
        total_trades = len(completed_trades)
        wins = [t for t in completed_trades if t.net_pnl > ZERO]
        losses = [t for t in completed_trades if t.net_pnl < ZERO]

        win_rate = (Decimal(len(wins)) / Decimal(total_trades) * HUNDRED) if total_trades > 0 else ZERO
        gross_wins = sum((t.net_pnl for t in wins), ZERO)
        gross_losses = abs(sum((t.net_pnl for t in losses), ZERO))
        profit_factor = (gross_wins / gross_losses) if gross_losses > ZERO else (Decimal("99.9") if gross_wins > ZERO else None)
        expectancy_r = (sum((t.return_r for t in completed_trades), ZERO) / Decimal(total_trades)) if total_trades > 0 else ZERO
        total_return_pct = ((equity - self._assumptions.initial_equity) / self._assumptions.initial_equity) * HUNDRED

        # Sharpe ratio calculation
        trade_returns = [float(t.return_r) for t in completed_trades]
        if len(trade_returns) >= 2:
            avg_r = sum(trade_returns) / len(trade_returns)
            variance = sum((r - avg_r) ** 2 for r in trade_returns) / (len(trade_returns) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0
            sharpe = Decimal(str(round((avg_r / std_r) * math.sqrt(250), 2))) if std_r > 0 else None
        else:
            sharpe = None

        avg_hours = (
            sum(
                (Decimal(str((t.closed_at - t.opened_at).total_seconds())) / Decimal("3600") for t in completed_trades),
                ZERO,
            )
            / Decimal(total_trades)
            if total_trades > 0
            else ZERO
        )

        # Archetype grouping
        archetype_perf: dict[str, dict[str, Any]] = {}
        unique_archs = {t.archetype for t in completed_trades}
        for arch in unique_archs:
            arch_trades = [t for t in completed_trades if t.archetype == arch]
            arch_wins = [t for t in arch_trades if t.net_pnl > ZERO]
            arch_losses = [t for t in arch_trades if t.net_pnl < ZERO]
            a_gw = sum((t.net_pnl for t in arch_wins), ZERO)
            a_gl = abs(sum((t.net_pnl for t in arch_losses), ZERO))
            archetype_perf[arch] = {
                "trades": len(arch_trades),
                "wins": len(arch_wins),
                "losses": len(arch_losses),
                "win_rate": f"{(len(arch_wins)/len(arch_trades)*100):.1f}%",
                "profit_factor": f"{(a_gw/a_gl):.2f}" if a_gl > ZERO else "N/A",
                "net_pnl": f"${float(sum((t.net_pnl for t in arch_trades), ZERO)):+.2f}",
                "avg_r": f"{float(sum((t.return_r for t in arch_trades), ZERO)/Decimal(len(arch_trades))):+.2f}R",
            }

        # Symbol grouping
        symbol_perf: dict[str, dict[str, Any]] = {}
        unique_syms = {t.symbol for t in completed_trades}
        for s in unique_syms:
            s_trades = [t for t in completed_trades if t.symbol == s]
            s_wins = [t for t in s_trades if t.net_pnl > ZERO]
            symbol_perf[s] = {
                "trades": len(s_trades),
                "win_rate": f"{(len(s_wins)/len(s_trades)*100):.1f}%",
                "net_pnl": f"${float(sum((t.net_pnl for t in s_trades), ZERO)):+.2f}",
                "avg_r": f"{float(sum((t.return_r for t in s_trades), ZERO)/Decimal(len(s_trades))):+.2f}R",
            }

        return ScreenerBacktestResult(
            symbols=[i.id.symbol for i in target_instruments],
            started_at=eval_timeline[0],
            ended_at=eval_timeline[-1],
            history_days=history_days,
            initial_equity=self._assumptions.initial_equity,
            final_equity=equity,
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            expectancy_r=expectancy_r,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_usd=max_drawdown_usd,
            sharpe_ratio=sharpe,
            avg_holding_hours=avg_hours,
            trades=completed_trades,
            archetype_performance=archetype_perf,
            symbol_performance=symbol_perf,
        )
