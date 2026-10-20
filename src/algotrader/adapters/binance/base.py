from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Instrument,
    InstrumentId,
    MarketKind,
    OrderResult,
    OrderStatus,
    Quote,
    Venue,
)

ORDER_STATUS = {status.value: status for status in OrderStatus}


def _decimal(value: object, default: str = "0") -> Decimal:
    return Decimal(str(value if value is not None else default))


def _filter(filters: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    return next((item for item in filters if item.get("filterType") == name), {})


class BinanceMarketDataBase:
    venue: Venue
    market_kind: MarketKind
    exchange_info_path: str
    ticker_24h_path: str
    book_ticker_path: str
    klines_path: str

    def __init__(self, client: BinanceHttpClient) -> None:
        self._client = client

    async def instruments(self) -> Sequence[Instrument]:
        payload = await self._client.request("GET", self.exchange_info_path)
        return [self._parse_instrument(item) for item in payload["symbols"]]

    def _parse_instrument(self, item: Mapping[str, Any]) -> Instrument:
        filters = item.get("filters", [])
        price_filter = _filter(filters, "PRICE_FILTER")
        lot_filter = _filter(filters, "LOT_SIZE")
        notional_filter = _filter(filters, "NOTIONAL") or _filter(filters, "MIN_NOTIONAL")
        return Instrument(
            id=InstrumentId(self.venue, str(item["symbol"])),
            base_asset=str(item["baseAsset"]),
            quote_asset=str(item["quoteAsset"]),
            asset_class=AssetClass.CRYPTO,
            market_kind=self.market_kind,
            trading=item.get("status") == "TRADING"
            and (self.market_kind is MarketKind.SPOT or item.get("contractType") == "PERPETUAL"),
            price_tick=_decimal(price_filter.get("tickSize", "0.00000001")),
            quantity_step=_decimal(lot_filter.get("stepSize", "0.00000001")),
            min_quantity=_decimal(lot_filter.get("minQty", "0")),
            min_notional=_decimal(
                notional_filter.get("minNotional", notional_filter.get("notional", "0"))
            ),
            max_leverage=1 if self.market_kind is MarketKind.SPOT else 125,
        )

    async def quotes(self, instruments: Sequence[Instrument]) -> Sequence[Quote]:
        wanted = {instrument.id.symbol for instrument in instruments}
        tickers, books = await self._fetch_quote_payloads()
        tickers_by_symbol = {
            str(item["symbol"]): item
            for item in tickers
            if item.get("symbol") in wanted
        }
        quotes: list[Quote] = []
        for item in books:
            symbol = str(item.get("symbol", ""))
            if symbol not in wanted:
                continue
            bid = _decimal(item.get("bidPrice"))
            ask = _decimal(item.get("askPrice"))
            if bid <= 0 or ask <= 0:
                continue
            t = tickers_by_symbol.get(symbol)
            high_24h = _decimal(t.get("highPrice")) if t else None
            low_24h = _decimal(t.get("lowPrice")) if t else None
            price_change_pct_24h = _decimal(t.get("priceChangePercent")) if t else None
            quote_volume_24h = _decimal(t.get("quoteVolume")) if t else Decimal("0")
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
                )
            )
        return quotes

    async def _fetch_quote_payloads(
        self,
    ) -> tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]:
        import asyncio

        tickers, books = await asyncio.gather(
            self._client.request("GET", self.ticker_24h_path),
            self._client.request("GET", self.book_ticker_path),
        )
        return tickers, books

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> Sequence[Candle]:
        payload = await self._client.request(
            "GET",
            self.klines_path,
            params={"symbol": instrument.id.symbol, "interval": interval, "limit": limit},
        )
        return self._parse_candle_rows(payload)

    async def historical_candles(
        self, instrument: Instrument, interval: str, limit: int
    ) -> Sequence[Candle]:
        """Page backward through public klines for research without account credentials."""
        if limit <= 0:
            return []
        rows: list[Sequence[Any]] = []
        remaining = limit
        end_time: int | None = None
        while remaining > 0:
            page_size = min(1000, remaining)
            params: dict[str, object] = {
                "symbol": instrument.id.symbol,
                "interval": interval,
                "limit": page_size,
            }
            if end_time is not None:
                params["endTime"] = end_time
            payload = await self._client.request("GET", self.klines_path, params=params)
            if not payload:
                break
            page = list(payload)
            rows = page + rows
            remaining -= len(page)
            if len(page) < page_size:
                break
            end_time = int(page[0][0]) - 1
            await asyncio.sleep(0.15)
        return self._parse_candle_rows(rows[-limit:])

    @staticmethod
    def _parse_candle_rows(payload: Sequence[Sequence[Any]]) -> list[Candle]:
        now_ms = datetime.now(UTC).timestamp() * 1000
        return [
            Candle(
                opened_at=datetime.fromtimestamp(int(row[0]) / 1000, UTC),
                closed_at=datetime.fromtimestamp(int(row[6]) / 1000, UTC),
                open=_decimal(row[1]),
                high=_decimal(row[2]),
                low=_decimal(row[3]),
                close=_decimal(row[4]),
                volume=_decimal(row[5]),
                quote_volume=_decimal(row[7]),
                closed=int(row[6]) < now_ms,
                taker_buy_volume=_decimal(row[9]) if len(row) > 9 else Decimal("0"),
                taker_buy_quote_volume=_decimal(row[10]) if len(row) > 10 else Decimal("0"),
            )
            for row in payload
        ]


