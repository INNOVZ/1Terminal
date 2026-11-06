"""Manual, bounded public-data observation job. Never scheduled by the screener."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.forward_observer import ForwardObserver
from algotrader.infrastructure.runtime import FUTURES_LIVE_URL
from algotrader.infrastructure.scan_archive import LocalScanArchive


async def observe_scan(root: Path, scan_id: str, horizon_hours: int, max_symbols: int) -> dict[str, Any]:
    client = BinanceHttpClient(FUTURES_LIVE_URL)
    try:
        return await ForwardObserver(LocalScanArchive(root), BinanceUsdmAdapter(client)).observe(scan_id, horizon_hours, max_symbols)
    finally:
        await client.close()
