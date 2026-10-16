from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from algotrader.adapters.binance.base import parse_order_result
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.domain.models import Venue
from tests.conftest import make_instrument


def _row(open_time: int) -> list[object]:
    return [
        open_time,
        "100",
        "101",
        "99",
        "100.5",
        "10",
        open_time + 3_599_999,
        "1005",
    ]


class PagedClient:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, Any]] = []
        self.responses = [
            [_row(index * 3_600_000) for index in range(2, 1002)],
            [_row(index * 3_600_000) for index in range(2)],
        ]

    async def request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> object:
        del method, path
        self.requests.append(params or {})
        return self.responses.pop(0)


async def test_historical_candles_page_backward_without_overlap() -> None:
    client = PagedClient()
    market = BinanceSpotAdapter(client)  # type: ignore[arg-type]

    candles = await market.historical_candles(make_instrument(), "1h", 1002)

    assert len(candles) == 1002
    assert candles[0].opened_at < candles[-1].opened_at
    assert client.requests[0]["limit"] == 1000
    assert client.requests[1]["limit"] == 2
    assert client.requests[1]["endTime"] == 2 * 3_600_000 - 1


def test_futures_fill_price_derives_from_cum_quote() -> None:
    """Regression: USD-M names the filled-notional field `cumQuote`, not the Spot spelling.

    Reading only `cummulativeQuoteQty` left futures fills with no derivable price. The
    execution coordinator treated that as an unknown fill and halted *after* the entry
    filled but *before* protection was placed -- leaving live inventory with no stop.
    """
    result = parse_order_result(
        {
            "status": "FILLED",
            "avgPrice": "0.00000",
            "cumQuote": "140.646",
            "executedQty": "12083",
            "orderId": "1",
            "clientOrderId": "atl-x",
        },
        Venue.BINANCE_USDM,
        "HFTUSDT",
    )
    assert result.average_price is not None
    assert abs(result.average_price - Decimal("0.011640")) < Decimal("0.000001")


def test_spot_fill_price_still_uses_its_own_field_name() -> None:
    result = parse_order_result(
        {
            "status": "FILLED",
            "cummulativeQuoteQty": "500",
            "executedQty": "2",
            "orderId": "2",
            "clientOrderId": "atl-y",
        },
        Venue.BINANCE_SPOT,
        "BTCUSDT",
    )
    assert result.average_price == Decimal("250")


def test_unfilled_order_still_reports_no_price() -> None:
    """A genuinely unfilled order must not acquire a fabricated price."""
    result = parse_order_result(
        {
            "status": "EXPIRED",
            "avgPrice": "0",
            "cumQuote": "0",
            "executedQty": "0",
            "orderId": "3",
            "clientOrderId": "atl-z",
        },
        Venue.BINANCE_USDM,
        "HFTUSDT",
    )
    assert result.average_price is None
