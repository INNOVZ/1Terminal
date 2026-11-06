"""Per-child monotonic transport limits; no production configuration effects."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from algotrader.domain.shadow_discovery import ShadowPolicy


class ShadowBudgetExceeded(RuntimeError):
    pass


class ShadowDeadlineExceeded(TimeoutError):
    """Recorded outer deadline, distinct from external task cancellation."""


class ShadowBudget:
    def __init__(
        self,
        policy: ShadowPolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        expires_at: float | None = None,
    ) -> None:
        self.policy = policy
        self._clock = monotonic
        self._started = monotonic()
        if expires_at is not None and not math.isfinite(expires_at):
            raise ValueError("budget expiry must be finite")
        self._expires_at = (
            min(self._started + policy.deadline_seconds, expires_at)
            if expires_at is not None
            else self._started + policy.deadline_seconds
        )
        self.attempts = 0
        self.observed_weight_1m: int | None = None
        self.exhausted_reason: str | None = None

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._expires_at - self._clock())

    def before_attempt(self) -> None:
        if self.remaining_seconds <= 0:
            self.exhausted_reason = "child deadline exhausted"
            raise ShadowBudgetExceeded("child deadline exhausted")
        if self.attempts >= self.policy.max_attempts:
            self.exhausted_reason = "child HTTP attempt limit exhausted"
            raise ShadowBudgetExceeded("child HTTP attempt limit exhausted")
        self.attempts += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "attempt_limit": self.policy.max_attempts,
            "remaining_seconds": self.remaining_seconds,
            "observed_weight_1m": self.observed_weight_1m,
        }
