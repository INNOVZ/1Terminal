"""Compose a disabled-by-default continuation research candidate from public data."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.bearish_continuation import POLICY_VERSION, TITLES, evaluate_continuation
from algotrader.domain.crypto_screener_models import (
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.indicators import ema
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Instrument, Quote

D = Decimal


def score_continuation(
    inst: Instrument,
    quote: Quote,
    candles_1h: Sequence[Candle],
    candles_4h: Sequence[Candle],
    *,
    now: datetime,
    derivatives: DerivativesSnapshot | None,
    note: Any,
    macro_narrative: str,
    beta: Decimal,
    residual: Decimal,
    is_discovery_tier: bool,
) -> CryptoOpportunity | None:
    if (
        quote.observed_at.tzinfo is None
        or not quote.is_valid_book
        or not quote.is_fresh_at(now, max_age_seconds=300.0, max_future_seconds=5.0)
        or quote.quote_volume_24h < D("500000")
        or (quote.ask - quote.bid) / quote.last > D("0.005")
    ):
        note(inst.id.symbol, "continuation_rejection", "quote_unavailable")
        return None
    result = evaluate_continuation(
        candles_1h,
        candles_4h,
        now=now,
        price=quote.last,
        tick=inst.price_tick,
        derivatives=derivatives,
        spread_fraction=(quote.ask - quote.bid) / quote.last,
    )
    note(
        inst.id.symbol,
        "continuation_gates",
        result.reason,
        enriched=derivatives is not None,
        policy=POLICY_VERSION,
        gates=result.gates,
        plan=result.plan.as_dict() if result.plan else None,
    )
    if not result.structurally_ready or (derivatives is not None and not result.confirmed):
        return None
    plan = result.plan
    assert plan is not None

    # Transparent bounded ranking factors, not a fitted outcome probability.
    def clip(value: Decimal) -> Decimal:
        return max(D("0"), min(D("1"), value))

    q = sorted(
        (c for c in candles_4h if c.closed and c.closed_at <= now), key=lambda c: c.closed_at
    )
    means = ema([c.close for c in q], 50)
    h = sorted(
        (c for c in candles_1h if c.closed and c.closed_at <= now), key=lambda c: c.closed_at
    )
    rejection = next(c for c in h if c.closed_at == plan.rejection_at)
    prior = [c for c in h if plan.break_at < c.closed_at < plan.rejection_at]
    break_bar = next(c for c in h if c.closed_at == plan.break_at)
    ref_volume = sum((c.volume for c in prior), D("0")) / len(prior) if prior else break_bar.volume
    trend = D("50") + D("50") * clip((means[-4] - means[-1]) / (plan.atr * D("0.5")))
    structure = D("50") + D("50") * clip((plan.support - rejection.close) / (plan.atr * D("0.5")))
    room = D("50") + D("50") * clip((plan.net_rr - D("1.5")) / D("1.5"))
    volume = D("50") + D("50") * clip(rejection.volume / ref_volume - D("1"))
    ci = derivatives.continuation_inputs if derivatives else None
    flow = (
        (
            D("50")
            + D("25") * clip((D("1") - ci["taker_ratio_1h"]) / D("0.25"))
            + D("25") * clip(ci["oi_change_4h"] / D("0.05"))
        )
        if result.confirmed and ci
        else D("0")
    )
    structural_score = (
        trend * D("0.20") + structure * D("0.20") + room * D("0.25") + volume * D("0.15")
    )
    score = (
        structural_score + flow * D("0.20") if result.confirmed else structural_score / D("0.80")
    ).quantize(D("0.01"))
    breakdown = CryptoScreenerScoreBreakdown(
        trend, structure, flow, D("0"), volume, room, D("1"), score
    )
    checklist = tuple(
        (title, gate is True, "verified" if gate is True else "unknown")
        for title, gate in zip(TITLES, result.gates, strict=True)
    )
    risk = plan.stop - plan.entry
    rr = (plan.entry - plan.target) / risk
    return CryptoOpportunity(
        instrument=inst,
        direction=Direction.SHORT,
        regime=CryptoMarketRegime.STRONG_TREND_DOWN,
        setup_label=SetupLabel.BREAKDOWN_RETEST_SHORT,
        score=score,
        conviction=score,
        score_breakdown=breakdown,
        current_price=quote.last,
        atr14=plan.atr,
        suggested_entry=plan.entry,
        suggested_stop=plan.stop,
        suggested_targets=(plan.target,),
        risk_reward_ratio=rr,
        risk_reward_target_index=1,
        btc_beta=beta,
        residual_return_24h=residual,
        funding_rate=derivatives.funding_rate if derivatives else None,
        oi_change_1h=derivatives.open_interest_change_1h if derivatives else None,
        oi_change_7d=derivatives.open_interest_change_7d if derivatives else None,
        taker_ratio=derivatives.taker_buy_sell_ratio if derivatives else None,
        taker_ratio_1h=derivatives.taker_buy_sell_ratio if derivatives else None,
        taker_ratio_4h=derivatives.taker_buy_sell_ratio_4h if derivatives else None,
        taker_ratio_window_hours=1,
        rationale=(
            "Closed support break followed by failed reclaim",
            "Experimental uncalibrated continuation policy",
        ),
        macro_narrative=macro_narrative,
        breakout_trigger=plan.entry,
        breakout_stop=plan.stop,
        execution_mode="BREAKDOWN_TRIGGER",
        breakout_readiness_status="BREAKDOWN_TRIGGER_PENDING",
        checklist=checklist,
        evaluated_at=now,
        estimated_win_rate_pct=D("50"),
        expected_value_r=(plan.net_rr - 1) / 2,
        research_policy_version=POLICY_VERSION,
        is_discovery_tier=is_discovery_tier,
        continuation_evidence={
            "policy_version": POLICY_VERSION,
            "plan": plan.as_dict(),
            "derivatives_confirmed": result.confirmed,
            "decision_at": now.isoformat(),
            "quote_as_of": quote.observed_at.isoformat(),
            "feature_1h_closed_at": h[-1].closed_at.isoformat(),
            "feature_4h_closed_at": q[-1].closed_at.isoformat(),
            "aligned_window_end": ci["oi_window_end"].isoformat() if ci else None,
            "ranking_components": {
                "trend": str(trend),
                "retest": str(structure),
                "room": str(room),
                "volume": str(volume),
                "flow": str(flow),
            },
        },
    )


def publication_inputs_current(evidence: Any, now: datetime) -> bool:
    """Withhold a new card if queue/archive latency aged its verified input set."""
    try:
        quote_at = datetime.fromisoformat(evidence["quote_as_of"])
        decision_at = datetime.fromisoformat(evidence["decision_at"])
        window_end = datetime.fromisoformat(evidence["aligned_window_end"])
        return (
            timedelta(seconds=-5) <= now - quote_at <= timedelta(seconds=60)
            and timedelta(seconds=-5) <= now - decision_at <= timedelta(minutes=5)
            and int(window_end.timestamp()) == int(now.timestamp()) // 3600 * 3600
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
