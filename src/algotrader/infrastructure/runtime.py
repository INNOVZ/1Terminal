from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import structlog
from pydantic import SecretStr

from algotrader.adapters.binance.bridge import LiveDataTestnetBridge
from algotrader.adapters.binance.client import BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.binance.spot import BinanceSpotAdapter
from algotrader.adapters.paper.broker import PaperBroker
from algotrader.application.execution import ExecutionCoordinator
from algotrader.application.manage import PositionManagementCoordinator
from algotrader.application.portfolio import PortfolioStateTracker
from algotrader.application.position_manager import PositionManager
from algotrader.application.risk import PortfolioRiskManager
from algotrader.application.scheduler import CycleScheduler
from algotrader.application.screener import MultiFactorScreener
from algotrader.application.service import TradingSystem, VenueContext
from algotrader.application.strategy import TrendMomentumStrategy
from algotrader.config import ExecutionMode, Settings
from algotrader.domain.errors import ConfigurationError
from algotrader.domain.models import AccountSnapshot, Venue
from algotrader.domain.ports import ExecutionPort, MarketDataPort
from algotrader.infrastructure.sqlite_state import SqliteStateRepository

log = structlog.get_logger()

SPOT_TESTNET_URL = "https://testnet.binance.vision"
SPOT_LIVE_URL = "https://api.binance.com"
FUTURES_TESTNET_URL = "https://demo-fapi.binance.com"
FUTURES_LIVE_URL = "https://fapi.binance.com"


@dataclass(slots=True)
class Runtime:
    settings: Settings
    system: TradingSystem
    clients: tuple[BinanceHttpClient, ...]
    scheduler: CycleScheduler
    repository: SqliteStateRepository
    spot_market: BinanceSpotAdapter
    futures_market: BinanceUsdmAdapter

    def start_automation(self) -> None:
        if self.settings.automation_enabled:
            self.scheduler.start()

    async def close(self) -> None:
        errors: list[Exception] = []
        try:
            await self.scheduler.stop()
        except Exception as exc:
            errors.append(exc)
        results = await asyncio.gather(
            *(client.close() for client in self.clients), return_exceptions=True
        )
        errors.extend(result for result in results if isinstance(result, Exception))
        if errors:
            raise RuntimeError(f"runtime cleanup failed in {len(errors)} component(s)") from errors[
                0
            ]

    async def readiness(self) -> tuple[bool, dict[str, object]]:
        """Check local dependencies required for this instance to accept traffic.

        Venue reachability is validated during startup and monitored by cycle failures. A
        readiness probe must stay cheap and must not turn a transient Binance response into
        a GCP restart loop, so it checks durable state plus the automation task itself.
        """
        try:
            await self.repository.get_state("kill_switch")
            state_store = "ok"
        except Exception as exc:
            log.error(
                "readiness_state_store_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False, {
                "status": "not_ready",
                "state_store": "failed",
            }
        scheduler_ready = not self.settings.automation_enabled or self.scheduler.running
        return scheduler_ready, {
            "status": "ready" if scheduler_ready else "not_ready",
            "state_store": state_store,
            "scheduler": "running" if self.scheduler.running else "stopped",
            "automation_enabled": self.settings.automation_enabled,
        }


async def _sync_and_validate(
    client: BinanceHttpClient,
    time_path: str,
    label: str,
    *,
    validate_auth: bool = False,
    auth_test_path: str = "",
) -> None:
    """Synchronize clock and optionally validate signed-request credentials."""
    try:
        await client.sync_clock(time_path)
        log.info("clock_synced", venue=label, offset_ms=client._clock_offset_ms)
    except Exception as exc:
        log.warning("clock_sync_failed", venue=label, error=str(exc))

    if validate_auth and auth_test_path:
        try:
            await client.request("GET", auth_test_path, signed=True)
            log.info("credentials_valid", venue=label)
        except Exception as exc:
            raise ConfigurationError(
                f"{label} credentials are invalid or expired. "
                f"Please regenerate API keys from the testnet portal. "
                f"Error: {exc}"
            ) from exc


