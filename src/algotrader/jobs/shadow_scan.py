"""Explicit manual research jobs; never imported by runtime/API/cron."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from algotrader.adapters.binance.shadow import ShadowMarketData
from algotrader.application.shadow_budget import ShadowBudget
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.shadow_discovery import ShadowPolicy, admit_shadow
from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive


def clock_now() -> datetime:
    return datetime.now(UTC)


async def run_shadow_scan(root: Path, scan_id: str) -> ShadowRunRecord:
    parent = await asyncio.to_thread(LocalScanArchive(root).read_scan, scan_id)
    policy = ShadowPolicy()
    admit_shadow(parent, policy, clock_now())
    market = ShadowMarketData(ShadowBudget(policy))
    try:
        record = await ShadowRunner(market, clock=clock_now).run(parent, policy)
        await asyncio.to_thread(LocalShadowArchive(root).write_run, record)
        if record.status == "cancelled":
            raise asyncio.CancelledError
        return record
    finally:
        await market.close()


async def observe_shadow_scan(
    root: Path, shadow_id: str, horizon_hours: int, max_symbols: int
) -> dict[str, Any]:
    from algotrader.adapters.binance.client import BinanceHttpClient
    from algotrader.adapters.binance.futures import BinanceUsdmAdapter
    from algotrader.application.shadow_observer import ShadowObserver

    client = BinanceHttpClient("https://fapi.binance.com")
    try:
        return await ShadowObserver(LocalShadowArchive(root), BinanceUsdmAdapter(client)).observe(
            shadow_id, horizon_hours, max_symbols
        )
    finally:
        await client.close()
