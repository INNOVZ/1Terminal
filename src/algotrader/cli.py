from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import metadata

import uvicorn
from pydantic import SecretStr

from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.application.backtest import BacktestAssumptions, BacktestResult, StrategyBacktester
from algotrader.application.portfolio import RISK_STATE_KEY
from algotrader.application.research import feature_information_report
from algotrader.application.screener import candidate_payload
from algotrader.config import DAILY_REGIME_PROFILES, ExecutionMode, StrategyProfile, load_settings
from algotrader.domain.errors import ConfigurationError
from algotrader.domain.models import Direction, Venue
from algotrader.infrastructure.runtime import (
    FUTURES_LIVE_URL,
    SPOT_LIVE_URL,
    build_runtime,
)
from algotrader.infrastructure.sqlite_state import SqliteStateRepository


async def _doctor() -> int:
    settings = load_settings()
    payload: dict[str, object] = {
        # Deployed-code fingerprint. A container running a stale image is indistinguishable
        # from a bug until you can see which commands the running build actually has.
        "package_version": _package_version(),
        "available_commands": sorted(COMMANDS),
        "environment": settings.environment.value,
        "execution_mode": settings.execution_mode.value,
        "strategy_profile": settings.strategy_profile.value,
        "live_enabled": settings.live_trading_enabled,
        "automation_enabled": settings.automation_enabled,
        "spot_credentials_configured": bool(settings.binance_spot_api_key.get_secret_value()),
        "futures_credentials_configured": bool(settings.binance_futures_api_key.get_secret_value()),
        "state_db": str(settings.state_db_path),
    }
    # When not in paper mode, validate signed-endpoint access.
    if settings.execution_mode.value != "paper":
        try:
            runtime = await build_runtime(settings)
            payload["spot_auth"] = "ok"
            payload["futures_auth"] = "ok"
            await runtime.close()
        except Exception as exc:
            payload["auth_error"] = str(exc)
    print(json.dumps(payload, indent=2))
    return 1 if "auth_error" in payload else 0