async def build_runtime(settings: Settings) -> Runtime:
    live = settings.execution_mode is ExecutionMode.LIVE
    uses_signed = settings.execution_mode is not ExecutionMode.PAPER
    spot_client = BinanceHttpClient(
        SPOT_LIVE_URL
        if live or settings.execution_mode is ExecutionMode.PAPER
        else SPOT_TESTNET_URL,
        settings.binance_spot_api_key,
        settings.binance_spot_api_secret,
    )
    futures_client = BinanceHttpClient(
        FUTURES_LIVE_URL
        if live or settings.execution_mode is ExecutionMode.PAPER
        else FUTURES_TESTNET_URL,
        settings.binance_futures_api_key,
        settings.binance_futures_api_secret,
    )

    # Synchronize clocks and validate credentials before any trading operations.
    # In paper mode, signed requests are not used, so only clock sync is needed.
    try:
        await asyncio.gather(
            _sync_and_validate(
                spot_client,
                "/api/v3/time",
                "Binance Spot",
                validate_auth=uses_signed,
                auth_test_path="/api/v3/account",
            ),
            _sync_and_validate(
                futures_client,
                "/fapi/v1/time",
                "Binance Futures",
                validate_auth=uses_signed,
                auth_test_path="/fapi/v2/balance",
            ),
        )
    except Exception:
        await asyncio.gather(spot_client.close(), futures_client.close())
        raise

    repository = SqliteStateRepository(
        settings.state_db_path,
        research_retention_days=settings.research_retention_days,
        event_retention_days=settings.event_retention_days,
    )
    await repository.initialize()
    spot_market = BinanceSpotAdapter(
        spot_client,
        ignored_balance_assets=(
            settings.ignored_spot_balance_assets
            if settings.execution_mode is ExecutionMode.TESTNET
            else ()
        ),
        repository=repository,
        use_testnet_balance_baseline=settings.execution_mode is ExecutionMode.TESTNET,
    )
    futures_market = BinanceUsdmAdapter(
        futures_client,
        require_hedge_mode=settings.binance_futures_hedge_mode,
        max_leverage=settings.max_futures_leverage,
    )
    market_clients: tuple[BinanceHttpClient, ...] = ()
    spot_data: MarketDataPort = spot_market
    futures_data: MarketDataPort = futures_market
    if settings.execution_mode is ExecutionMode.TESTNET and settings.testnet_uses_live_market_data:
        # Unauthenticated clients: only public market-data endpoints are reached, so no key
        # is ever presented to the live venue and no order can be routed there.
        spot_data_client = BinanceHttpClient(SPOT_LIVE_URL, SecretStr(""), SecretStr(""))
        futures_data_client = BinanceHttpClient(FUTURES_LIVE_URL, SecretStr(""), SecretStr(""))
        market_clients = (spot_data_client, futures_data_client)
        spot_data = LiveDataTestnetBridge(
            live=BinanceSpotAdapter(spot_data_client), execution_venue=spot_market
        )
        futures_data = LiveDataTestnetBridge(
            live=BinanceUsdmAdapter(
                futures_data_client,
                require_hedge_mode=False,
                max_leverage=settings.max_futures_leverage,
            ),
            execution_venue=futures_market,
        )
        log.info("testnet_market_data_source", source="live_public_api")

    if settings.execution_mode is ExecutionMode.PAPER:
        spot_equity = settings.paper_starting_equity * settings.paper_spot_allocation
        futures_equity = settings.paper_starting_equity - spot_equity
        spot_execution: ExecutionPort = PaperBroker(spot_data, starting_equity=spot_equity)
        futures_execution: ExecutionPort = PaperBroker(futures_data, starting_equity=futures_equity)
    else:
        spot_execution = spot_market
        futures_execution = futures_market

    portfolio_state = PortfolioStateTracker(repository)
    risk = PortfolioRiskManager(settings)

    async def consolidated_account() -> AccountSnapshot:
        accounts = await asyncio.gather(
            spot_execution.account_snapshot(), futures_execution.account_snapshot()
        )
        account = AccountSnapshot(
            venue=Venue.PAPER,
            equity=sum((account.equity for account in accounts), Decimal("0")),
            available_balance=sum(
                (account.available_balance for account in accounts), Decimal("0")
            ),
            realized_pnl_today=sum(
                (account.realized_pnl_today for account in accounts), Decimal("0")
            ),
            peak_equity=sum((account.peak_equity for account in accounts), Decimal("0")),
            positions=tuple(position for account in accounts for position in account.positions),
        )
        return await portfolio_state.enrich(account)

    position_manager = PositionManager(settings)
    contexts = tuple(
        VenueContext(
            market_data=market,
            execution=execution,
            coordinator=ExecutionCoordinator(execution, repository, risk, consolidated_account),
            management=PositionManagementCoordinator(
                execution, market, repository, position_manager, settings
            ),
        )
        for market, execution in (
            (spot_data, spot_execution),
            (futures_data, futures_execution),
        )
    )
    system = TradingSystem(
        contexts=contexts,
        screener=MultiFactorScreener(settings),
        strategy=TrendMomentumStrategy(settings),
        repository=repository,
        execution_cooldown_hours=settings.execution_cooldown_hours,
        signal_expiry_bars=settings.signal_expiry_bars,
        execution_lease_seconds=settings.execution_lease_seconds,
    )
    scheduler = CycleScheduler(
        system,
        repository,
        interval_hours=settings.signal_discovery_interval_hours,
        grace_seconds=settings.bar_close_grace_seconds,
        failure_limit=settings.scheduler_failure_limit,
    )
    return Runtime(
        settings=settings,
        system=system,
        clients=(spot_client, futures_client, *market_clients),
        scheduler=scheduler,
        repository=repository,
        spot_market=spot_market,
        futures_market=futures_market,
    )
