from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from pydantic import SecretStr

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.domain.precision import normalize_price, normalize_quantity

log = structlog.get_logger(__name__)

FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"


class TestnetTrader:
    """Automated Binance USD-M Futures Testnet Trader.

    Safety & Risk Parameters:
    - Maximum 5 executed trades per rolling 24-hour window.
    - Maximum order notional size = $100.00 USD per trade.
    - Executes only highest-conviction setups (Score >= 90.0, R:R >= 2.0).
    - Places Limit Entry with contingent Stop Loss and Take Profit brackets.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        max_trades_24h: int = 5,
        max_trades_per_cycle: int = 3,
        max_order_usd: float = 100.0,
        min_conviction_score: float = 90.0,
        min_risk_reward: float = 2.0,
        leverage: int = 3,
        data_dir: Path | str = "data",
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.max_trades_24h = max_trades_24h
        self.max_trades_per_cycle = max_trades_per_cycle
        self.max_order_usd = max_order_usd
        self.min_conviction_score = min_conviction_score
        self.min_risk_reward = min_risk_reward
        self.leverage = leverage

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.data_dir / "testnet_trades.json"
        self.watchlist_file = self.data_dir / "testnet_watchlist.json"

        self._client: BinanceHttpClient | None = None
        self._exchange_info_cache: dict[str, dict[str, Any]] = {}
        self._exchange_info_cached_at: datetime | None = None
        self._lock = asyncio.Lock()

    def _get_client(self) -> BinanceHttpClient:
        if self._client is None:
            self._client = BinanceHttpClient(
                base_url=FUTURES_TESTNET_URL,
                api_key=SecretStr(self.api_key),
                api_secret=SecretStr(self.api_secret),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    # --------------------------------------------------------------------------
    # Persistence & Quota Tracking
    # --------------------------------------------------------------------------

    def load_trades(self) -> list[dict[str, Any]]:
        if not self.trades_file.exists():
            return []
        try:
            data = json.loads(self.trades_file.read_text())
            if isinstance(data, list):
                return data
        except Exception as exc:
            log.warning("failed_loading_testnet_trades", error=str(exc))
        return []

    def save_trades(self, trades: list[dict[str, Any]]) -> None:
        try:
            self.trades_file.parent.mkdir(parents=True, exist_ok=True)
            self.trades_file.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            log.error("failed_saving_testnet_trades", error=str(exc))

    def reset_trades(self) -> None:
        """Clears local trade records for resetting the 24h rolling quota."""
        self.save_trades([])

    def load_watchlist(self) -> dict[str, dict[str, Any]]:
        if not self.watchlist_file.exists():
            return {}
        try:
            data = json.loads(self.watchlist_file.read_text())
            if isinstance(data, dict):
                return data
        except Exception as exc:
            log.warning("failed_loading_testnet_watchlist", error=str(exc))
        return {}

    def save_watchlist(self, watchlist: dict[str, dict[str, Any]]) -> None:
        try:
            self.watchlist_file.parent.mkdir(parents=True, exist_ok=True)
            self.watchlist_file.write_text(json.dumps(watchlist, indent=2, default=str))
        except Exception as exc:
            log.error("failed_saving_testnet_watchlist", error=str(exc))

    def get_trades_last_24h(self) -> list[dict[str, Any]]:
        trades = self.load_trades()
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recent: list[dict[str, Any]] = []
        for t in trades:
            ts_str = t.get("timestamp")
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if dt >= cutoff:
                        recent.append(t)
                except Exception:
                    pass
        return recent

    def get_24h_trade_count(self) -> int:
        return len(self.get_trades_last_24h())

    def has_active_symbol_trade(self, symbol: str) -> bool:
        """Check if symbol was entered in the last 12 hours to avoid duplicate stacking."""
        trades = self.load_trades()
        cutoff = datetime.now(UTC) - timedelta(hours=12)
        for t in trades:
            if t.get("symbol") == symbol:
                ts_str = t.get("timestamp")
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if dt >= cutoff:
                            return True
                    except Exception:
                        pass
        return False

    # --------------------------------------------------------------------------
    # Exchange Info & Filters
    # --------------------------------------------------------------------------

    async def get_symbol_filters(self, symbol: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        if (
            not self._exchange_info_cache
            or not self._exchange_info_cached_at
            or (now - self._exchange_info_cached_at) > timedelta(hours=2)
        ):
            client = self._get_client()
            try:
                res = await client.request("GET", "/fapi/v1/exchangeInfo")
                if isinstance(res, dict) and "symbols" in res:
                    new_cache: dict[str, dict[str, Any]] = {}
                    for s in res["symbols"]:
                        sym_name = s.get("symbol")
                        if sym_name:
                            new_cache[sym_name] = s
                    self._exchange_info_cache = new_cache
                    self._exchange_info_cached_at = now
            except Exception as exc:
                log.warning("failed_fetching_testnet_exchange_info", error=str(exc))

        sym_info = self._exchange_info_cache.get(symbol, {})
        filters = sym_info.get("filters", [])
        tick_size = Decimal("0.0001")
        step_size = Decimal("1")
        min_qty = Decimal("1")
        min_notional = Decimal("5.0")

        for f in filters:
            ftype = f.get("filterType")
            if ftype == "PRICE_FILTER":
                tick_size = Decimal(str(f.get("tickSize", "0.0001")))
            elif ftype == "LOT_SIZE":
                step_size = Decimal(str(f.get("stepSize", "1")))
                min_qty = Decimal(str(f.get("minQty", "1")))
            elif ftype == "MIN_NOTIONAL":
                min_notional = Decimal(str(f.get("notional", "5.0")))

        return {
            "tick_size": tick_size,
            "step_size": step_size,
            "min_qty": min_qty,
            "min_notional": min_notional,
        }

    # --------------------------------------------------------------------------
    # Execution & Sizing
    # --------------------------------------------------------------------------

    async def get_account_balance(self) -> dict[str, Any]:
        client = self._get_client()
        await client.sync_clock("/fapi/v1/time")
        try:
            res = await client.request("GET", "/fapi/v2/balance", signed=True)
            if isinstance(res, list):
                usdt = next((x for x in res if x.get("asset") == "USDT"), None)
                if usdt:
                    return {
                        "available_balance": float(usdt.get("availableBalance", 0.0)),
                        "wallet_balance": float(usdt.get("balance", 0.0)),
                        "cross_unpnl": float(usdt.get("crossUnPnl", 0.0)),
                    }
        except Exception as exc:
            log.warning("testnet_balance_query_failed", error=str(exc))
        return {"available_balance": 0.0, "wallet_balance": 0.0, "cross_unpnl": 0.0}

    async def get_open_orders(self) -> list[dict[str, Any]]:
        client = self._get_client()
        await client.sync_clock("/fapi/v1/time")
        orders: list[dict[str, Any]] = []
        try:
            res = await client.request("GET", "/fapi/v1/openOrders", signed=True)
            if isinstance(res, list):
                orders.extend(res)
        except Exception as exc:
            log.warning("testnet_open_orders_query_failed", error=str(exc))

        try:
            algo_res = await client.request("GET", "/fapi/v1/openAlgoOrders", signed=True)
            if isinstance(algo_res, list):
                orders.extend(algo_res)
        except Exception as exc:
            log.warning("testnet_open_algo_orders_query_failed", error=str(exc))

        return orders

    async def get_status(self) -> dict[str, Any]:
        trades_24h = self.get_trades_last_24h()
        open_orders = await self.get_open_orders()
        watchlist = self.load_watchlist()

        return {
            "enabled": True,
            "mode": "testnet",
            "venue": "Binance USD-M Futures Testnet",
            "interval_hours": 4,
            "max_trades_24h": self.max_trades_24h,
            "max_trades_per_cycle": self.max_trades_per_cycle,
            "trades_last_24h_count": len(trades_24h),
            "remaining_trades_today": max(0, self.max_trades_24h - len(trades_24h)),
            "max_order_usd": self.max_order_usd,
            "min_conviction_score": self.min_conviction_score,
            "min_risk_reward": self.min_risk_reward,
            "leverage": self.leverage,
            "open_orders_count": len(open_orders),
            "watchlist_count": len(watchlist),
            "watchlist": list(watchlist.values())[:15],
            "recent_trades": self.load_trades()[-10:],
        }

    async def execute_trade(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Executes a single top-conviction candidate on Binance Testnet."""
        symbol = str(candidate.get("symbol", "")).upper()
        direction = str(candidate.get("direction", "long")).lower()
        is_long = direction != "short"
        pos_side = "LONG" if is_long else "SHORT"
        entry_side = "BUY" if is_long else "SELL"
        exit_side = "SELL" if is_long else "BUY"

        # Determine reference entry price
        raw_entry = float(
            candidate.get("pullback_entry")
            or candidate.get("suggested_entry")
            or candidate.get("current_price")
            or 0.0
        )
        raw_stop = float(candidate.get("suggested_stop") or 0.0)
        targets = candidate.get("suggested_targets") or []
        raw_target = float(targets[0]) if targets else (raw_entry * (1.05 if is_long else 0.95))

        if raw_entry <= 0 or raw_stop <= 0:
            log.warning("invalid_prices_for_candidate", symbol=symbol, entry=raw_entry, stop=raw_stop)
            return None

        # Fetch symbol precision filters
        filters = await self.get_symbol_filters(symbol)
        tick_size = filters["tick_size"]
        step_size = filters["step_size"]
        min_qty = filters["min_qty"]
        min_notional = filters["min_notional"]

        # Calculate quantity for $100 max order notional
        dec_entry = normalize_price(Decimal(str(raw_entry)), tick_size)
        dec_stop = normalize_price(Decimal(str(raw_stop)), tick_size)
        dec_target = normalize_price(Decimal(str(raw_target)), tick_size)

        target_notional = Decimal(str(self.max_order_usd))
        raw_qty = target_notional / dec_entry
        dec_qty = normalize_quantity(raw_qty, step_size)

        if dec_qty < min_qty:
            dec_qty = min_qty

        actual_notional = dec_qty * dec_entry
        if actual_notional < min_notional:
            log.warning("notional_below_venue_minimum", symbol=symbol, notional=str(actual_notional))
            return None

        client = self._get_client()
        await client.sync_clock("/fapi/v1/time")

        # 1. Set Leverage
        try:
            await client.request(
                "POST",
                "/fapi/v1/leverage",
                params={"symbol": symbol, "leverage": self.leverage},
                signed=True,
            )
        except Exception as lev_err:
            log.info("leverage_set_note", symbol=symbol, error=str(lev_err))

        # 2. Place Limit Entry Order
        trade_id = uuid4().hex
        client_order_id = f"atl-{trade_id[:12]}-ent"
        entry_params = {
            "symbol": symbol,
            "side": entry_side,
            "positionSide": pos_side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": str(dec_qty),
            "price": str(dec_entry),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "RESULT",
        }

        try:
            entry_resp = await client.request("POST", "/fapi/v1/order", params=entry_params, signed=True)
            entry_order_id = entry_resp.get("orderId") if isinstance(entry_resp, dict) else None
        except Exception as exc:
            log.error("entry_order_failed", symbol=symbol, error=str(exc))
            return None

        # 3. Place Stop Loss Protection (via Binance Futures Algo Order API)
        sl_order_id = None
        try:
            sl_params = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": exit_side,
                "positionSide": pos_side,
                "type": "STOP_MARKET",
                "triggerPrice": str(dec_stop),
                "quantity": str(dec_qty),
                "workingType": "MARK_PRICE",
                "newClientAlgoId": f"atl-{trade_id[:12]}-sl",
            }
            sl_resp = await client.request("POST", "/fapi/v1/algoOrder", params=sl_params, signed=True)
            sl_order_id = sl_resp.get("algoId") if isinstance(sl_resp, dict) else None
        except Exception as sl_err:
            log.warning("sl_algo_order_failed", symbol=symbol, error=str(sl_err))

        # 4. Place Take Profit Protection (via Binance Futures Algo Order API)
        tp_order_id = None
        try:
            tp_params = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": exit_side,
                "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": str(dec_target),
                "quantity": str(dec_qty),
                "workingType": "MARK_PRICE",
                "newClientAlgoId": f"atl-{trade_id[:12]}-tp",
            }
            tp_resp = await client.request("POST", "/fapi/v1/algoOrder", params=tp_params, signed=True)
            tp_order_id = tp_resp.get("algoId") if isinstance(tp_resp, dict) else None
        except Exception as tp_err:
            log.warning("tp_algo_order_failed", symbol=symbol, error=str(tp_err))

        trade_record = {
            "trade_id": trade_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "setup_label": candidate.get("setup_label", "INSTITUTIONAL_SETUP"),
            "conviction_score": float(candidate.get("score") if candidate.get("score") is not None else (candidate.get("conviction") or 0.0)),
            "entry_price": float(dec_entry),
            "stop_loss": float(dec_stop),
            "take_profit": float(dec_target),
            "quantity": float(dec_qty),
            "notional_usd": float(actual_notional),
            "entry_order_id": entry_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "status": "PLACED",
        }

        # Persist trade record
        all_trades = self.load_trades()
        all_trades.append(trade_record)
        self.save_trades(all_trades)

        log.info(
            "testnet_trade_placed",
            symbol=symbol,
            direction=direction,
            entry=float(dec_entry),
            notional=float(actual_notional),
            entry_order_id=entry_order_id,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )
        return trade_record

    def analyze_24_48h_opportunity(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Performs in-depth technical analysis and evaluates 24-48 hour execution viability."""
        current = float(candidate.get("current_price") or 0.0)
        raw_entry = float(
            candidate.get("pullback_entry")
            or candidate.get("suggested_entry")
            or current
        )
        raw_stop = float(candidate.get("suggested_stop") or 0.0)
        targets = candidate.get("suggested_targets") or []
        direction = str(candidate.get("direction", "long")).lower()
        is_long = direction != "short"
        target = float(targets[0]) if targets else (raw_entry * (1.05 if is_long else 0.95))
        rr = float(candidate.get("risk_reward_ratio") or 0.0)

        if current <= 0 or raw_entry <= 0 or raw_stop <= 0:
            return {"is_viable": False, "composite_score": 0.0, "reason": "missing_price_data"}

        # 1. Proximity to Entry (Actionable in next 24-48 hours)
        distance_pct = abs(current - raw_entry) / current * 100.0
        if distance_pct <= 2.5:
            proximity_score = 100.0
            timeframe_window = "IMMINENT_0_12H"
        elif distance_pct <= 6.5:
            proximity_score = 88.0
            timeframe_window = "HIGH_POTENTIAL_12_24H"
        elif distance_pct <= 16.0:
            proximity_score = 70.0
            timeframe_window = "DEVELOPING_24_48H"
        else:
            proximity_score = 40.0
            timeframe_window = "INCUBATING_OVER_48H"

        # 2. Structural Checklist Verification (Value, Volatility, Momentum, Volume)
        checklist = candidate.get("checklist_verification") or {}
        passed_count = sum(1 for v in checklist.values() if v is True)
        checklist_ratio = passed_count / max(1, len(checklist))
        checklist_score = checklist_ratio * 100.0

        # 3. Volume Microstructure & Absorption Check
        vcr = float(candidate.get("volume_contraction_ratio") or 1.0)
        if vcr < 0.85:
            vol_score = 100.0
        elif vcr <= 1.05:
            vol_score = 85.0
        else:
            vol_score = 55.0

        # 4. Momentum & Multi-Timeframe Scoring
        score_breakdown = candidate.get("score_breakdown") or {}
        momentum_val = float(score_breakdown.get("momentum") or 80.0)
        structure_val = float(score_breakdown.get("structure") or 80.0)
        trend_val = float(score_breakdown.get("trend") or 80.0)
        mtf_score = (momentum_val * 0.4) + (structure_val * 0.3) + (trend_val * 0.3)

        # 5. Composite Technical Opportunity Score (out of 100)
        raw_score = float(candidate.get("score") if candidate.get("score") is not None else (candidate.get("conviction") or 0.0))

        composite_score = (
            (raw_score * 0.35)           # Multi-factor score
            + (proximity_score * 0.30)   # 24-48h timeliness & entry proximity
            + (mtf_score * 0.15)         # Trend and momentum alignment
            + (checklist_score * 0.10)   # Structural checklist
            + (vol_score * 0.10)         # Volume contraction
        )

        # Viability: composite >= 75.0, distance <= 25%, R:R >= min_risk_reward, raw_score >= min_conviction
        is_viable = (
            composite_score >= 75.0
            and distance_pct <= 25.0
            and rr >= self.min_risk_reward
            and raw_score >= self.min_conviction_score
        )

        return {
            "is_viable": is_viable,
            "composite_score": round(composite_score, 1),
            "raw_score": round(raw_score, 1),
            "proximity_score": proximity_score,
            "distance_pct": round(distance_pct, 2),
            "timeframe_window": timeframe_window,
            "checklist_passed": f"{passed_count}/{len(checklist)}",
            "volume_contraction": round(vcr, 2),
            "risk_reward_ratio": rr,
            "direction": direction,
            "entry": raw_entry,
            "stop": raw_stop,
            "target": target,
        }

    def evaluate_market_condition(self, opportunities: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluates macro market health. If market is adverse/chopping, triggers NO_TRADE."""
        if not opportunities:
            return {"tradable": False, "regime": "NO_TRADE_HOLD", "reason": "Zero market opportunities available from screener"}

        high_score_count = sum(
            1 for o in opportunities
            if float(o.get("score") if o.get("score") is not None else (o.get("conviction") or 0.0)) >= self.min_conviction_score
        )

        if high_score_count < 2:
            return {
                "tradable": False,
                "regime": "NO_TRADE_HOLD",
                "reason": f"Market underperformance: only {high_score_count} setups meet conviction >= 90.0",
                "high_conviction_count": high_score_count,
            }

        return {
            "tradable": True,
            "regime": "ACTIVE_OPPORTUNITY",
            "reason": f"Market performance healthy: {high_score_count} high-conviction setups available",
            "high_conviction_count": high_score_count,
        }

    def update_watchlist(
        self,
        opportunities: list[dict[str, Any]],
        analyses: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Maintains the 24-48h incubation watchlist across 4-hour cycles."""
        now_iso = datetime.now(UTC).isoformat()
        cutoff = datetime.now(UTC) - timedelta(hours=48)
        existing = self.load_watchlist()

        pruned: dict[str, dict[str, Any]] = {}
        for sym, item in existing.items():
            ts_str = item.get("first_observed_at")
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if dt >= cutoff:
                        pruned[sym] = item
                except Exception:
                    pass

        for opp in opportunities:
            sym = str(opp.get("symbol", "")).upper()
            analysis = analyses.get(sym)
            if not analysis or analysis["composite_score"] < 70.0:
                continue

            if sym in pruned:
                entry = pruned[sym]
                entry["cycles_tracked"] = entry.get("cycles_tracked", 1) + 1
                entry["last_evaluated_at"] = now_iso
                entry["current_price"] = float(opp.get("current_price") or 0.0)
                entry["distance_pct"] = analysis["distance_pct"]
                entry["composite_score"] = analysis["composite_score"]
                entry["timeframe_window"] = analysis["timeframe_window"]
                if analysis["distance_pct"] <= 5.0:
                    entry["status"] = "CONFIRMED_READY"
            else:
                pruned[sym] = {
                    "symbol": sym,
                    "direction": opp.get("direction", "long"),
                    "setup_label": opp.get("setup_label", "SETUP"),
                    "first_observed_at": now_iso,
                    "last_evaluated_at": now_iso,
                    "cycles_tracked": 1,
                    "initial_price": float(opp.get("current_price") or 0.0),
                    "current_price": float(opp.get("current_price") or 0.0),
                    "suggested_entry": analysis["entry"],
                    "suggested_stop": analysis["stop"],
                    "suggested_target": analysis["target"],
                    "distance_pct": analysis["distance_pct"],
                    "composite_score": analysis["composite_score"],
                    "timeframe_window": analysis["timeframe_window"],
                    "status": "CONFIRMED_READY" if analysis["distance_pct"] <= 5.0 else "INCUBATING",
                }

        self.save_watchlist(pruned)
        return pruned

    async def evaluate_and_execute_candidates(
        self, opportunities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Evaluates screened candidates with 24-48h technical analysis and paces execution (max 3 per 4H cycle)."""
        async with self._lock:
            current_24h_count = self.get_24h_trade_count()
            remaining_24h = self.max_trades_24h - current_24h_count
            available_slots = max(0, min(self.max_trades_per_cycle, remaining_24h))

            # Perform Technical Analysis on all candidates
            analyses: dict[str, dict[str, Any]] = {}
            for opp in opportunities:
                sym = str(opp.get("symbol", "")).upper()
                analyses[sym] = self.analyze_24_48h_opportunity(opp)

            # Update 4H incubation watchlist
            self.update_watchlist(opportunities, analyses)

            if available_slots <= 0:
                log.info(
                    "testnet_quota_blocked",
                    trades_24h=current_24h_count,
                    max_trades_24h=self.max_trades_24h,
                    max_per_cycle=self.max_trades_per_cycle,
                    remaining_24h=remaining_24h,
                )
                return []

            # Market Regime & No-Trade Hours Filter
            market_condition = self.evaluate_market_condition(opportunities)
            if not market_condition.get("tradable", True):
                log.warning("testnet_no_trade_hours_active", reason=market_condition.get("reason"))
                return []

            # Filter viable candidates
            viable: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for opp in opportunities:
                sym = str(opp.get("symbol", "")).upper()
                analysis = analyses.get(sym)
                if not analysis or not analysis["is_viable"]:
                    continue
                if self.has_active_symbol_trade(sym):
                    continue

                viable.append((opp, analysis))

            # Sort descending by composite 24-48h score, then by risk-reward ratio
            viable.sort(
                key=lambda item: (
                    item[1]["composite_score"],
                    item[1]["risk_reward_ratio"],
                ),
                reverse=True,
            )

            log.info(
                "testnet_4h_evaluation_complete",
                scanned=len(opportunities),
                viable_count=len(viable),
                available_slots=available_slots,
                max_per_cycle=self.max_trades_per_cycle,
            )

            executed_trades: list[dict[str, Any]] = []
            for candidate, analysis in viable[:available_slots]:
                enriched = dict(candidate)
                enriched["technical_analysis"] = analysis
                result = await self.execute_trade(enriched)
                if result:
                    executed_trades.append(result)

            return executed_trades
