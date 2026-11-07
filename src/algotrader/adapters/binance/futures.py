from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from algotrader.adapters.binance.base import BinanceMarketDataBase, _decimal
from algotrader.domain.errors import VenueError
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)


class BinanceUsdmAdapter(BinanceMarketDataBase):
    venue = Venue.BINANCE_USDM
    market_kind = MarketKind.FUTURES
    exchange_info_path = "/fapi/v1/exchangeInfo"
    ticker_24h_path = "/fapi/v1/ticker/24hr"
    book_ticker_path = "/fapi/v1/ticker/bookTicker"
    klines_path = "/fapi/v1/klines"

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        candles = await super().candles(instrument, interval, limit)
        interval_ms = {"1h": 3_600_000, "4h": 14_400_000, "1w": 604_800_000}.get(interval)
        if interval_ms is not None:
            closed = [candle for candle in candles if candle.closed]
            if not closed:
                raise VenueError(f"Binance returned no closed {interval} candles for {instrument.id.symbol}")
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            latest_closed_ms = int(max(candle.closed_at for candle in closed).timestamp() * 1000)
            if now_ms - latest_closed_ms > interval_ms + 300_000:
                raise VenueError(f"Binance returned stale {interval} candles for {instrument.id.symbol}")
        return candles

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        retries: int = 2,
        base_delay: float = 0.200,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await self._client.request(method, path, params=params)
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0.01, 0.05)
                    await asyncio.sleep(delay)
        raise last_exc or VenueError(f"Request failed after {retries} retries: {path}")

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        """Fetch public point-in-time derivatives features without API credentials.

        Uses async retries with exponential backoff + jitter for Stage 2 endpoints.
        Never defaults failed endpoints to synthetic zero fallbacks; marks degraded_telemetry instead.
        """
        symbol = instrument.id.symbol
        has_degraded_telemetry = False

        async def fetch_endpoint(path: str, params: dict[str, object]) -> Any:
            nonlocal has_degraded_telemetry
            try:
                return await self._request_with_retry("GET", path, params=params, retries=2)
            except Exception:
                has_degraded_telemetry = True
                return None

        # One hourly OI series supplies timestamp-checked 1h, 4h, 24h and 7d changes.
        results = await asyncio.gather(
            fetch_endpoint("/fapi/v1/premiumIndex", {"symbol": symbol}),
            fetch_endpoint("/fapi/v1/openInterest", {"symbol": symbol}),
            fetch_endpoint(
                "/futures/data/openInterestHist",
                {"symbol": symbol, "period": "1h", "limit": 200},
            ),
            fetch_endpoint(
                "/futures/data/takerlongshortRatio",
                {"symbol": symbol, "period": "1h", "limit": 26},
            ),
            fetch_endpoint("/fapi/v1/depth", {"symbol": symbol, "limit": 20}),
            return_exceptions=True,
        )
        premium = results[0] if isinstance(results[0], dict) else {}
        open_interest = results[1] if isinstance(results[1], dict) else {}
        oi_history = results[2] if isinstance(results[2], list) else []
        taker = results[3] if isinstance(results[3], list) else []
        depth = results[4] if isinstance(results[4], dict) else {}

        if not premium and not open_interest:
            raise VenueError("Binance returned invalid derivatives snapshot objects")

        funding_rate: Decimal | None = None
        try:
            candidate_funding = Decimal(str(premium["lastFundingRate"]))
            if candidate_funding.is_finite():
                funding_rate = candidate_funding
        except (InvalidOperation, KeyError, TypeError, ValueError):
            pass
        if funding_rate is None:
            has_degraded_telemetry = True

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        try:
            reference_at = int(open_interest.get("time") or premium["time"])
            provider_clock_fresh = -60_000 <= now_ms - reference_at <= 300_000
        except (KeyError, TypeError, ValueError):
            reference_at = now_ms
            provider_clock_fresh = False
        if not provider_clock_fresh:
            has_degraded_telemetry = True

        mark = Decimal(str(premium.get("markPrice", "0")))
        index = Decimal(str(premium.get("indexPrice", "0")))
        basis = (mark - index) / index if index > 0 else Decimal("0")

        oi_samples: dict[int, Decimal] = {}
        for row in oi_history:
            if not isinstance(row, dict):
                continue
            try:
                timestamp = int(row["timestamp"])
                quantity = Decimal(str(row["sumOpenInterest"]))
            except (InvalidOperation, KeyError, TypeError, ValueError):
                continue
            if timestamp > 0 and quantity.is_finite() and quantity > 0:
                oi_samples[timestamp] = quantity

        oi_change_1h: Decimal | None = None
        oi_change_4h: Decimal | None = None
        oi_change_24h: Decimal | None = None
        oi_change_7d: Decimal | None = None
        if oi_samples:
            latest_at = max(oi_samples)
            if provider_clock_fresh and -300_000 <= reference_at - latest_at <= 3_900_000:
                latest_quantity = oi_samples[latest_at]

                def change_over(hours: int) -> Decimal | None:
                    target_at = latest_at - hours * 3_600_000
                    baseline_at = min(oi_samples, key=lambda at: abs(at - target_at))
                    if abs(baseline_at - target_at) > 300_000:
                        return None
                    baseline = oi_samples[baseline_at]
                    return (latest_quantity - baseline) / baseline

                oi_change_1h = change_over(1)
                oi_change_4h = change_over(4)
                oi_change_24h = change_over(24)
                oi_change_7d = change_over(168)
            else:
                has_degraded_telemetry = True
        else:
            has_degraded_telemetry = True

        hour_ms = 3_600_000
        latest_closed_start = (reference_at // hour_ms) * hour_ms - hour_ms
        taker_samples: dict[int, tuple[Decimal, Decimal]] = {}
        for row in taker:
            if not isinstance(row, dict):
                continue
            try:
                timestamp = int(row["timestamp"])
                buy_volume = Decimal(str(row["buyVol"]))
                sell_volume = Decimal(str(row["sellVol"]))
            except (InvalidOperation, KeyError, TypeError, ValueError):
                continue
            if buy_volume.is_finite() and sell_volume.is_finite() and buy_volume >= 0 and sell_volume >= 0:
                taker_samples[timestamp] = (buy_volume, sell_volume)

        # Within the 5-minute top-of-hour batch aggregation window (matching the 300_000ms
        # OI rollover grace on line 146), Binance publishes hourly takerlongshortRatio rows
        # asynchronously across symbols. Anchor to the prior closed hour if the new hour's
        # batch row has not landed yet.
        effective_closed_start = latest_closed_start
        if (
            (reference_at % hour_ms) <= 300_000
            and latest_closed_start not in taker_samples
            and (latest_closed_start - hour_ms) in taker_samples
        ):
            effective_closed_start = latest_closed_start - hour_ms

        def taker_ratio_over(hours: int) -> Decimal | None:
            if not provider_clock_fresh:
                return None
            samples = [
                taker_samples.get(effective_closed_start - offset * hour_ms)
                for offset in range(hours)
            ]
            if any(sample is None for sample in samples):
                return None
            total_buy = sum((sample[0] for sample in samples if sample is not None), Decimal("0"))
            total_sell = sum((sample[1] for sample in samples if sample is not None), Decimal("0"))
            return total_buy / total_sell if total_sell > 0 else None

        taker_ratio_1h = taker_ratio_over(1)
        taker_ratio_4h = taker_ratio_over(4)
        taker_ratio_24h = taker_ratio_over(24)
        if taker_ratio_1h is None:
            has_degraded_telemetry = True

        def depth_notional(side: object) -> Decimal:
            if not isinstance(side, list):
                return Decimal("0")
            return sum(
                (
                    Decimal(str(level[0])) * Decimal(str(level[1]))
                    for level in side
                    if isinstance(level, list) and len(level) >= 2
                ),
                Decimal("0"),
            )

        return DerivativesSnapshot(
            funding_rate=funding_rate,
            basis_rate=basis,
            open_interest=Decimal(str(open_interest.get("openInterest", "0"))),
            open_interest_change_1h=oi_change_1h,
            taker_buy_sell_ratio=taker_ratio_1h,
            bid_depth_notional=depth_notional(depth.get("bids")),
            ask_depth_notional=depth_notional(depth.get("asks")),
            open_interest_change_4h=oi_change_4h,
            open_interest_change_24h=oi_change_24h,
            open_interest_change_7d=oi_change_7d,
            taker_buy_sell_ratio_4h=taker_ratio_4h,
            taker_buy_sell_ratio_24h=taker_ratio_24h,
            degraded_telemetry=has_degraded_telemetry,
        )

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        """Stage 0 Bulk Quote & Premium/Funding Ingestion (Weight: 40 + 5 + 10 = 55)."""
        wanted = {instrument.id.symbol for instrument in instruments}
        results = await asyncio.gather(
            self._client.request("GET", self.ticker_24h_path),
            self._client.request("GET", self.book_ticker_path),
            self._client.request("GET", "/fapi/v1/premiumIndex"),
            return_exceptions=True,
        )
        tickers_list = results[0] if isinstance(results[0], list) else []
        books_list = results[1] if isinstance(results[1], list) else []
        premiums_list = results[2] if isinstance(results[2], list) else []

        tickers_by_symbol = {
            str(item["symbol"]): item
            for item in tickers_list
            if item.get("symbol") in wanted
        }
        premiums_by_symbol = {
            str(item["symbol"]): item
            for item in premiums_list
            if item.get("symbol") in wanted
        }

        quotes: list[Quote] = []
        for item in books_list:
            symbol = str(item.get("symbol", ""))
            if symbol not in wanted:
                continue
            bid = _decimal(item.get("bidPrice"))
            ask = _decimal(item.get("askPrice"))
            if bid <= 0 or ask <= 0:
                continue
            t = tickers_by_symbol.get(symbol)
            p = premiums_by_symbol.get(symbol)
            high_24h = _decimal(t.get("highPrice")) if t else None
            low_24h = _decimal(t.get("lowPrice")) if t else None
            price_change_pct_24h = _decimal(t.get("priceChangePercent")) if t else None
            quote_volume_24h = _decimal(t.get("quoteVolume")) if t else Decimal("0")

            funding_rate: Decimal | None = None
            basis_pct: Decimal | None = None
            if p:
                if "lastFundingRate" in p and p["lastFundingRate"] is not None:
                    funding_rate = _decimal(p.get("lastFundingRate"))
                mark_p = _decimal(p.get("markPrice"))
                index_p = _decimal(p.get("indexPrice"))
                if index_p > 0 and mark_p > 0:
                    basis_pct = (mark_p - index_p) / index_p

            quotes.append(
                Quote(
                    instrument_id=InstrumentId(self.venue, symbol),
                    bid=bid,
                    ask=ask,
                    last=(bid + ask) / Decimal("2"),
                    quote_volume_24h=quote_volume_24h,
                    high_24h=high_24h,
                    low_24h=low_24h,
                    price_change_pct_24h=price_change_pct_24h,
                    funding_rate=funding_rate,
                    basis_pct=basis_pct,
                )
            )
        return quotes