def parse_order_result(payload: Mapping[str, Any], venue: Venue, symbol: str) -> OrderResult:
    raw_status = str(payload.get("status", "UNKNOWN"))
    average = payload.get("avgPrice")
    if average in {None, "", "0", 0, "0.00", "0.00000"}:
        # Spot reports filled notional as `cummulativeQuoteQty`; USD-M Futures calls the
        # same field `cumQuote`. Reading only the Spot spelling left futures fills with no
        # derivable price, which the execution coordinator treats as an unknown fill --
        # halting *after* the entry filled but *before* protection was placed.
        quote_quantity = _decimal(payload.get("cummulativeQuoteQty"))
        if quote_quantity <= 0:
            quote_quantity = _decimal(payload.get("cumQuote"))
        executed = _decimal(payload.get("executedQty"))
        average_price = quote_quantity / executed if quote_quantity > 0 and executed > 0 else None
    else:
        average_price = _decimal(average)
    return OrderResult(
        venue=venue,
        symbol=symbol,
        client_order_id=str(payload.get("clientOrderId", payload.get("origClientOrderId", ""))),
        exchange_order_id=str(payload.get("orderId", "")),
        status=ORDER_STATUS.get(raw_status, OrderStatus.UNKNOWN),
        executed_quantity=_decimal(payload.get("executedQty")),
        average_price=average_price,
        raw_status=raw_status,
    )


def parse_algo_order_result(payload: Mapping[str, Any], venue: Venue, symbol: str) -> OrderResult:
    """Map Binance USD-M conditional-algo responses into the common order model."""
    raw_status = str(payload.get("algoStatus", "UNKNOWN"))
    return OrderResult(
        venue=venue,
        symbol=symbol,
        client_order_id=str(payload.get("clientAlgoId", "")),
        exchange_order_id=str(payload.get("algoId", "")),
        status=ORDER_STATUS.get(raw_status, OrderStatus.UNKNOWN),
        # Conditional orders have not executed when Binance accepts the bracket.
        executed_quantity=Decimal("0"),
        average_price=None,
        raw_status=raw_status,
    )


def bracket_revision(stop_price: Decimal, target_price: Decimal | None = None) -> str:
    """Short deterministic tag identifying one bracket geometry.

    Client order ids must change when a bracket is amended, or the venue rejects the new
    order as a duplicate. Deriving the tag from the prices rather than a random value keeps
    a retry after an ambiguous response idempotent: the same amendment reuses the same id
    instead of creating a second protective order against one position.
    """
    target_revision = target_price if target_price is not None else "stop-only"
    digest = sha256(f"{stop_price}|{target_revision}".encode()).hexdigest()
    return digest[:6]
