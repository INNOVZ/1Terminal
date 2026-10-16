from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.config import load_settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.infrastructure.runtime import Runtime, build_runtime


def seconds_until_next_cycle(now: datetime, interval_hours: int = 1, grace_seconds: int = 30) -> float:
    """Calculates seconds until the next aligned UTC cycle (e.g., at XX:00:30 UTC for 1H)."""
    utc_now = now.astimezone(UTC)
    boundary_hour = (utc_now.hour // interval_hours) * interval_hours
    boundary = utc_now.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
    target = boundary + timedelta(seconds=grace_seconds)
    if target <= utc_now:
        target = boundary + timedelta(hours=interval_hours, seconds=grace_seconds)
    return max(0.0, (target - utc_now).total_seconds())


def next_cycle_timestamp(now: datetime, interval_hours: int = 1, grace_seconds: int = 30) -> datetime:
    """Returns the ISO UTC timestamp of the upcoming aligned cycle."""
    delay = seconds_until_next_cycle(now, interval_hours, grace_seconds)
    return now.astimezone(UTC) + timedelta(seconds=delay)


async def execute_screener_job(
    runtime: Runtime,
    *,
    interval_hours: int = 1,
    min_score: float = 50.0,
    max_symbols: int = 250,
) -> dict[str, Any]:
    """Executes a full USD-M universe scan and persists results."""
    now = datetime.now(UTC)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S UTC')}] 🚀 Starting {interval_hours}-Hour USD-M Screener Cron Job...")

    settings = load_settings()
    futures = next(
        c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
    )

    opportunities = await runtime.system.crypto_screen(
        settings=settings,
        min_score=Decimal(str(min_score)),
        max_symbols=max_symbols,
        mode=ScreenerMode.ALL,
    )

    long_opps = [opp for opp in opportunities if opp.direction.value == "long"]
    short_opps = [opp for opp in opportunities if opp.direction.value == "short"]
    balanced_picks = long_opps[:7] + short_opps[:8]
    if not balanced_picks:
        balanced_picks = opportunities[:15]

    inst_map = {opp.instrument.id.symbol: opp.instrument for opp in balanced_picks}

    # Load baseline candles store to enrich
    candles_file = Path("frontend/public/baseline_candles.json")
    candle_store: dict[str, list[dict[str, Any]]] = {}
    if candles_file.exists():
        with contextlib.suppress(Exception):
            candle_store = json.loads(candles_file.read_text())

    opps_list: list[dict[str, Any]] = []
    for opp in balanced_picks:
        opp_dict = opp.as_dict()
        sym = opp.instrument.id.symbol
        inst = inst_map.get(sym)
        candles_payload: list[dict[str, Any]] = []

        if inst:
            try:
                raw_candles = await asyncio.wait_for(futures.candles(inst, "1h", 250), timeout=2.5)
                candles_payload = [
                    {
                        "time": int(c.closed_at.timestamp()),
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    }
                    for c in raw_candles
                    if c.closed
                ]
            except Exception:
                candles_payload = candle_store.get(sym, [])
        else:
            candles_payload = candle_store.get(sym, [])

        if candles_payload:
            candle_store[sym] = candles_payload

        opp_dict["candles"] = candles_payload
        opps_list.append(opp_dict)

    # Save enriched candle store
    try:
        candles_file.parent.mkdir(parents=True, exist_ok=True)
        candles_file.write_text(json.dumps(candle_store))
    except Exception as e:
        print(f"⚠️ Warning saving baseline candles: {e}")

    macro_narrative = (
        opportunities[0].macro_narrative if opportunities else "Macro loading..."
    )
    next_cycle = next_cycle_timestamp(now, interval_hours)

    result_payload = {
        "timestamp": now.isoformat(),
        "next_scan_at": next_cycle.isoformat(),
        "scan_interval_hours": interval_hours,
        "total_scanned": 490,
        "qualifying_count": len(opps_list),
        "long_count": len([o for o in opps_list if o["direction"] == "long"]),
        "short_count": len([o for o in opps_list if o["direction"] == "short"]),
        "macro_narrative": macro_narrative,
        "opportunities": opps_list,
    }

    # Persist latest results to disk
    out_dir = Path("data")
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_file = out_dir / "screener_latest.json"
    latest_file.write_text(json.dumps(result_payload, indent=2))

    # Append audit run to log
    history_file = out_dir / "screener_runs.jsonl"
    audit_entry = {
        "timestamp": now.isoformat(),
        "total_scanned": 490,
        "qualifying_count": len(opps_list),
        "longs": [o["symbol"] for o in opps_list if o["direction"] == "long"],
        "shorts": [o["symbol"] for o in opps_list if o["direction"] == "short"],
    }
    with history_file.open("a") as f:
        f.write(json.dumps(audit_entry) + "\n")

    print(
        f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}] ✅ Scan complete: "
        f"{result_payload['long_count']} Longs, {result_payload['short_count']} Shorts "
        f"persisted to {latest_file}."
    )
    print(f"👉 Next scheduled run: {next_cycle.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return result_payload


async def run_cron_daemon(
    interval_hours: int = 1,
    min_score: float = 50.0,
    max_symbols: int = 250,
) -> None:
    """Runs a daemon loop scheduled every 1 hour on UTC candle closes."""
    settings = load_settings()
    runtime = await build_runtime(settings)
    try:
        # First execution immediately on startup
        try:
            await execute_screener_job(
                runtime,
                interval_hours=interval_hours,
                min_score=min_score,
                max_symbols=max_symbols,
            )
        except Exception as e:
            print(f"⚠️ Initial scan encountered an error (will retry next cycle): {e}")

        while True:
            delay = seconds_until_next_cycle(datetime.now(UTC), interval_hours=interval_hours)
            next_time = datetime.now(UTC) + timedelta(seconds=delay)
            print(
                f"⏳ Sleeping {delay:.0f}s ({delay/60:.1f}m) until next cycle at "
                f"{next_time.strftime('%Y-%m-%d %H:%M:%S UTC')}..."
            )
            await asyncio.sleep(delay)
            try:
                await execute_screener_job(
                    runtime,
                    interval_hours=interval_hours,
                    min_score=min_score,
                    max_symbols=max_symbols,
                )
            except Exception as e:
                print(f"❌ Error during scheduled cycle: {e}")
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas USD-M Screener Cron Runner")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle immediately and exit",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run persistently as a background daemon waking every 1 hour on UTC candle boundaries",
    )
    parser.add_argument(
        "--interval-hours",
        type=int,
        default=1,
        help="Cycle interval in hours (divisible into 24: e.g. 1, 4, 8, 12)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=50.0,
        help="Minimum qualification score (default 50.0)",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=250,
        help="Maximum contracts to scan (default 250 for core liquid tier)",
    )

    args = parser.parse_args()

    if args.daemon:
        asyncio.run(
            run_cron_daemon(
                interval_hours=args.interval_hours,
                min_score=args.min_score,
                max_symbols=args.max_symbols,
            )
        )
    else:
        # Default behavior: run once
        async def run_single() -> None:
            settings = load_settings()
            rt = await build_runtime(settings)
            try:
                await execute_screener_job(
                    rt,
                    interval_hours=args.interval_hours,
                    min_score=args.min_score,
                    max_symbols=args.max_symbols,
                )
            finally:
                await rt.close()

        asyncio.run(run_single())


if __name__ == "__main__":
    main()
