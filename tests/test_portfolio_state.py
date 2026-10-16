from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from algotrader.application.portfolio import PortfolioStateTracker
from algotrader.domain.models import AccountSnapshot, Venue
from algotrader.infrastructure.sqlite_state import SqliteStateRepository


async def test_portfolio_baseline_and_peak_survive_restart(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    tracker = PortfolioStateTracker(repository)
    now = datetime(2026, 8, 2, 12, tzinfo=UTC)

    initial = AccountSnapshot(
        Venue.PAPER, Decimal("100"), Decimal("100"), Decimal("0"), Decimal("100")
    )
    assert (await tracker.enrich(initial, observed_at=now)).realized_pnl_today == 0

    losing = AccountSnapshot(Venue.PAPER, Decimal("97"), Decimal("97"), Decimal("0"), Decimal("97"))
    enriched = await PortfolioStateTracker(repository).enrich(
        losing, observed_at=now + timedelta(hours=1)
    )
    assert enriched.realized_pnl_today == Decimal("0")
    assert enriched.mark_to_market_pnl_today == Decimal("-3")
    assert enriched.peak_equity == Decimal("100")
    assert enriched.drawdown == Decimal("0.03")

    next_day = await tracker.enrich(losing, observed_at=now + timedelta(days=1))
    assert next_day.realized_pnl_today == 0
    assert next_day.mark_to_market_pnl_today == 0
    assert next_day.peak_equity == Decimal("100")
