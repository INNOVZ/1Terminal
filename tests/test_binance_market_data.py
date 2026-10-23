from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from algotrader.adapters.binance.spot import BinanceSpotAdapter
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
