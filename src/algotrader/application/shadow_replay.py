"""Strict offline reproduction of the research arm, without constructing live clients."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from algotrader.application.scan_recording import screening_identity
from algotrader.application.scan_replay import ReplayMarket, ReplayMismatch, ReplayTape
from algotrader.application.shadow_budget import (
    ShadowBudget,
    ShadowBudgetExceeded,
    ShadowDeadlineExceeded,
)
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.errors import RateLimitError, VenueError
from algotrader.domain.scan_observations import ScanRecord
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.scan_archive import canonical_bytes


class _ShadowTape(ReplayTape):
    def __init__(self, calls: Any, *, deadline_cancel: bool = False) -> None:
        super().__init__(calls)
        self.deadline_cancel = deadline_cancel

    def take(self, method: str, arguments: dict[str, Any]) -> Any:
        group = self.calls.get(canonical_bytes((method, arguments)), [])
        error = group[0].error if group else None
        try:
            return super().take(method, arguments)
        except ReplayMismatch:
            raise
        except asyncio.CancelledError as exc:
            if self.deadline_cancel:
                raise ShadowDeadlineExceeded("recorded outer deadline cancellation") from exc
            raise
        except RuntimeError as exc:
            known: dict[str, type[Exception]] = {
                "ShadowBudgetExceeded": ShadowBudgetExceeded,
                "ValueError": ValueError,
                "VenueError": VenueError,
                "RateLimitError": RateLimitError,
                "RuntimeError": RuntimeError,
            }
            if error not in known:
                raise ReplayMismatch("unsupported recorded failure type") from exc
            raise known[error]("recorded provider failure") from exc


class _ShadowMarket(ReplayMarket):
    def __init__(self, tape: ReplayTape, policy: ShadowPolicy, timing: dict[str, Any]) -> None:
        super().__init__(tape)
        self.budget = ShadowBudget(policy)
        self.snapshot_started_at = timing.get("snapshot_provider_requested_at")
        self.snapshot_completed_at = timing.get("snapshot_provider_finished_at")

    async def decision_tickers(self) -> Any:
        return self.tape.take("decision_tickers", {})


async def replay_shadow(record: ShadowRunRecord, parent: ScanRecord) -> dict[str, Any]:
    if record.schema_version != 1 or record.parent_id != parent.scan_id:
        raise ReplayMismatch("shadow schema/parent identity mismatch")
    if record.parent_checksum != hashlib.sha256(canonical_bytes(parent)).hexdigest():
        raise ReplayMismatch("shadow parent checksum mismatch")
    current = screening_identity()
    for key in ("source_hash", "policy", "python", "packages"):
        if record.identity.get(key) != current[key]:
            raise ReplayMismatch("shadow identity mismatch: " + key)
    if record.identity.get("configuration") != parent.identity.get("configuration"):
        raise ReplayMismatch("shadow configuration identity mismatch")
    tape = _ShadowTape(record.inputs, deadline_cancel=record.error == "TimeoutError")
    times = iter(record.timing["clock_times"])
    policy = ShadowPolicy(**record.policy)
    result = await ShadowRunner(
        _ShadowMarket(tape, policy, record.timing), clock=lambda: next(times)
    ).run(parent, policy, replay_termination=record.timing.get("termination"))
    tape.assert_consumed()
    for field in (
        "status",
        "admission",
        "results",
        "labels",
        "coverage",
        "journal",
        "reused",
        "error",
        "timing",
        "identity",
    ):
        if getattr(result, field) != getattr(record, field):
            raise ReplayMismatch("shadow replay differs: " + field)
    if next(times, None) is not None:
        raise ReplayMismatch("unused recorded clock readings")
    return {
        "shadow_id": record.shadow_id,
        "matches": True,
        "status": record.status,
        "comparison": "staggered_shadow",
        "validation_status": "uncalibrated_research",
    }
