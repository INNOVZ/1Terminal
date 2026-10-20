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
        max_order_usd: float = 100.0,
        min_conviction_score: float = 90.0,
        min_risk_reward: float = 2.0,
        leverage: int = 3,
        data_dir: Path | str = "data",
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.max_trades_24h = max_trades_24h
        self.max_order_usd = max_order_usd
        self.min_conviction_score = min_conviction_score
        self.min_risk_reward = min_risk_reward
        self.leverage = leverage

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.data_dir / "testnet_trades.json"

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
        try:
            res = await client.request("GET", "/fapi/v1/openOrders", signed=True)
            if isinstance(res, list):
                return res
        except Exception as exc:
            log.warning("testnet_open_orders_query_failed", error=str(exc))
        return []

    async def get_status(self) -> dict[str, Any]:
        balance = await self.get_account_balance()
        trades_24h = self.get_trades_last_24h()
        open_orders = await self.get_open_orders()

        return {
            "enabled": True,
            "mode": "testnet",
            "venue": "Binance USD-M Futures Testnet",
            "max_trades_24h": self.max_trades_24h,
            "trades_last_24h_count": len(trades_24h),
            "remaining_trades_today": max(0, self.max_trades_24h - len(trades_24h)),
            "max_order_usd": self.max_order_usd,
            "min_conviction_score": self.min_conviction_score,
            "min_risk_reward": self.min_risk_reward,
            "leverage": self.leverage,
            "account_balance": balance,
            "open_orders_count": len(open_orders),
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

        # 3. Place Stop Loss Protection (closePosition=true)
        sl_order_id = None
        try:
            sl_params = {
                "symbol": symbol,
                "side": exit_side,
                "positionSide": pos_side,
                "type": "STOP_MARKET",
                "stopPrice": str(dec_stop),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
                "newClientOrderId": f"atl-{trade_id[:12]}-sl",
            }
            sl_resp = await client.request("POST", "/fapi/v1/order", params=sl_params, signed=True)
            sl_order_id = sl_resp.get("orderId") if isinstance(sl_resp, dict) else None
        except Exception as sl_err:
            log.warning("sl_order_failed", symbol=symbol, error=str(sl_err))

        # 4. Place Take Profit Protection (closePosition=true)
        tp_order_id = None
        try:
            tp_params = {
                "symbol": symbol,
                "side": exit_side,
                "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": str(dec_target),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
                "newClientOrderId": f"atl-{trade_id[:12]}-tp",
            }
            tp_resp = await client.request("POST", "/fapi/v1/order", params=tp_params, signed=True)
            tp_order_id = tp_resp.get("orderId") if isinstance(tp_resp, dict) else None
        except Exception as tp_err:
            log.warning("tp_order_failed", symbol=symbol, error=str(tp_err))

        trade_record = {
            "trade_id": trade_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "setup_label": candidate.get("setup_label", "INSTITUTIONAL_SETUP"),
            "conviction_score": float(candidate.get("conviction") or candidate.get("score") or 0.0),
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
        )
        return trade_record

    async def evaluate_and_execute_candidates(
        self, opportunities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Evaluates screened candidates and automatically executes up to 24h limit."""
        async with self._lock:
            # Check remaining quota
            current_24h_count = self.get_24h_trade_count()
            remaining_slots = self.max_trades_24h - current_24h_count

            if remaining_slots <= 0:
                log.info(
                    "testnet_daily_quota_full",
                    trades_24h=current_24h_count,
                    max_trades=self.max_trades_24h,
                )
                return []

            # Filter candidates for top conviction
            qualifying: list[dict[str, Any]] = []
            for opp in opportunities:
                score = float(opp.get("conviction") or opp.get("score") or 0.0)
                rr = float(opp.get("risk_reward_ratio") or 0.0)
                symbol = str(opp.get("symbol", "")).upper()

                if score < self.min_conviction_score:
                    continue
                if rr < self.min_risk_reward:
                    continue
                if self.has_active_symbol_trade(symbol):
                    continue

                qualifying.append(opp)

            # Sort descending by conviction score
            qualifying.sort(
                key=lambda o: float(o.get("conviction") or o.get("score") or 0.0),
                reverse=True,
            )

            executed_trades: list[dict[str, Any]] = []
            for candidate in qualifying[:remaining_slots]:
                result = await self.execute_trade(candidate)
                if result:
                    executed_trades.append(result)

            return executed_trades
