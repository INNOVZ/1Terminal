"""Versioned long-only recovery selection and conservative bounded-pool controls."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.recovery_screener import RecoveryScreener
from algotrader.domain.crypto_screener_models import CryptoOpportunity
from algotrader.domain.discovery_selection import ACTIVITY_POLICY, activity_watch_selection
from algotrader.domain.models import Direction, Instrument, Quote


class ActivityRecoveryScreener(RecoveryScreener):
    async def screen(self, *args: Any, **kwargs: Any) -> list[CryptoOpportunity]:
        if kwargs.get("target_direction") not in (None, Direction.LONG):
            raise ValueError("activity recovery is a long-only research policy")
        kwargs["target_direction"] = Direction.LONG
        return await super().screen(*args, **kwargs)

    def _extend_selection(
        self, selected: list[Instrument], liquid: list[Instrument],
        quotes: dict[str, Quote], max_symbols: int | None,
    ) -> list[Instrument]:
        return activity_watch_selection(selected, liquid, quotes, max_symbols or 265,
                                        self._decision_at or datetime.now(UTC), rotation_at=self._evaluated_at)

    def _probe_rejection_reason(self, quote: Quote, direction: Direction | None) -> str | None:
        at = self._decision_at or datetime.now(UTC)
        if (direction is Direction.LONG and quote.funding_rate is not None
                and quote.funding_rate.is_finite() and quote.funding_rate > Decimal("0.00025")
                and quote.observed_at.utcoffset() is not None
                and -5 <= (at - quote.observed_at).total_seconds() <= 600):
            return ACTIVITY_POLICY + ":fresh_long_funding_veto"
        return None


class PoolControlScreener(CryptoOpportunityScreener):
    def _candidate_is_discovery_tier(self, selected_tier: bool) -> bool:
        return True


class PoolRecoveryScreener(ActivityRecoveryScreener):
    def _candidate_is_discovery_tier(self, selected_tier: bool) -> bool:
        return True
