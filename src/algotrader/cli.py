"""Commands for screening and serving the research portal."""
from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal

import uvicorn

from algotrader.config import load_settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.infrastructure.runtime import build_runtime


async def _screen(mode: str, min_score: float, max_symbols: int) -> int:
    runtime = await build_runtime(load_settings())
    try:
        opportunities = await runtime.crypto_screen(
            mode=ScreenerMode(mode), min_score=Decimal(str(min_score)),
            max_symbols=max_symbols,
        )
        print(json.dumps({
            "mode": "screener", "order_execution_enabled": False,
            "validation_status": "uncalibrated_research",
            "opportunities": [item.as_dict() for item in opportunities],
        }, indent=2, default=str))
        return 0
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Atlas public-data crypto screener")
    commands = parser.add_subparsers(dest="command", required=True)
    screen = commands.add_parser("crypto-screen", aliases=["screen"], help="Screen public USD-M market data")
    screen.add_argument("--mode", choices=[mode.value for mode in ScreenerMode], default="all")
    screen.add_argument("--min-score", type=float, default=68.0)
    screen.add_argument("--max-symbols", type=int, default=250)
    serve = commands.add_parser("portal", aliases=["serve"], help="Serve the screener API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("doctor", help="Report local screener configuration without network access")
    args = parser.parse_args()
    if args.command in {"screen", "crypto-screen"}:
        if not 0 <= args.min_score <= 100 or not 1 <= args.max_symbols <= 500:
            parser.error("min-score must be 0..100 and max-symbols must be 1..500")
        return asyncio.run(_screen(args.mode, args.min_score, args.max_symbols))
    if args.command in {"portal", "serve"}:
        uvicorn.run("algotrader.api.portal_app:app", host=args.host, port=args.port)
        return 0
    settings = load_settings()
    print(json.dumps({
        "mode": "screener", "order_execution_enabled": False,
        "credentials_required": False, "environment": settings.environment.value,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