async def _screen() -> int:
    settings = load_settings()
    runtime = await build_runtime(settings)
    try:
        # One execution-strategy pass yields both eligible candidates and its rejection
        # funnel. Alt momentum is a separate, observation-only universe view: surface it in
        # the same operator report without allowing its ranks to leak into order authority.
        candidates, diagnostics = await runtime.system.screen_with_diagnostics()
        try:
            alt_momentum: dict[str, object] = await runtime.system.alt_momentum_leaderboard()
        except Exception as exc:
            # Discovery diagnostics must never hide the executable screen. This command is
            # read-only, so report the independent research-view failure inline.
            alt_momentum = {
                "available": False,
                "execution_authorized": False,
                "error_type": type(exc).__name__,
                "detail": str(exc)[:400],
            }
        print(
            json.dumps(
                {
                    "execution_screen": {
                        "strategy_profile": settings.strategy_profile.value,
                        "order_path": "configured_strategy",
                        "candidate_count": len(candidates),
                        "candidates": [candidate_payload(item) for item in candidates[:20]],
                        "diagnostics": diagnostics,
                        "portfolio_note": (
                            "These are signal-eligible candidates. run-once still applies "
                            "open-position, exposure, cooldown, and risk checks."
                        ),
                    },
                    "alt_momentum": alt_momentum,
                    "order_authority": (
                        "Only execution_screen.candidates can reach run-once; "
                        "alt_momentum remains observation-only."
                    ),
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _alt_momentum() -> int:
    runtime = await build_runtime(load_settings())
    try:
        print(json.dumps(await runtime.system.alt_momentum_leaderboard(), indent=2))
    finally:
        await runtime.close()
    return 0


async def _reconcile() -> int:
    """Show exactly what is blocking the cycle. Read-only; clears nothing."""
    runtime = await build_runtime(load_settings())
    try:
        print(json.dumps(await runtime.system.reconciliation_report(), indent=2))
    finally:
        await runtime.close()
    return 0


async def _adopt_position(*, signal_id: str, reason: str) -> int:
    """Protect and track a filled position the system lost track of."""
    runtime = await build_runtime(load_settings())
    try:
        print(
            json.dumps(
                await runtime.system.adopt_orphaned_position(signal_id, reason=reason),
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _abandon_lifecycle(*, signal_id: str, reason: str) -> int:
    """Retire a stuck lifecycle the operator has confirmed holds no open position.

    Exposed on the CLI as well as the API because this is an incident-time action, and at
    incident time the operator is already inside the container. Requiring a bearer token
    from a shell that does not have one turns a two-minute recovery into a scavenger hunt.
    """
    runtime = await build_runtime(load_settings())
    try:
        print(
            json.dumps(
                await runtime.system.abandon_execution_lifecycle(signal_id, reason=reason),
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _reconcile_close(
    *, signal_id: str, exit_price: Decimal, fees: Decimal, exit_reason: str
) -> int:
    """Book the close of a trade that is gone from the venue but still open locally.

    ``exit_price`` and ``fees`` must be the real figures from the venue: they land in the
    ledger, and the ledger drives the daily realised-loss circuit breaker. Guessing here
    silently mis-calibrates a risk control.
    """
    if exit_price <= 0:
        raise SystemExit("--exit-price must be a positive number taken from the venue")
    runtime = await build_runtime(load_settings())
    try:
        trade, gross = await runtime.system.reconcile_trade_close(
            signal_id, exit_price=exit_price, fees=fees, exit_reason=exit_reason
        )
        print(
            json.dumps(
                {
                    "reconciled": True,
                    "signal_id": str(trade.signal_id),
                    "instrument": str(trade.instrument_id),
                    "gross_realized_pnl": str(gross),
                    "net_realized_pnl": str(gross - fees),
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _suggest_close(*, signal_id: str) -> int:
    """Derive a trade's exit from the venue's own fill history.

    Read-only. It books nothing -- it prints the figures to pass to `reconcile-close`, so
    the operator still makes the call, but from exchange truth rather than recollection.
    """
    runtime = await build_runtime(load_settings())
    try:
        trade = next(
            (
                item
                for item in await runtime.repository.list_open_trades()
                if str(item.signal_id) == signal_id
            ),
            None,
        )
        if trade is None:
            raise SystemExit(f"no open trade {signal_id!r} was found")
        if trade.instrument_id.venue is not Venue.BINANCE_USDM:
            raise SystemExit("fill lookup is currently implemented for USD-M futures only")

        fills = await runtime.futures_market.recent_fills(
            trade.instrument_id, since=trade.opened_at
        )
        closing = [fill for fill in fills if fill.is_sell is (trade.direction is Direction.LONG)]
        if not closing:
            raise SystemExit(
                "no closing fills found at the venue since the trade opened; "
                "the position may have been closed outside this window"
            )
        quantity = sum((fill.quantity for fill in closing), Decimal("0"))
        notional = sum((fill.price * fill.quantity for fill in closing), Decimal("0"))
        fees = sum((fill.commission for fill in closing), Decimal("0"))
        vwap = notional / quantity if quantity > 0 else Decimal("0")
        print(
            json.dumps(
                {
                    "signal_id": signal_id,
                    "instrument": str(trade.instrument_id),
                    "tracked_quantity": str(trade.quantity),
                    "closing_fills": len(closing),
                    "closed_quantity": str(quantity),
                    "quantity_matches": quantity == trade.quantity,
                    "suggested_exit_price": str(vwap),
                    "suggested_fees": str(fees),
                    "venue_realized_pnl": str(
                        sum((fill.realized_pnl for fill in closing), Decimal("0"))
                    ),
                    "next": (
                        f"atlas reconcile-close --signal-id {signal_id} "
                        f"--exit-price {vwap} --fees {fees} --exit-reason manual"
                    ),
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _set_kill_switch(*, active: bool, reason: str) -> int:
    """Arm or clear the durable kill switch.

    Clearing still runs the full interlock: it refuses while unresolved intents, incomplete
    lifecycles, or position mismatches remain. This only removes the need for a bearer token
    from a shell that does not have one -- it grants no authority the API did not already.
    """
    runtime = await build_runtime(load_settings())
    try:
        await runtime.system.set_kill_switch(active, reason)
        print(json.dumps({"kill_switch": active, "reason": reason}, indent=2))
    finally:
        await runtime.close()
    return 0


async def _run_once() -> int:
    runtime = await build_runtime(load_settings())
    try:
        report = await runtime.system.run_cycle()
        print(
            json.dumps(
                {
                    "executed": report.executed,
                    "reason": report.reason.value,
                    "candidate_count": report.candidate_count,
                    "detail": report.detail,
                    "next_eligible_at": (
                        report.next_eligible_at.isoformat() if report.next_eligible_at else None
                    ),
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _backtest(
    *,
    symbol: str,
    venue: str,
    initial_equity: Decimal,
    intervals: tuple[int, ...],
    history_days: int,
    end_days_ago: int,
    stagnation_exit_bars: int,
    stagnation_min_favorable_r: Decimal,
    strategy_profile: StrategyProfile | None,
    cost_multiplier: Decimal,
    exit_policy: str,
    untouched_holdout: bool,
) -> int:
    if not intervals:
        raise SystemExit("backtest requires at least one interval")
    if len(set(intervals)) != len(intervals):
        raise SystemExit("backtest intervals must be unique")
    if history_days < 30:
        raise SystemExit("backtest history must be at least 30 days")
    if end_days_ago < 0:
        raise SystemExit("backtest end-days-ago cannot be negative")
    if stagnation_exit_bars == 1 or stagnation_exit_bars < 0:
        raise SystemExit("stagnation exit must be zero (disabled) or at least two bars")
    if cost_multiplier <= 0:
        raise SystemExit("cost multiplier must be positive")
    settings = load_settings()
    if strategy_profile is not None:
        settings = settings.model_copy(update={"strategy_profile": strategy_profile})
    client = BinanceHttpClient(
        SPOT_LIVE_URL if venue == "spot" else FUTURES_LIVE_URL,
        SecretStr(""),
        SecretStr(""),
    )
    try:
        market = (
            BinanceSpotAdapter(client)
            if venue == "spot"
            else BinanceUsdmAdapter(client, max_leverage=settings.max_futures_leverage)
        )
        instruments = await market.instruments()
        instrument = next(
            (item for item in instruments if item.id.symbol == symbol.upper() and item.trading),
            None,
        )
        if instrument is None:
            raise SystemExit(f"{symbol.upper()} is not a trading {venue} instrument")
        requested_days = history_days + end_days_ago
        one_hour_bars = requested_days * 24 + 260
        four_hour_bars = requested_days * 6 + settings.min_4h_history_bars + 5
        tasks = [
            market.historical_candles(instrument, "1h", one_hour_bars),
            market.historical_candles(instrument, "4h", four_hour_bars),
        ]
        if settings.strategy_profile in DAILY_REGIME_PROFILES:
            tasks.append(market.historical_candles(instrument, "1d", requested_days + 260))
        histories = await asyncio.gather(*tasks)
        one_hour, four_hour = histories[:2]
        daily = histories[2] if len(histories) > 2 else ()
        if instrument.base_asset == "BTC":
            benchmark_one_hour = one_hour
        else:
            benchmark = next(
                (item for item in instruments if item.base_asset == "BTC" and item.trading),
                None,
            )
            if benchmark is None:
                raise SystemExit(f"a BTC benchmark is unavailable on {venue}")
            benchmark_one_hour = await market.historical_candles(benchmark, "1h", one_hour_bars)
        if end_days_ago:
            cutoff = datetime.now(UTC) - timedelta(days=end_days_ago)
            one_hour = tuple(bar for bar in one_hour if bar.closed_at <= cutoff)
            four_hour = tuple(bar for bar in four_hour if bar.closed_at <= cutoff)
            daily = tuple(bar for bar in daily if bar.closed_at <= cutoff)
            benchmark_one_hour = tuple(bar for bar in benchmark_one_hour if bar.closed_at <= cutoff)
        backtester = StrategyBacktester(
            settings,
            BacktestAssumptions(
                initial_equity=initial_equity,
                spread_bps=Decimal("10") * cost_multiplier,
                fee_bps_per_side=Decimal("5") * cost_multiplier,
                stop_slippage_bps=Decimal("5") * cost_multiplier,
                funding_bps_per_8h=Decimal("1") * cost_multiplier,
                manage_exits=exit_policy == "managed",
                stagnation_exit_bars=(stagnation_exit_bars or None),
                stagnation_min_favorable_r=stagnation_min_favorable_r,
            ),
        )
        results = backtester.compare_intervals(
            instrument,
            list(one_hour),
            list(four_hour),
            list(daily),
            benchmark_one_hour=list(benchmark_one_hour),
            intervals=intervals,
        )
        promotion_gate = _promotion_gate(
            results,
            cost_multiplier=cost_multiplier,
            untouched_holdout_attested=untouched_holdout,
        )
        print(
            json.dumps(
                {
                    "instrument": str(instrument.id),
                    "history_days_requested": history_days,
                    "end_days_ago": end_days_ago,
                    "strategy_profile": settings.strategy_profile.value,
                    "research_scope": "single-symbol closed-candle diagnostic",
                    "assumptions": {
                        "initial_equity": str(initial_equity),
                        "cost_multiplier": str(cost_multiplier),
                        "screening_spread_bps": "10",
                        "spread_bps": str(Decimal("10") * cost_multiplier),
                        "fee_bps_per_side": str(Decimal("5") * cost_multiplier),
                        "stop_slippage_bps": str(Decimal("5") * cost_multiplier),
                        "funding_bps_per_8h": str(Decimal("1") * cost_multiplier),
                        "exit_policy": exit_policy,
                        "stagnation_exit_bars": stagnation_exit_bars or None,
                        "stagnation_min_favorable_r": str(stagnation_min_favorable_r),
                    },
                    "results": [result.as_dict() for result in results],
                    "promotion_gate": promotion_gate,
                },
                indent=2,
            )
        )
    finally:
        await client.close()
    return 0


def _promotion_gate(
    results: Sequence[BacktestResult],
    *,
    cost_multiplier: Decimal,
    untouched_holdout_attested: bool,
) -> dict[str, object]:
    positive = bool(results) and all(
        result.expectancy_r is not None
        and result.expectancy_r > 0
        and result.profit_factor is not None
        and result.profit_factor > 1
        for result in results
    )
    review_candidate = positive and cost_multiplier >= Decimal("2") and untouched_holdout_attested
    return {
        "positive_expectancy_and_profit_factor": positive,
        "doubled_costs": cost_multiplier >= Decimal("2"),
        "untouched_holdout_attested_not_verified": untouched_holdout_attested,
        # An operator assertion cannot prove that a dataset was never inspected. Keep the
        # automated promotion result fail-closed until an immutable experiment registry owns
        # holdout access and dataset/config/code hashes.
        "eligible_for_governed_review": review_candidate,
        "eligible_for_promotion": False,
        "blocked_reason": (
            "holdout access is not yet enforced by an immutable experiment registry"
            if review_candidate
            else "expectancy, doubled-cost, and holdout-attestation gates are not all satisfied"
        ),
        "rule": (
            "All requested intervals must have expectancy_r > 0 and profit_factor > 1 "
            "under >=2x costs; final promotion additionally requires a registry-controlled "
            "untouched holdout."
        ),
    }


async def _capture_testnet_spot_baseline(*, replace: bool) -> int:
    settings = load_settings()
    if settings.execution_mode is not ExecutionMode.TESTNET:
        raise SystemExit("Spot balance baselines require ATLAS_EXECUTION_MODE=testnet")
    runtime = await build_runtime(settings)
    try:
        asset_count = await runtime.spot_market.capture_testnet_balance_baseline(replace=replace)
        # A new exchange-account baseline must also start a fresh local risk baseline.
        await runtime.repository.delete_state(RISK_STATE_KEY)
        print(
            json.dumps(
                {
                    "spot_testnet_baseline": "reset" if replace else "initialized",
                    "asset_count": asset_count,
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
    return 0


async def _init_testnet_spot_baseline() -> int:
    """Capture the baseline, or report that it is already captured.

    ``init`` is idempotent by design: re-running it when a baseline exists is a no-op, not a
    failure. The underlying guard still refuses to *overwrite*, because silently recapturing
    would fold any open position into the ignored seed inventory and hide it from risk --
    that remains the job of the explicit reset command.
    """
    try:
        return await _capture_testnet_spot_baseline(replace=False)
    except ConfigurationError as exc:
        if "already exists" not in str(exc):
            raise
        print(
            json.dumps(
                {
                    "spot_testnet_baseline": "already_initialized",
                    "detail": "nothing to do; run reset-testnet-spot-baseline to recapture",
                },
                indent=2,
            )
        )
        return 0


async def _reset_testnet_spot_baseline() -> int:
    return await _capture_testnet_spot_baseline(replace=True)


async def _research_report() -> int:
    settings = load_settings()
    repository = SqliteStateRepository(
        settings.state_db_path,
        research_retention_days=settings.research_retention_days,
        event_retention_days=settings.event_retention_days,
    )
    await repository.initialize()
    observations = await repository.list_opportunities(
        since=datetime.now(UTC) - timedelta(days=settings.research_report_lookback_days)
    )
    print(json.dumps(feature_information_report(observations), indent=2))
    return 0


async def _worker() -> int:
    settings = load_settings()
    if not settings.automation_enabled:
        raise SystemExit("worker requires ATLAS_AUTOMATION_ENABLED=true")
    runtime = await build_runtime(settings)
    try:
        await runtime.scheduler.run_forever()
    finally:
        await runtime.close()
    return 0


async def _crypto_screen(
    *,
    min_score: Decimal,
    direction: str | None,
    top_n: int,
    max_symbols: int,
    mode: str = "accumulation",
    as_json: bool,
) -> int:
    settings = load_settings()
    runtime = await build_runtime(settings)
    try:
        from algotrader.domain.crypto_screener_models import ScreenerMode

        screener_mode = ScreenerMode(mode.lower())
        target_dir = None
        if direction:
            target_dir = Direction.LONG if direction.lower() == "long" else Direction.SHORT
        opportunities = await runtime.system.crypto_screen(
            settings=settings,
            min_score=min_score,
            target_direction=target_dir,
            top_n=top_n,
            max_symbols=max_symbols,
            mode=screener_mode,
        )
        if as_json:
            print(json.dumps([opp.as_dict() for opp in opportunities], indent=2))
            return 0

        mode_desc = (
            "Early Pre-Breakout Coils & Accumulation"
            if screener_mode is ScreenerMode.ACCUMULATION
            else "Active Momentum Breakouts & Expansion"
        )
        print("=" * 80)
        print("  ATLAS CRYPTO OPPORTUNITY SCREENER — MULTI-FACTOR REGIME & DERIVATIVES")
        print(f"  Mode: {screener_mode.value.upper()} ({mode_desc})")
        print("=" * 80)
        if opportunities:
            print(f" Macro Context: {opportunities[0].macro_narrative}")
            print(f" Found {len(opportunities)} institutional opportunities (min score: {min_score}):\n")
            for idx, opp in enumerate(opportunities, 1):
                dir_label = "🟢 LONG" if opp.direction is Direction.LONG else "🔴 SHORT"
                print(
                    f"#{idx:02d} {opp.instrument.id.symbol:<12} {dir_label:<8} "
                    f"Score: {opp.score:>5} | Setup: {opp.setup_label.value} | Regime: {opp.regime.value}"
                )
                print(
                    f"    Price: {opp.current_price} | Invalidation: {opp.suggested_stop} | "
                    f"Targets: {', '.join(str(t) for t in opp.suggested_targets)} (R:R {opp.risk_reward_ratio}:1)"
                )
                vp_str = f"POC: {opp.poc_price} | VAH: {opp.vah_price} | VAL: {opp.val_price}" if opp.poc_price is not None else "N/A"
                print(f"    Volume Profile: {vp_str}")
                f_bps = (
                    f"{float((opp.funding_rate or Decimal('0')) * 10000):+.1f} bps"
                    if opp.funding_rate is not None
                    else "N/A"
                )
                oi_1h_str = (
                    f"{float((opp.oi_change_1h or Decimal('0')) * 100):+.2f}%"
                    if opp.oi_change_1h is not None
                    else "N/A"
                )
                oi_7d_str = (
                    f"{float((opp.oi_change_7d or Decimal('0')) * 100):+.2f}%"
                    if opp.oi_change_7d is not None
                    else "N/A"
                )
                v_ratio_str = f"{opp.volume_contraction_ratio}x" if opp.volume_contraction_ratio is not None else "N/A"
                print(
                    f"    OI 7d: {oi_7d_str} | OI 1h: {oi_1h_str} | Funding: {f_bps} | "
                    f"Vol Dry-Up (7d/14d): {v_ratio_str} | Taker: {opp.taker_ratio or 'N/A'}"
                )
                if opp.rationale:
                    print(f"    Rationale: {'; '.join(opp.rationale)}")
                print("-" * 80)
        else:
            print(f" No setups met the minimum score threshold ({min_score}) in current regime.")
            print("=" * 80)
    finally:
        await runtime.close()
    return 0


async def _chart_verify(*, symbol: str | None = None, mode: str = "accumulation") -> int:
    import webbrowser
    from pathlib import Path

    from algotrader.application.chart_visualizer import generate_chart_html
    from algotrader.domain.crypto_screener_models import ScreenerMode

    settings = load_settings()
    runtime = await build_runtime(settings)
    try:
        screener_mode = ScreenerMode(mode.lower())
        futures = next(
            c.market_data for c in runtime.system._contexts if c.market_data.venue.value == "binance_usdm"
        )
        opportunities = await runtime.system.crypto_screen(
            settings=settings,
            min_score=Decimal("50.0"),
            max_symbols=250,
            mode=screener_mode,
        )
        if not opportunities:
            print("No opportunities met the threshold to generate chart verification.")
            return 1

        target_sym = symbol.upper() if symbol else opportunities[0].instrument.id.symbol
        inst_map = {opp.instrument.id.symbol: opp.instrument for opp in opportunities}

        candles_map = {}
        for opp in opportunities[:20]:
            sym = opp.instrument.id.symbol
            inst = inst_map.get(sym)
            if inst:
                c1h = await futures.candles(inst, "1h", 250)
                candles_map[sym] = [c for c in c1h if c.closed]

        html_content = generate_chart_html(opportunities, candles_map, active_symbol=target_sym)
        out_path = Path.cwd() / "dashboard.html"
        out_path.write_text(html_content, encoding="utf-8")

        print("=" * 80)
        print("  ATLAS CHART ANALYTICS & VISUAL VERIFICATION DASHBOARD")
        print("=" * 80)
        print(f"  Dashboard generated: file://{out_path.resolve()}")
        print(f"  Active candidate: {target_sym}")
        print("  Opening interactive chart in browser...")
        print("=" * 80)

        webbrowser.open(f"file://{out_path.resolve()}")
    finally:
        await runtime.close()
    return 0


def _portal() -> int:
    import subprocess
    import sys
    import time
    import webbrowser
    from pathlib import Path

    print("=" * 80)
    print("  LAUNCHING ATLAS INSTITUTIONAL CRYPTO PORTAL (FULLSTACK)")
    print("=" * 80)
    print("  Backend API:  http://127.0.0.1:8000/api")
    print("  Next.js App:  http://localhost:3000")
    print("=" * 80)

    # 1. Start Python FastAPI backend
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "algotrader.api.portal_app"],
        cwd=str(Path.cwd()),
    )

    # 2. Start Next.js frontend
    frontend_dir = Path.cwd() / "frontend"
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(frontend_dir),
    )

    time.sleep(3)
    webbrowser.open("http://localhost:3000")

    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down Atlas Portal...")
        backend_proc.terminate()
        frontend_proc.terminate()
        try:
            backend_proc.wait(timeout=3)
            frontend_proc.wait(timeout=3)
        except Exception:
            pass
async def _backtest_screener(
    *,
    symbols_arg: str | None,
    history_days: int,
    interval_hours: int,
    min_score: Decimal,
    direction_arg: str | None,
    entry_mode: str,
    initial_equity: Decimal,
    risk_per_trade_pct: Decimal,
    max_open_trades: int,
    as_json: bool,
) -> int:
    from algotrader.application.crypto_screener_backtest import (
        CryptoScreenerBacktestAssumptions,
        CryptoScreenerBacktester,
    )
    from algotrader.domain.models import Direction

    if symbols_arg:
        symbols = [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]
    else:
        # Default representative basket: includes short squeeze candidates, base coils, and distribution shorts
        symbols = [
            "QUSDT", "AXLUSDT", "RSRUSDT", "KMNOUSDT", "SOLVUSDT",
            "BEAMXUSDT", "COLLECTUSDT", "IOTXUSDT", "COMPUSDT", "PNUTUSDT"
        ]

    target_direction = None
    if direction_arg:
        target_direction = Direction.LONG if direction_arg.lower() == "long" else Direction.SHORT

    assumptions = CryptoScreenerBacktestAssumptions(
        initial_equity=initial_equity,
        risk_per_trade_pct=risk_per_trade_pct,
        max_open_trades=max_open_trades,
        min_score=min_score,
        entry_mode=entry_mode,
        target_direction=target_direction,
    )
    backtester = CryptoScreenerBacktester(assumptions=assumptions)

    dir_label = target_direction.value.upper() if target_direction else "BIDIRECTIONAL"
    print("=" * 85)
    print("  ATLAS TRIPLE-CONFLUENCE SCREENER AUTOMATED BACKTESTER")
    print(f"  Universe ({len(symbols)} Assets): {', '.join(symbols)}")
    print(f"  Period: Last {history_days} Days | Step: {interval_hours}H | Min Score: {min_score} | Direction: {dir_label} | Mode: {entry_mode.upper()}")
    print(f"  Initial Equity: ${initial_equity:,.2f} | Risk/Trade: {float(risk_per_trade_pct * 100):.1f}% | Max Open Trades: {max_open_trades}")
    print("=" * 85)

    result = await backtester.run(
        symbols=symbols,
        history_days=history_days,
        interval_hours=interval_hours,
    )

    if as_json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0

    print("\n" + "=" * 85)
    print("  PORTFOLIO EXECUTIVE SUMMARY")
    print("=" * 85)
    print(f"  Initial Capital:      ${result.initial_equity:,.2f}")
    print(f"  Ending Capital:       ${result.final_equity:,.2f}")
    ret_sign = "+" if result.total_return_pct >= 0 else ""
    print(f"  Total Return:         {ret_sign}{result.total_return_pct:.2f}%")
    print(f"  Max Drawdown:         -${result.max_drawdown_usd:,.2f} ({result.max_drawdown_pct:.2f}%)")
    print(f"  Total Trades:         {result.total_trades} (Wins: {result.winning_trades}, Losses: {result.losing_trades})")
    print(f"  Win Rate:             {result.win_rate_pct:.1f}%")
    pf_str = f"{result.profit_factor:.2f}" if result.profit_factor is not None else "N/A"
    print(f"  Profit Factor:        {pf_str}")
    print(f"  Expectancy (E[R]):    {result.expectancy_r:+.2f}R per trade")
    sharpe_str = f"{result.sharpe_ratio:.2f}" if result.sharpe_ratio is not None else "N/A"
    print(f"  Sharpe Ratio (Ann.):  {sharpe_str}")
    print(f"  Avg Holding Duration: {result.avg_holding_hours:.1f} Hours")
    print("=" * 85)

    if result.archetype_performance:
        print("\n" + "-" * 85)
        print("  PERFORMANCE BREAKDOWN BY INSTITUTIONAL ARCHETYPE")
        print("-" * 85)
        print(f"  {'Archetype':<32} {'Trades':<8} {'Win Rate':<10} {'Profit Factor':<15} {'Net PnL':<12} {'Avg R':<8}")
        print("  " + "-" * 83)
        for arch, stats in sorted(result.archetype_performance.items(), key=lambda x: x[1]['trades'], reverse=True):
            print(f"  {arch:<32} {stats['trades']:<8} {stats['win_rate']:<10} {stats['profit_factor']:<15} {stats['net_pnl']:<12} {stats['avg_r']:<8}")

    if result.symbol_performance:
        print("\n" + "-" * 85)
        print("  PERFORMANCE BREAKDOWN BY SYMBOL")
        print("-" * 85)
        print(f"  {'Symbol':<15} {'Trades':<10} {'Win Rate':<12} {'Net PnL':<15} {'Avg R':<10}")
        print("  " + "-" * 83)
        for sym, stats in sorted(result.symbol_performance.items(), key=lambda x: x[0]):
            print(f"  {sym:<15} {stats['trades']:<10} {stats['win_rate']:<12} {stats['net_pnl']:<15} {stats['avg_r']:<10}")

    if result.trades:
        print("\n" + "-" * 85)
        print(f"  TRADE EXECUTION LOG (Total {len(result.trades)} Trades)")
        print("-" * 85)
        print(f"  {'Symbol':<10} {'Dir':<6} {'Archetype':<26} {'Entry':<10} {'Exit':<10} {'Net PnL':<10} {'R-Mult':<8} {'Exit Reason'}")
        print("  " + "-" * 83)
        for t in result.trades[-25:]:
            dir_str = "LONG" if t.direction is Direction.LONG else "SHORT"
            print(
                f"  {t.symbol:<10} {dir_str:<6} {t.archetype[:25]:<26} "
                f"{float(t.entry_price):<10.4g} {float(t.exit_price):<10.4g} "
                f"{float(t.net_pnl):>+8.2f}  {float(t.return_r):>+5.2f}R  {t.exit_reason}"
            )
        if len(result.trades) > 25:
            print(f"  ... ({len(result.trades) - 25} earlier trades omitted; run with --json to see all)")

    print("=" * 85 + "\n")
    return 0


COMMANDS: dict[str, Callable[[], Coroutine[object, object, int]]] = {
    "alt-momentum": _alt_momentum,
    "doctor": _doctor,
    "screen": _screen,
    "reconcile": _reconcile,
    "research-report": _research_report,
    "run-once": _run_once,
    "worker": _worker,
    "init-testnet-spot-baseline": _init_testnet_spot_baseline,
    "reset-testnet-spot-baseline": _reset_testnet_spot_baseline,
}

# Handled separately in `main` because they take arguments or never return.
ARGUMENT_COMMANDS = (
    "abandon-lifecycle",
    "adopt-position",
    "backtest",
    "backtest-screener",
    "chart-verify",
    "crypto-screen",
    "dashboard",
    "portal",
    "reconcile-close",
    "kill-switch",
    "serve",
    "suggest-close",
)


def _package_version() -> str:
    try:
        return metadata.version("atlas-algotrader")
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(prog="atlas")
    # Single source of truth: registering a command in COMMANDS is enough to expose it, so
    # a command can never be dispatchable but rejected by the parser (or the reverse).
    parser.add_argument(
        "command",
        choices=(*sorted(COMMANDS), *ARGUMENT_COMMANDS),
    )
    parser.add_argument("target_symbol", nargs="?", default=None, help="Optional symbol for chart-verify or backtest")
    parser.add_argument("--symbol", default=None, help="Target asset symbol (e.g. BTCUSDT, TIAUSDT)")
    parser.add_argument("--venue", choices=("spot", "futures"), default="futures")
    parser.add_argument("--initial-equity", type=Decimal, default=Decimal("10000"))
    parser.add_argument("--intervals", default="1,4,8")
    parser.add_argument("--history-days", type=int, default=365)
    parser.add_argument(
        "--strategy-profile",
        type=StrategyProfile,
        choices=tuple(StrategyProfile),
        default=None,
        help="backtest-only profile override; does not change the deployment environment",
    )
    parser.add_argument(
        "--end-days-ago",
        type=int,
        default=0,
        help="end a replay this many days before now for an earlier holdout window",
    )
    parser.add_argument(
        "--stagnation-exit-bars",
        type=int,
        default=0,
        help="research-only: exit at the bar close when a trade has made insufficient progress",
    )
    parser.add_argument(
        "--stagnation-min-favorable-r",
        type=Decimal,
        default=Decimal("0.5"),
        help="minimum favorable R excursion required before the stagnation deadline",
    )
    parser.add_argument(
        "--cost-multiplier",
        type=Decimal,
        default=Decimal("1"),
        help="multiply spread, fees, stop slippage, and funding assumptions",
    )
    parser.add_argument(
        "--exit-policy",
        choices=("managed", "static"),
        default="managed",
        help="isolate entry alpha from exit management by replaying either exit policy",
    )
    parser.add_argument(
        "--untouched-holdout",
        action="store_true",
        help="attest that this time window was not used to choose strategy parameters",
    )
    parser.add_argument("--signal-id", default="")
    parser.add_argument(
        "--active",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="arm the kill switch; use --no-active to clear it",
    )
    parser.add_argument("--reason", default="")
    parser.add_argument("--exit-price", type=Decimal, default=Decimal("0"))
    parser.add_argument("--fees", type=Decimal, default=Decimal("0"))
    parser.add_argument(
        "--exit-reason", choices=("stop", "target", "manual", "liquidation"), default="manual"
    )
    parser.add_argument("--min-score", type=Decimal, default=Decimal("60.0"))
    parser.add_argument("--direction", choices=("long", "short"), default=None)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--max-symbols", type=int, default=30)
    parser.add_argument(
        "--mode",
        choices=("accumulation", "breakout"),
        default="accumulation",
        help="Screening objective: pre-breakout accumulation coils vs active momentum breakouts",
    )
    parser.add_argument("--symbols", default=None, help="Comma-separated list of symbols for multi-symbol screener backtest")
    parser.add_argument("--interval-hours", type=int, default=4, help="Screening frequency in hours for backtest (e.g. 1 or 4)")
    parser.add_argument("--risk-per-trade-pct", type=Decimal, default=Decimal("0.01"), help="Risk per trade as equity fraction (e.g. 0.01 = 1%%)")
    parser.add_argument("--entry-mode", choices=("pullback", "breakout", "both"), default="pullback", help="Entry execution mode: pullback limit or breakout stop")
    parser.add_argument("--max-open-trades", type=int, default=5, help="Maximum concurrent open positions in portfolio")
    parser.add_argument("--json", dest="as_json", action="store_true", default=False)
    args = parser.parse_args()
    if args.command == "backtest-screener":
        return asyncio.run(
            _backtest_screener(
                symbols_arg=args.symbols or args.symbol or args.target_symbol,
                history_days=int(args.history_days),
                interval_hours=int(args.interval_hours),
                min_score=Decimal(args.min_score),
                direction_arg=args.direction,
                entry_mode=str(args.entry_mode),
                initial_equity=Decimal(args.initial_equity),
                risk_per_trade_pct=Decimal(args.risk_per_trade_pct),
                max_open_trades=int(args.max_open_trades),
                as_json=bool(args.as_json),
            )
        )
    if args.command == "crypto-screen":
        return asyncio.run(
            _crypto_screen(
                min_score=Decimal(args.min_score),
                direction=args.direction,
                top_n=int(args.top_n),
                max_symbols=int(args.max_symbols),
                mode=str(args.mode),
                as_json=bool(args.as_json),
            )
        )
    if args.command in {"chart-verify", "dashboard"}:
        target = args.symbol or args.target_symbol
        return asyncio.run(_chart_verify(symbol=target, mode=str(args.mode)))
    if args.command == "kill-switch":
        if len(str(args.reason)) < 10:
            raise SystemExit(
                "kill-switch requires --reason of at least 10 characters; it is persisted "
                "and shown in every status report"
            )
        return asyncio.run(_set_kill_switch(active=bool(args.active), reason=str(args.reason)))
    if args.command == "suggest-close":
        if not str(args.signal_id):
            raise SystemExit("suggest-close requires --signal-id")
        return asyncio.run(_suggest_close(signal_id=str(args.signal_id)))
    if args.command == "adopt-position":
        if not str(args.signal_id) or len(str(args.reason)) < 10:
            raise SystemExit("adopt-position requires --signal-id and --reason")
        return asyncio.run(_adopt_position(signal_id=str(args.signal_id), reason=str(args.reason)))
    if args.command in {"abandon-lifecycle", "reconcile-close"}:
        if not str(args.signal_id):
            raise SystemExit(f"{args.command} requires --signal-id")
        if args.command == "abandon-lifecycle":
            if len(str(args.reason)) < 10:
                raise SystemExit(
                    "--reason must describe why the interlock is being overridden "
                    "(at least 10 characters); it is written to the audit log"
                )
            return asyncio.run(
                _abandon_lifecycle(signal_id=str(args.signal_id), reason=str(args.reason))
            )
        return asyncio.run(
            _reconcile_close(
                signal_id=str(args.signal_id),
                exit_price=Decimal(args.exit_price),
                fees=Decimal(args.fees),
                exit_reason=str(args.exit_reason),
            )
        )
    if args.command == "portal":
        return _portal()
    if args.command == "serve":
        uvicorn.run("algotrader.api.app:app", host="127.0.0.1", port=8000, reload=False)
        return 0
    if args.command == "backtest":
        intervals = tuple(int(value.strip()) for value in args.intervals.split(","))
        return asyncio.run(
            _backtest(
                symbol=str(args.symbol or args.target_symbol or "BTCUSDT"),
                venue=str(args.venue),
                initial_equity=Decimal(args.initial_equity),
                intervals=intervals,
                history_days=int(args.history_days),
                end_days_ago=int(args.end_days_ago),
                stagnation_exit_bars=int(args.stagnation_exit_bars),
                stagnation_min_favorable_r=Decimal(args.stagnation_min_favorable_r),
                strategy_profile=args.strategy_profile,
                cost_multiplier=Decimal(args.cost_multiplier),
                exit_policy=str(args.exit_policy),
                untouched_holdout=bool(args.untouched_holdout),
            )
        )
    return asyncio.run(COMMANDS[str(args.command)]())


if __name__ == "__main__":
    raise SystemExit(main())
