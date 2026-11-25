"""Compose a disabled-by-default Early-Recovery (`RECOVERY_RECLAIM_LONG`) candidate."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrader.domain.crypto_screener_models import (
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    SetupLabel,
)
from algotrader.domain.early_recovery import (
    DEFAULT_EARLY_RECOVERY_POLICY,
    POLICY_VERSION,
    TITLES,
    EarlyRecoveryEvaluation,
    EarlyRecoveryPolicy,
    evaluate_early_recovery,
)
from algotrader.domain.indicators import volume_contraction_metrics, volume_profile
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Instrument, Quote
from algotrader.domain.plan_diagnostics import BracketContext
from algotrader.domain.scoring import OpportunityGrade

D = Decimal
ZERO = D("0")


def _clip01(value: Decimal) -> Decimal:
    return max(ZERO, min(D("1"), value))


def score_early_recovery(
    inst: Instrument,
    quote: Quote,
    candles_1h: Sequence[Candle],
    candles_4h: Sequence[Candle],
    btc_candles_1h: Sequence[Candle],
    *,
    now: datetime,
    derivatives: DerivativesSnapshot | None,
    is_btc_cascade: bool,
    macro_multiplier: Decimal,
    macro_narrative: str,
    beta: Decimal,
    residual_24h: Decimal,
    is_discovery_tier: bool,
    note: Any,
    policy: EarlyRecoveryPolicy = DEFAULT_EARLY_RECOVERY_POLICY,
) -> tuple[CryptoOpportunity | None, EarlyRecoveryEvaluation]:
    """Evaluate Early-Recovery and return (opportunity_or_None, evaluation).

    Stage 1 (`derivatives=None`): returns an intermediate structural candidate ONLY so
    Stage-1 tracking / journal notes work; actual publication requires all 5 gates (`confirmed=True`).
    Never manufactures fake core `ChecklistVerification` passes.
    """
    evaluation = evaluate_early_recovery(
        candles_1h,
        candles_4h,
        btc_candles_1h,
        now=now,
        quote=quote,
        tick=inst.price_tick,
        derivatives=derivatives,
        is_btc_cascade=is_btc_cascade,
        policy=policy,
    )
    note(
        inst.id.symbol,
        "early_recovery_gates",
        evaluation.reason,
        enriched=derivatives is not None,
        policy=policy.version,
        family_policy_mode=policy.family_policy_mode,
        gates=dict(zip(TITLES, evaluation.gates, strict=True)),
        plan=evaluation.plan.as_dict() if evaluation.plan is not None else None,
        metrics=evaluation.metrics,
    )
    if not evaluation.structurally_ready or (derivatives is not None and not evaluation.confirmed):
        return None, evaluation

    plan = evaluation.plan
    assert plan is not None

    h = sorted((c for c in candles_1h if c.closed and c.closed_at <= now), key=lambda c: c.closed_at)
    q = sorted((c for c in candles_4h if c.closed and c.closed_at <= now), key=lambda c: c.closed_at)
    vp = volume_profile(q[-180:], tick_size=inst.price_tick)
    adv7, adv14, vcr = (
        volume_contraction_metrics(h[-336:], 7, 14)
        if len(h) >= 336
        else (None, None, None)
    )

    rs6 = D(evaluation.metrics.get("btc_relative_6h_pp", "0"))
    va6 = D(evaluation.metrics.get("turnover_accel_6h", "1"))
    risk_pct = plan.risk_unit / max(plan.entry, D("1e-8"))

    # Transparent bounded heuristic ranking components (72..96 range on strong setups)
    structure_comp = D("68") + D("27") * _clip01((h[-1].close - plan.pivot) / max(plan.atr_4h * D("0.60"), D("1e-8")))
    participation_comp = D("68") + D("16") * _clip01((va6 - D("1.25")) / D("1.75")) + D("16") * _clip01((rs6 - D("1.0")) / D("5.0"))
    room_comp = D("68") + D("32") * _clip01((plan.rr_t2 - D("2.0")) / D("2.0"))
    risk_comp = D("65") + D("35") * _clip01((D("0.085") - risk_pct) / D("0.065"))

    if evaluation.confirmed and derivatives is not None:
        oi4 = derivatives.open_interest_change_4h or ZERO
        tk4 = derivatives.taker_buy_sell_ratio_4h or D("1")
        flow_comp = (
            D("70")
            + D("15") * _clip01(oi4 / D("0.06"))
            + D("15") * _clip01((tk4 - D("1.0")) / D("0.20"))
        )
        raw_score = (
            structure_comp * D("0.22")
            + participation_comp * D("0.23")
            + room_comp * D("0.18")
            + risk_comp * D("0.15")
            + flow_comp * D("0.22")
        )
    else:
        flow_comp = ZERO
        raw_score = (
            structure_comp * D("0.28")
            + participation_comp * D("0.30")
            + room_comp * D("0.22")
            + risk_comp * D("0.20")
        )

    eff_mult = max(D("0.90"), min(D("1.05"), macro_multiplier))
    score = max(ZERO, min(D("100"), (raw_score * eff_mult).quantize(D("0.1"))))
    conviction = score
    grade = (
        OpportunityGrade.GRADE_A.value
        if score >= D("82.0") and evaluation.confirmed
        else OpportunityGrade.GRADE_B.value
        if score >= D("68.0")
        else OpportunityGrade.BELOW_THRESHOLD.value
    )

    breakdown = CryptoScreenerScoreBreakdown(
        trend_score=structure_comp.quantize(D("0.1")),
        momentum_score=participation_comp.quantize(D("0.1")),
        derivatives_score=flow_comp.quantize(D("0.1")),
        alpha_score=participation_comp.quantize(D("0.1")),
        volume_score=participation_comp.quantize(D("0.1")),
        structure_score=room_comp.quantize(D("0.1")),
        macro_multiplier=eff_mult.quantize(D("0.01")),
        final_composite_score=score,
    )
    checklist = tuple(
        (title, gate is True, detail)
        for title, gate, detail in zip(TITLES, evaluation.gates, evaluation.details, strict=True)
    )
    # Uncalibrated baseline model expectancy placeholder (explicitly labelled uncalibrated in score_semantics)
    model_ev_r = ((plan.r_t1 * D("0.50")) - D("0.50")).quantize(D("0.01"))
    taker_1h = derivatives.taker_buy_sell_ratio if derivatives else None
    taker_4h = derivatives.taker_buy_sell_ratio_4h if derivatives else None
    eff_taker = (
        max(x for x in (taker_1h, taker_4h) if x is not None)
        if (taker_1h is not None or taker_4h is not None)
        else None
    )

    opp = CryptoOpportunity(
        evaluated_at=now,
        instrument=inst,
        direction=Direction.LONG,
        regime=CryptoMarketRegime.EMERGING_TREND_UP,
        setup_label=SetupLabel.RECOVERY_RECLAIM_LONG,
        score=score,
        conviction=conviction,
        grade=grade,
        score_breakdown=breakdown,
        current_price=quote.last,
        atr14=plan.atr_4h,
        suggested_entry=plan.entry,
        pullback_entry=plan.retest_entry,
        breakout_trigger=plan.entry,
        suggested_stop=plan.stop,
        retest_stop=plan.stop,
        breakout_stop=plan.stop,
        suggested_targets=plan.targets,
        risk_reward_ratio=plan.rr_t2,
        risk_reward_target_index=2,
        estimated_win_rate_pct=D("50.0"),
        expected_value_r=model_ev_r,
        checklist=checklist,
        checklist_verification=None,  # Never manufacture 5 legacy conviction-factor passes
        btc_beta=beta.quantize(D("0.01")),
        residual_return_24h=residual_24h.quantize(D("0.0001")),
        funding_rate=derivatives.funding_rate if derivatives else quote.funding_rate,
        oi_change_1h=derivatives.open_interest_change_1h if derivatives else None,
        oi_change_7d=derivatives.open_interest_change_7d if derivatives else None,
        taker_ratio=eff_taker,
        taker_ratio_1h=taker_1h,
        taker_ratio_4h=taker_4h,
        taker_ratio_24h=derivatives.taker_buy_sell_ratio_24h if derivatives else None,
        taker_ratio_window_hours=4,
        poc_price=vp.poc_price,
        vah_price=vp.vah_price,
        val_price=vp.val_price,
        adv_7d=adv7.quantize(D("1")) if adv7 is not None else None,
        adv_14d=adv14.quantize(D("1")) if adv14 is not None else None,
        volume_contraction_ratio=vcr,
        execution_mode="CEILING_BREAKOUT",
        breakout_readiness_status="RECOVERY_TRIGGER_PENDING",
        rationale=(
            f"Family: RECOVERY_RECLAIM_LONG ({policy.version}) | Heuristic Score: {float(score):.1f} (uncalibrated)",
            evaluation.details[0],
            evaluation.details[2],
            evaluation.details[3],
            evaluation.details[4],
        ),
        macro_narrative=macro_narrative,
        is_discovery_tier=is_discovery_tier,
        research_policy_version=policy.version,
        bracket_context=BracketContext(
            atr_value=plan.atr_4h,
            atr_timeframe="4h",
            stop_anchor="early_recovery_local_low",
            t1_before_risk_sizing=plan.t1,
            levels=(
                ("Overhead Res", plan.first_resistance),
                ("4H EMA50", plan.ema50_4h_ref),
                ("30d POC", plan.poc_30d_ref),
            ),
        ),
        recovery_evidence={
            "policy_version": policy.version,
            "family_policy_mode": policy.family_policy_mode,
            "plan": plan.as_dict(),
            "derivatives_confirmed": evaluation.confirmed,
            "decision_at": now.isoformat(),
            "quote_as_of": quote.observed_at.isoformat(),
            "feature_1h_closed_at": h[-1].closed_at.isoformat(),
            "feature_4h_closed_at": q[-1].closed_at.isoformat(),
            "derivatives_observed_at": (
                derivatives.observed_at.isoformat() if derivatives is not None else None
            ),
            "family_gates": [
                {"title": title, "passed": gate is True, "status": "passed" if gate is True else "unknown" if gate is None else "failed", "detail": detail}
                for title, gate, detail in zip(TITLES, evaluation.gates, evaluation.details, strict=True)
            ],
            "metrics": evaluation.metrics,
        },
    )
    return opp, evaluation


def recovery_publication_inputs_current(evidence: Any, now: datetime) -> bool:
    """Withhold a new recovery card if queue/scan latency aged its verified quote or derivatives."""
    try:
        if not isinstance(evidence, dict) or evidence.get("policy_version") != POLICY_VERSION:
            return False
        if evidence.get("derivatives_confirmed") is not True:
            return False
        quote_at = datetime.fromisoformat(str(evidence["quote_as_of"]))
        decision_at = datetime.fromisoformat(str(evidence["decision_at"]))
        deriv_raw = evidence.get("derivatives_observed_at")
        if not deriv_raw:
            return False
        deriv_at = datetime.fromisoformat(str(deriv_raw))
        return (
            timedelta(seconds=-5) <= now - quote_at <= timedelta(seconds=120)
            and timedelta(seconds=-5) <= now - decision_at <= timedelta(minutes=5)
            and timedelta(seconds=-5) <= now - deriv_at <= timedelta(minutes=5)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False

