from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation

from algotrader.domain.models import AccountSnapshot
from algotrader.domain.ports import PortfolioRepository

RISK_STATE_KEY = "portfolio_risk"


class PortfolioStateTracker:
    """Maintains UTC daily baseline and all-time process peak across restarts."""

    def __init__(self, repository: PortfolioRepository) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()

    async def enrich(
        self, account: AccountSnapshot, *, observed_at: datetime | None = None
    ) -> AccountSnapshot:
        now = observed_at or datetime.now(UTC)
        trading_day = now.astimezone(UTC).date().isoformat()
        day_started_at = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
        async with self._lock:
            state = await self._repository.get_state(RISK_STATE_KEY)
            day_start, peak = self._parse_state(state, account.equity, trading_day)
            if not state or state.get("trading_day") != trading_day:
                day_start = account.equity
            peak = max(peak, account.equity)
            await self._repository.set_state(
                RISK_STATE_KEY,
                {
                    "trading_day": trading_day,
                    "day_start_equity": str(day_start),
                    "peak_equity": str(peak),
                },
            )
        realized_pnl = await self._repository.realized_pnl_since(day_started_at)
        # Mark-to-market delta remains a separate capital-protection signal. Deposits and
        # withdrawals still require reconciliation so they do not look like trading PnL.
        return replace(
            account,
            realized_pnl_today=realized_pnl,
            mark_to_market_pnl_today=account.equity - day_start,
            day_start_equity=day_start,
            peak_equity=peak,
            observed_at=now,
        )

    @staticmethod
    def _parse_state(
        state: dict[str, object] | None, equity: Decimal, trading_day: str
    ) -> tuple[Decimal, Decimal]:
        if state is None:
            return equity, equity
        try:
            day_start = Decimal(str(state["day_start_equity"]))
            peak = Decimal(str(state["peak_equity"]))
        except (KeyError, InvalidOperation, ValueError):
            return equity, equity
        if state.get("trading_day") != trading_day:
            day_start = equity
        return day_start, max(peak, equity)
