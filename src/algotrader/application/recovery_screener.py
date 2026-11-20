"""Opt-in recovery candidate policy using the normal bounded screening pipeline."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.crypto_screener import CryptoOpportunityScreener
from algotrader.application.scan_recording import MacroContextSource
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    ScreenerMode,
    SetupLabel,
)
from algotrader.domain.discovery_features import (
    DiscoveryFeatureUnavailable,
    discovery_volume_metrics,
)
from algotrader.domain.execution_brackets import InvalidExecutionBracket
from algotrader.domain.indicators import IndicatorSnapshot, ema, volume_profile
from algotrader.domain.models import Candle, DerivativesSnapshot, Direction, Instrument, Quote
from algotrader.domain.recovery import RECOVERY_POLICY_VERSION, assess_recovery
from algotrader.domain.scan_observations import ScanDecisionJournal
from algotrader.domain.scoring import (
    classify_opportunity_grade,
    clip,
    compute_3tier_ev_r,
    compute_p_win_and_conviction,
)


class RecoveryScreener(CryptoOpportunityScreener):
    def __init__(
        self, settings: Settings, macro_provider: MacroContextSource | None = None, *,
        evaluated_at: datetime | None = None, decision_at: datetime | None = None,
        journal: ScanDecisionJournal | None = None,
    ) -> None:
        super().__init__(settings, macro_provider, evaluated_at=evaluated_at,
                         journal=journal, feature_contract="discovery_v2")
        self._decision_at = decision_at
        self._recovery_ready: set[str] = set()

    async def screen(self, *args: Any, **kwargs: Any) -> list[CryptoOpportunity]:
        if kwargs.get("mode") == ScreenerMode.BREAKOUT:
            raise ValueError("recovery-v1 requires the bounded accumulation or all-mode enrichment path")
        self._recovery_ready.clear()
        result = await super().screen(*args, **kwargs)
        confirmed = []
        for opportunity in result:
            if (opportunity.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG
                    and (len(opportunity.checklist) != 5
                         or not all(passed for _, passed, _ in opportunity.checklist))):
                self._note(opportunity.instrument.id.symbol, "recovery_publication", "confirmation_unavailable")
                continue
            confirmed.append(opportunity)
        self.scan_counts["returned"] = len(confirmed)
        return confirmed

    def _additional_probe_priority(self, symbol: str) -> Decimal:
        return Decimal("1000") if symbol in self._recovery_ready else Decimal("0")

    def _score_accumulation_setup(
        self, inst: Instrument, quote: Quote, snap_1h: IndicatorSnapshot,
        closed_1h: Sequence[Candle], closed_4h: Sequence[Candle],
        derivatives: DerivativesSnapshot | None, macro_context: MacroSentimentContext,
        is_btc_cascade: bool = False, btc_ret_1h: Decimal = Decimal("0"),
        beta: Decimal = Decimal("1"), residual_24h: Decimal = Decimal("0"),
        candles_1d: Sequence[Candle] | None = None, candles_1w: Sequence[Candle] | None = None,
        is_discovery_tier: bool = False,
    ) -> CryptoOpportunity | None:
        original = super()._score_accumulation_setup(
            inst, quote, snap_1h, closed_1h, closed_4h, derivatives, macro_context,
            is_btc_cascade, btc_ret_1h, beta, residual_24h, candles_1d, candles_1w, is_discovery_tier,
        )
        if original is not None:
            return original
        if inst.id.symbol not in self._emerging_symbols or is_btc_cascade:
            return None
        try:
            if len(closed_4h) < 50:
                raise DiscoveryFeatureUnavailable("recovery requires 50 closed 4H bars for EMA50")
            adv7, adv14, contraction, _ = discovery_volume_metrics(closed_1h, closed_4h)
            profile = volume_profile(closed_4h[-180:], tick_size=inst.price_tick)
            ema50 = ema([c.close for c in closed_4h], 50)[-1]
            assessment = assess_recovery(
                closed_1h, quote=quote, atr=snap_1h.atr14, ema20=snap_1h.ema20,
                ema50_4h=ema50, poc_30d=profile.poc_price, tick_size=inst.price_tick,
                emerging=True, derivatives=derivatives,
                cutoff=self._evaluated_at or datetime.now(UTC),
                decision_at=self._decision_at or datetime.now(UTC),
            )
        except DiscoveryFeatureUnavailable as exc:
            self._feature_unavailable.add(inst.id.symbol)
            self._note(inst.id.symbol, "recovery_rejection", "feature_contract_unavailable", detail=str(exc))
            return None
        except InvalidExecutionBracket as exc:
            self._note(inst.id.symbol, "recovery_rejection", "input_or_bracket_unavailable", detail=str(exc))
            return None
        self._note(inst.id.symbol, "recovery_gates", "passed" if all(v for _, v in assessment.gates) else "failed",
                   policy=RECOVERY_POLICY_VERSION, enriched=derivatives is not None,
                   gates=dict(assessment.gates), pivot=assessment.pivot,
                   turnover_acceleration=assessment.turnover_acceleration,
                   entry=assessment.bracket.suggested_entry, stop=assessment.bracket.invalidation_stop)
        if not assessment.ready:
            return None
        self._recovery_ready.add(inst.id.symbol)
        if derivatives is not None and not assessment.gates[-1][1]:
            return None

        bracket = assessment.bracket
        structure = clip(Decimal("60") + (snap_1h.close - assessment.pivot) / snap_1h.atr14 * 10,
                         Decimal("0"), Decimal("100"))
        momentum = clip(Decimal("60") + (snap_1h.close - snap_1h.ema20) / snap_1h.atr14 * 10,
                        Decimal("0"), Decimal("100"))
        participation = clip(Decimal("60") + (assessment.turnover_acceleration - 1) * 15,
                             Decimal("0"), Decimal("100"))
        risk = clip(Decimal("100") - bracket.risk_unit / bracket.suggested_entry * 500,
                    Decimal("0"), Decimal("100"))
        taker = derivatives.taker_buy_sell_ratio_4h if derivatives else None
        oi4 = derivatives.open_interest_change_4h if derivatives else None
        flow = clip(Decimal("60") + ((taker or Decimal("1")) - 1) * 50
                    + (oi4 or Decimal("0")) * 200, Decimal("0"), Decimal("100"))
        multiplier = macro_context.multiplier_for(Direction.LONG)
        score = clip((structure + momentum + participation + risk + flow) / 5 * multiplier,
                     Decimal("0"), Decimal("100")).quantize(Decimal("0.1"))
        checklist = tuple((name, passed, RECOVERY_POLICY_VERSION) for name, passed in assessment.gates)
        verification = ChecklistVerification(
            value_acceptance=assessment.gates[0][1], volatility_structure=assessment.gates[3][1],
            momentum_alignment=assessment.gates[1][1], volume_microstructure=assessment.gates[2][1],
            derivatives_positioning=assessment.gates[4][1],
        )
        p_win, conviction = compute_p_win_and_conviction(
            direction=Direction.LONG, archetype=SetupLabel.RECOVERY_RECLAIM_LONG,
            quantitative_score=score, checklist_passed=verification.passed_count,
        )
        ev = compute_3tier_ev_r(p_win=p_win, r_t1=bracket.r_t1, r_t2=bracket.r_t2, r_t3=bracket.r_t3)
        grade = classify_opportunity_grade(ev_r=ev, conviction=conviction,
                                           asymmetric_rr=bracket.r_t1,
                                           checklist_passed=verification.passed_count)
        return CryptoOpportunity(
            instrument=inst, direction=Direction.LONG, regime=CryptoMarketRegime.EMERGING_TREND_UP,
            setup_label=SetupLabel.RECOVERY_RECLAIM_LONG, score=score,
            score_breakdown=CryptoScreenerScoreBreakdown(structure, momentum, flow, momentum,
                                                       participation, risk, multiplier, score),
            current_price=snap_1h.close, atr14=snap_1h.atr14,
            suggested_entry=bracket.suggested_entry, suggested_stop=bracket.invalidation_stop,
            suggested_targets=bracket.targets, risk_reward_ratio=bracket.r_t1, risk_reward_target_index=1,
            btc_beta=beta, residual_return_24h=residual_24h,
            funding_rate=derivatives.funding_rate if derivatives else quote.funding_rate,
            oi_change_1h=derivatives.open_interest_change_1h if derivatives else None,
            oi_change_7d=derivatives.open_interest_change_7d if derivatives else None,
            taker_ratio=taker, taker_ratio_4h=taker, taker_ratio_window_hours=4,
            rationale=(f"Research policy {RECOVERY_POLICY_VERSION}",
                       "Closed-hour local reclaim below older trend/value references; projected 2R/3R/4R objectives.",
                       f"Estimated value profile uses {min(180, len(closed_4h)) * 4} closed hours."),
            macro_narrative=macro_context.narrative(), poc_price=profile.poc_price,
            vah_price=profile.vah_price, val_price=profile.val_price,
            adv_7d=adv7, adv_14d=adv14, volume_contraction_ratio=contraction,
            pullback_entry=bracket.retest_entry, breakout_trigger=bracket.breakout_trigger,
            retest_stop=bracket.retest_stop, breakout_stop=bracket.breakout_stop,
            execution_mode=bracket.execution_mode, checklist=checklist, checklist_verification=verification,
            breakout_readiness_status="RECOVERY_TRIGGER_PENDING",
            evaluated_at=self._evaluated_at or datetime.now(UTC),
            estimated_win_rate_pct=p_win * 100, expected_value_r=ev, conviction=conviction,
            grade=grade.value, is_discovery_tier=True, research_policy_version=RECOVERY_POLICY_VERSION,
        )
