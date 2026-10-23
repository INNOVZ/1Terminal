"""Public Binance Spot market data; no account or order capabilities."""

from algotrader.adapters.binance.base import BinanceMarketDataBase
from algotrader.domain.models import MarketKind, Venue


class BinanceSpotAdapter(BinanceMarketDataBase):
    venue = Venue.BINANCE_SPOT
    market_kind = MarketKind.SPOT
    exchange_info_path = "/api/v3/exchangeInfo"
    ticker_24h_path = "/api/v3/ticker/24hr"
    book_ticker_path = "/api/v3/ticker/bookTicker"
    klines_path = "/api/v3/klines"
