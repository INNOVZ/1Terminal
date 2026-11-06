"""Manual bounded discovery arm. The production runtime never invokes this module."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, cast
from uuid import uuid4

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.alt_momentum import _return_over
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import CallTape, RecordingMarketData, screening_identity
from algotrader.application.shadow_budget import (
    ShadowBudget,
    ShadowBudgetExceeded,
    ShadowDeadlineExceeded,
)
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Instrument
from algotrader.domain.ports import MarketDataPort
from algotrader.domain.scan_observations import ScanDecisionJournal, ScanRecord
from algotrader.domain.screener_policy import publication_rejection_reasons
from algotrader.domain.shadow_discovery import (
    ShadowPolicy,
    admit_shadow,
    classify_shadow,
    setup_family,
)
from algotrader.domain.shadow_observations import ShadowRunRecord
from algotrader.infrastructure.scan_archive import canonical_bytes


class ShadowMarketPort(Protocol):
    budget: ShadowBudget

    async def candles(
        self, instrument: Instrument, interval: str, limit: int
    ) -> Sequence[Candle]: ...
    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot: ...
    async def decision_tickers(self) -> Sequence[dict[str, Any]]: ...


class _RecordedMacro:
    def __init__(self, context: MacroSentimentContext) -> None:
        self.context = context

    async def get_context(self) -> MacroSentimentContext:
        return self.context


def _fresh(observed: datetime, now: datetime, max_age: int) -> bool:
    return observed.utcoffset() is not None and -5 <= (now - observed).total_seconds() <= max_age


def _usable_macro(context: MacroSentimentContext, now: datetime, policy: ShadowPolicy) -> bool:
    components = [
        value
        for value in (context.fear_greed, context.stablecoin_supply, context.market_breadth)
        if value is not None
    ]
    return (
        bool(components)
        and _fresh(context.observed_at, now, policy.macro_context_max_age_seconds)
        and all(
            _fresh(value.observed_at, now, policy.macro_component_max_age_seconds)
            for value in components
        )
    )


def _usable_bars(bars: Sequence[Candle], interval: str, now: datetime) -> bool:
    # Short weekly histories must retain the core evaluator's resampling fallback.
    seconds, minimum = {"1h": (3600, 120), "4h": (14400, 40), "1w": (604800, 1)}[interval]
    if any(bar.closed and bar.closed_at > now for bar in bars):
        return False
    closed = sorted(
        (bar for bar in bars if bar.closed and bar.closed_at <= now), key=lambda bar: bar.closed_at
    )
    if interval == "1w" and not closed:
        return True
    return (
        len(closed) >= minimum
        and len({bar.opened_at for bar in closed}) == len(closed)
        and all(
            abs((bar.closed_at - bar.opened_at).total_seconds() - seconds) <= 1 for bar in closed
        )
        and (now - closed[-1].closed_at).total_seconds() <= seconds + 300
    )


class ShadowRunner:
    def __init__(
        self, market: ShadowMarketPort, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.market = market
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        parent: ScanRecord,
        policy: ShadowPolicy,
        *,
        replay_termination: dict[str, Any] | None = None,
    ) -> ShadowRunRecord:
        clocks: list[datetime] = []

        def now() -> datetime:
            value = self.clock()
            clocks.append(value)
            return value

        started = now()
        admission = admit_shadow(parent, policy, started)
        current = await asyncio.to_thread(screening_identity)
        for key in ("policy", "source_hash", "python", "packages"):
            if parent.identity.get(key) != current[key]:
                raise ValueError("parent screening identity mismatch: " + key)
        if self.market.budget.policy != policy:
            raise ValueError("market budget and run policy differ")
        settings = Settings(**{**parent.identity["configuration"], "_env_file": None})
        tape, journal = CallTape(), ScanDecisionJournal()
        # The evaluator consumes preloaded bars/derivatives; it cannot start another universe scan.
        recorded = RecordingMarketData(cast(Any, self.market), cast(Any, self.market), tape)
        reused: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        timings: list[dict[str, Any]] = []
        status, error = "complete", None
        macro_calls = [
            call
            for call in parent.inputs
            if call.method == "macro"
            and call.error is None
            and isinstance(call.result, MacroSentimentContext)
            and _usable_macro(call.result, started, policy)
        ]
        btc_calls = [
            call
            for call in parent.inputs
            if call.method == "candles"
            and call.error is None
            and call.arguments.get("interval") == "1h"
            and call.arguments["instrument"].base_asset == "BTC"
            and _usable_bars(call.result, "1h", started)
        ]

        async def bars_for(inst: Instrument, interval: str, limit: int) -> Sequence[Candle]:
            calls = [
                call
                for call in parent.inputs
                if call.method == "candles"
                and call.error is None
                and call.arguments == {"instrument": inst, "interval": interval, "limit": limit}
                and _usable_bars(call.result, interval, started)
            ]
            if calls:
                call = calls[-1]
                reused.append(
                    {"method": call.method, "index": call.index, "arguments": call.arguments}
                )
                bars = cast(Sequence[Candle], call.result)
                if interval == "1w" and sum(bar.closed for bar in bars) < 15:
                    journal.note(
                        inst.id.symbol,
                        "input",
                        "weekly_resampled_from_4h",
                        observed_weekly_bars=sum(bar.closed for bar in bars),
                    )
                return bars
            bars = await recorded.candles(inst, interval, limit)
            if not _usable_bars(bars, interval, now()):
                raise ValueError("unavailable or stale " + interval + " candles")
            if interval == "1w" and sum(bar.closed for bar in bars) < 15:
                journal.note(
                    inst.id.symbol,
                    "input",
                    "weekly_resampled_from_4h",
                    observed_weekly_bars=sum(bar.closed for bar in bars),
                )
            return bars

        processed: set[str] = set()
        rows: Sequence[dict[str, Any]] = ()
        phase = "admission"
        active_symbol: str | None = None
        snapshot_requested: datetime | None = None
        snapshot_arrived: datetime | None = None
        snapshot_finished: datetime | None = None
        try:
            async with asyncio.timeout(self.market.budget.remaining_seconds):
                for item in admission.selected:
                    symbol = item.instrument.id.symbol
                    active_symbol = symbol
                    if not macro_calls or not btc_calls:
                        journal.note(symbol, "shadow", "context_unavailable")
                        processed.add(symbol)
                        status = "partial"
                        continue
                    macro = cast(MacroSentimentContext, macro_calls[-1].result)
                    btc = cast(Sequence[Candle], btc_calls[-1].result)
                    try:
                        phase = "candles"
                        candles = tuple(
                            [
                                await bars_for(item.instrument, interval, limit)
                                for interval, limit in (("1h", 240), ("4h", 480), ("1w", 40))
                            ]
                        )
                        phase = "derivatives"
                        derivatives = await recorded.derivatives_snapshot(item.instrument)
                        evaluated = now()
                        if derivatives.degraded_telemetry or not _fresh(
                            derivatives.observed_at, evaluated, policy.derivatives_max_age_seconds
                        ):
                            status = "partial"
                            journal.note(
                                symbol,
                                "input",
                                "derivatives_unavailable",
                                degraded=derivatives.degraded_telemetry,
                                observed_at=derivatives.observed_at,
                            )
                            processed.add(symbol)
                            continue
                        evaluator = CryptoOpportunityScreener(
                            settings, _RecordedMacro(macro), evaluated_at=evaluated, journal=journal
                        )
                        quote = next(
                            quote
                            for call in parent.inputs
                            if call.method == "quotes" and call.error is None
                            for quote in call.result
                            if quote.instrument_id == item.instrument.id
                        )
                        btc_ret = _return_over(btc, 1)
                        phase = "evaluation"
                        if (
                            replay_termination is not None
                            and replay_termination.get("phase") == phase
                            and replay_termination.get("symbol") == symbol
                        ):
                            if replay_termination.get("cause") == "TimeoutError":
                                raise ShadowDeadlineExceeded("recorded evaluation deadline")
                            if replay_termination.get("cause") == "CancelledError":
                                raise asyncio.CancelledError
                        opportunities = await evaluator._evaluate_single(
                            inst=item.instrument,
                            quote=quote,
                            market=cast(MarketDataPort, recorded),
                            derivatives_port=recorded,
                            btc_candles=btc,
                            macro_context=macro,
                            mode=ScreenerMode.BREAKOUT,
                            target_direction=Direction.LONG,
                            is_btc_cascade=btc_ret <= Decimal("-0.025")
                            or _return_over(btc, 4) <= Decimal("-0.045"),
                            btc_ret_1h=btc_ret,
                            min_score=Decimal(str(parent.request.get("min_score", "68"))),
                            is_discovery_tier=True,
                            preloaded_candles=candles,
                            explicit_derivatives=derivatives,
                        )
                        coi = parent.request.get("coi_score")
                        if coi is None:
                            coi = getattr(macro, "coi_score", None)
                        for opportunity in opportunities:
                            reasons = publication_rejection_reasons(
                                opportunity, coi_score=float(coi) if coi is not None else None
                            )
                            results.append(
                                {
                                    **opportunity.as_dict(),
                                    "qualified": not reasons,
                                    "rejection_reasons": reasons,
                                    "arm": "discovery_shadow",
                                    "family": setup_family(opportunity.setup_label.value),
                                    "bucket": item.bucket,
                                }
                            )
                        if not opportunities:
                            journal.note(symbol, "shadow", "no_setup")
                        timings.append({"symbol": symbol, "evaluated_at": evaluated})
                    except (ShadowBudgetExceeded, ShadowDeadlineExceeded):
                        raise
                    except Exception as exc:
                        status = "partial"
                        journal.note(
                            symbol,
                            "shadow",
                            "input_or_evaluation_failure",
                            error=type(exc).__name__,
                        )
                    processed.add(symbol)
                active_symbol, phase = None, "snapshot"
                snapshot_requested = now()
                try:
                    rows = await tape.capture("decision_tickers", {}, self.market.decision_tickers)
                    snapshot_arrived = now()
                finally:
                    snapshot_finished = now()
        except (ShadowBudgetExceeded, TimeoutError) as exc:
            status, error = (
                "partial",
                "TimeoutError" if isinstance(exc, TimeoutError) else type(exc).__name__,
            )
        except asyncio.CancelledError:
            # Return inspectable cancellation evidence; the job must persist then propagate cancellation.
            status, error = "cancelled", "CancelledError"
        except Exception as exc:
            status, error = "partial", type(exc).__name__
        for item in admission.selected:
            if item.instrument.id.symbol not in processed:
                journal.note(item.instrument.id.symbol, "shadow", "run_interrupted", error=error)
        classified = now()
        classification = classify_shadow(parent.cohort, results, rows, classified, policy)
        if not classification.coverage["complete"] or tape.recording_error:
            status = "partial" if status == "complete" else status
        reused.extend(
            {"method": call.method, "index": call.index, "arguments": call.arguments}
            for call in (macro_calls[-1:] + btc_calls[-1:])
        )
        completed = now()
        return ShadowRunRecord(
            str(uuid4()),
            parent.scan_id,
            hashlib.sha256(canonical_bytes(parent)).hexdigest(),
            started,
            completed,
            status,
            {**current, "configuration": parent.identity["configuration"]},
            asdict(policy),
            parent.cohort,
            admission.decisions,
            tuple(tape.calls),
            tuple(results),
            classification.labels,
            classification.coverage,
            {
                "comparison": "staggered_shadow",
                "parent_completed_at": parent.completed_at,
                "admission_at": started,
                "classified_at": classified,
                "snapshot_requested_at": snapshot_requested,
                "snapshot_arrived_at": snapshot_arrived,
                "snapshot_finished_at": snapshot_finished,
                "snapshot_provider_requested_at": getattr(self.market, "snapshot_started_at", None),
                "snapshot_provider_finished_at": getattr(
                    self.market, "snapshot_completed_at", None
                ),
                "clock_times": tuple(clocks),
                "termination": {"cause": error, "phase": phase, "symbol": active_symbol}
                if error
                else None,
                "evaluations": timings,
                "parent_lag_seconds": (started - parent.completed_at).total_seconds(),
            },
            self.market.budget.snapshot(),
            journal.snapshot(),
            tuple(reused),
            error=error or tape.recording_error,
        )
