"""Refresh existing opt-in recovery episodes once using public USD-M market data."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.recovery_episodes import RecoveryEpisodeService
from algotrader.domain.models import Venue
from algotrader.infrastructure.recovery_episodes import RecoveryEpisodeStore


async def refresh(path):
    if not path.is_file():
        raise ValueError("recovery refresh requires an existing enrolled database")
    store = RecoveryEpisodeStore(path)
    episodes = store.episodes(active_only=True)
    if any(episode.instrument.id.venue != Venue.BINANCE_USDM for episode in episodes):
        raise ValueError("recovery refresh supports public Binance USD-M episodes")
    if not episodes:
        return []
    client = BinanceHttpClient("https://fapi.binance.com")
    try:
        market = BinanceUsdmAdapter(client)
        states = await RecoveryEpisodeService(store).refresh(market, market)
        return [{"symbol": state.instrument.id.symbol, "status": state.status,
                 "reason": state.reason, "coverage_complete": state.coverage_complete} for state in states]
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(refresh(args.database))))


if __name__ == "__main__":
    main()
