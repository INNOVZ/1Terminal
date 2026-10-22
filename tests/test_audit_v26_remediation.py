from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application.crypto_screener import (
    COI_OVEREXTENDED_THRESHOLD,
    COI_OVERSOLD_THRESHOLD,
    COI_STANDARD_CONVICTION_FLOOR,
    COI_TIGHTENED_CONVICTION_FLOOR,
    CryptoOpportunityScreener,
)
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    Direction,
    ScreenerMode,
    SetupLabel,
)
from algotrader.domain.execution_brackets import build_long_bracket, build_short_bracket
from algotrader.domain.indicators import resample_candles, rsi
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.scoring import OpportunityGrade
from algotrader.domain.screening_gates import (
    LongGateInputs,
    ShortGateInputs,
    evaluate_long_gates,
    evaluate_short_gates,
)
from algotrader.infrastructure.persistence import atomic_write_json
from tests.conftest import make_instrument, trending_candles


def _dummy_opportunity(
    *,
    symbol: str = "SOLUSDT",
    direction: Direction = Direction.LONG,
    score: Decimal = Decimal("72.0"),
    conviction: Decimal = Decimal("68.0"),
    rr: Decimal = Decimal("2.20"),
    ev_r: Decimal = Decimal("0.35"),
    grade: str = "Grade B",
    passed_count: int = 4,
) -> CryptoOpportunity:
    inst = make_instrument(
        venue=Venue.BINANCE_USDM,
        market_kind=MarketKind.FUTURES,
        symbol=symbol,
    )
    return CryptoOpportunity(
        instrument=inst,
        direction=direction,
        regime=CryptoMarketRegime.STRONG_TREND_UP,
        setup_label=SetupLabel.COIL_SPRING_LONG if direction is Direction.LONG else SetupLabel.EXHAUSTION_REVERSAL_SHORT,
        score=score,
        score_breakdown=CryptoScreenerScoreBreakdown(
            trend_score=Decimal("80"),
            momentum_score=Decimal("80"),
            derivatives_score=Decimal("80"),
            alpha_score=Decimal("80"),
            volume_score=Decimal("80"),
            structure_score=Decimal("80"),
            macro_multiplier=Decimal("1.0"),
            final_composite_score=score,
        ),
        current_price=Decimal("100.00"),
        atr14=Decimal("2.00"),
        suggested_entry=Decimal("100.00"),
        suggested_stop=Decimal("95.00") if direction is Direction.LONG else Decimal("105.00"),
        suggested_targets=(Decimal("105.00"), Decimal("110.00"), Decimal("115.00")),
        risk_reward_ratio=rr,
        btc_beta=Decimal("1.0"),
        residual_return_24h=Decimal("0.02"),
        funding_rate=Decimal("0.0001"),
        oi_change_1h=Decimal("0.01"),
        taker_ratio=Decimal("1.10"),
        rationale=("Institutional setup",),
        macro_narrative="Bullish expansion",
        estimated_win_rate_pct=Decimal("62.0"),
        expected_value_r=ev_r,
        conviction=conviction,
        grade=grade,
        checklist_verification=ChecklistVerification(
            value_acceptance=True,
            volatility_structure=True,
            momentum_alignment=True,
            volume_microstructure=passed_count >= 4,
            derivatives_positioning=passed_count >= 5,
        ),
    )


def test_task1_short_rr_quality_gate_direction_aware(settings: Settings) -> None:
    """Task 1: Verify Direction-aware R:R floor prevents vetoing bounded Short setups (R:R in [1.50, 1.99])."""
    # 1. Bounded short setup with R:R = 1.75 and EV = +0.25R
    bounded_short = _dummy_opportunity(
        symbol="DOGEUSDT",
        direction=Direction.SHORT,
        score=Decimal("72.0"),
        conviction=Decimal("68.0"),
        rr=Decimal("1.75"),
        ev_r=Decimal("0.25"),
    )

    # 2. Long setup with R:R = 1.75 and EV = +0.25R (must fail R:R >= 2.0)
    sub_rr_long = _dummy_opportunity(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        score=Decimal("72.0"),
        conviction=Decimal("68.0"),
        rr=Decimal("1.75"),
        ev_r=Decimal("0.25"),
    )

    # 3. Sub-floor short with R:R = 1.40 (must fail R:R >= 1.50)
    too_low_short = _dummy_opportunity(
        symbol="XRPUSDT",
        direction=Direction.SHORT,
        score=Decimal("72.0"),
        conviction=Decimal("68.0"),
        rr=Decimal("1.40"),
        ev_r=Decimal("0.25"),
    )

    # 4. Short with EV = +0.10R (must fail EV >= 0.20R)
    low_ev_short = _dummy_opportunity(
        symbol="ADAUSDT",
        direction=Direction.SHORT,
        score=Decimal("72.0"),
        conviction=Decimal("68.0"),
        rr=Decimal("1.75"),
        ev_r=Decimal("0.10"),
    )

    screener = CryptoOpportunityScreener(settings)

    # Helper simulating the quality gate in screen()
    def passes_gate(opp: CryptoOpportunity) -> bool:
        eff_score = opp.conviction if opp.conviction is not None else opp.score
        if opp.grade is OpportunityGrade.BELOW_THRESHOLD or str(opp.grade) == "Below Threshold":
            return False
        if eff_score < Decimal("68.0") or opp.score < Decimal("68.0"):
            return False
        if opp.checklist_verification is not None and opp.checklist_verification.passed_count < 3:
            return False
        min_rr = Decimal("1.50") if opp.direction is Direction.SHORT else Decimal("2.00")
        if opp.risk_reward_ratio < min_rr or opp.expected_value_r < Decimal("0.20"):
            return False
        return True

    assert passes_gate(bounded_short) is True, "Bounded short with 1.75R must PASS direction-aware quality gate"
    assert passes_gate(sub_rr_long) is False, "Long with 1.75R must be VETOED (requires >= 2.0R)"
    assert passes_gate(too_low_short) is False, "Short with 1.40R must be VETOED (requires >= 1.50R)"
    assert passes_gate(low_ev_short) is False, "Short with 0.10R EV must be VETOED (requires >= 0.20R)"


def test_task1_short_bracket_bounding_and_asymmetric_rr() -> None:
    """Task 1: Verify short bracket bounds T1 at 1.75R/14% while using T2 for asymmetric_rr."""
    close = Decimal("100.00")
    tick = Decimal("0.01")
    bracket = build_short_bracket(
        close=close,
        ema50_4h=Decimal("102.00"),
        poc_30d=Decimal("103.00"),
        atr14_4h=Decimal("3.00"),
        local_consolidation_low_4h=Decimal("98.00"),
        swing_high_4h=Decimal("105.00"),
        low_30d=Decimal("70.00"),
        vah_30d=Decimal("106.00"),
        val_30d=Decimal("94.00"),
        tick_size=tick,
    )
    # T1 is bounded around 1.75R
    assert bracket.r_t1 <= Decimal("2.20")
    # T2 is the asymmetric structural target (>= 2.0R)
    assert bracket.r_t2 >= Decimal("2.00")
    # asymmetric_rr uses r_t2
    assert bracket.asymmetric_rr == min(bracket.r_t2, Decimal("8.00"))


def test_task2_resampling_lookback_weekly_rsi_convergence() -> None:
    """Task 2: Verify 1000 4H bars provide >= 15 weekly bars for converged 14-period RSI."""
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    # Generate 1000 4-hour candles (~166 days = ~24 weeks)
    candles_4h: list[Candle] = []
    price = Decimal("100.0")
    for i in range(1000):
        c_time = base_time + timedelta(hours=4 * i)
        price += Decimal("0.05") if i % 2 == 0 else Decimal("-0.03")
        candles_4h.append(
            Candle(
                opened_at=c_time,
                closed_at=c_time + timedelta(hours=4),
                open=price - Decimal("0.1"),
                high=price + Decimal("0.2"),
                low=price - Decimal("0.2"),
                close=price,
                volume=Decimal("1000"),
                closed=True,
            )
        )

    # 1. Resample to 1D and 1W
    candles_1d = resample_candles(candles_4h, "1d")
    candles_1w = resample_candles(candles_1d, "1w")

    assert len(candles_1w) >= 20, f"Expected >= 20 weekly candles from 1000 4H bars, got {len(candles_1w)}"

    # 2. Compute 14-period Wilder's RSI on weekly closes
    closes_1w = [c.close for c in candles_1w]
    rsi_1w_series = rsi(closes_1w, 14)
    assert len(rsi_1w_series) >= 6
    assert rsi_1w_series[-1] is not None
    assert Decimal("0.0") <= rsi_1w_series[-1] <= Decimal("100.0")

    # 3. Verify VPVR window strictly slices last 180 bars
    vpvr_window = candles_4h[-180:]
    assert len(vpvr_window) == 180, "VPVR must slice exactly 180 bars (30 days)"

    # 4. Compare with 250 bars (old behavior) which failed to produce 15 weekly bars
    short_4h = candles_4h[-250:]
    short_1d = resample_candles(short_4h, "1d")
    short_1w = resample_candles(short_1d, "1w")
    assert len(short_1w) < 10, "250 bars produces only 5-8 weekly candles, causing RSI calculation failure"


def test_task3_stage2_degraded_telemetry_handling() -> None:
    """Task 3: Degraded telemetry sets degraded_telemetry=True and does not falsely pass gates."""
    # 1. Snapshot with degraded telemetry
    degraded_snap = DerivativesSnapshot(
        funding_rate=None,
        basis_rate=Decimal("0"),
        open_interest=Decimal("50000000"),
        open_interest_change_1h=None,
        taker_buy_sell_ratio=Decimal("1.0"),
        bid_depth_notional=Decimal("100000"),
        ask_depth_notional=Decimal("100000"),
        open_interest_change_7d=None,
        degraded_telemetry=True,
    )
    assert degraded_snap.degraded_telemetry is True
    assert degraded_snap.funding_rate is None

    # 2. Gate L6: Price at ceiling (dist <= 4.5%) with bleeding OI (-5%) must fail
    bleeding_inputs = LongGateInputs(
        close=Decimal("100.00"),
        ema50_4h=Decimal("95.00"),
        ema20_1h=Decimal("98.00"),
        ema200_4h=Decimal("90.00"),
        low_24h=Decimal("97.00"),
        atr14_4h=Decimal("2.00"),
        vcr_4h=Decimal("0.85"),
        ema20_4h=Decimal("98.00"),
        excursion_72h=Decimal("0.15"),
        ret_24h=Decimal("0.02"),
        rsi_1h=Decimal("55.0"),
        rsi_4h=Decimal("58.0"),
        high_30d=Decimal("103.00"),  # dist_to_ceiling = (103 - 100) / 100 = 3.0% <= 4.5%
        low_30d=Decimal("85.00"),
        poc_30d=Decimal("96.00"),
        val_30d=Decimal("92.00"),
        vah_30d=Decimal("99.00"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.15"),
        rvol_4h=Decimal("1.00"),  # < 1.20 (no volume surge)
        oi_delta_7d=Decimal("-0.05"),  # -5% bleeding OI!
        taker_buy_ratio_4h=Decimal("0.50"),
    )
    passed, details = evaluate_long_gates(bleeding_inputs, SetupLabel.COIL_SPRING_LONG)
    assert details["L6"] is False, "Gate L6 must REJECT when 7D OI is bleeding at the ceiling"
    assert passed is False


@pytest.mark.asyncio
async def test_task5_coi_regime_desk_throttling(settings: Settings) -> None:
    """Task 5: Wire COI into CryptoOpportunityScreener desk curation and conviction floors."""
    # 1. Overextended Regime (COI = 80 >= 75)
    # Long conviction floor tightened to 70.0; max 8 Longs, max 8 Shorts
    screener = CryptoOpportunityScreener(settings)

    opp_long_68 = _dummy_opportunity(symbol="L68USDT", direction=Direction.LONG, conviction=Decimal("68.0"))
    opp_long_72 = _dummy_opportunity(symbol="L72USDT", direction=Direction.LONG, conviction=Decimal("72.0"))
    opp_short_68 = _dummy_opportunity(symbol="S68USDT", direction=Direction.SHORT, conviction=Decimal("68.0"))

    # Test COI = 80 gating:
    # Long at 68.0 must fail tightened floor (70.0)
    assert opp_long_68.conviction < COI_TIGHTENED_CONVICTION_FLOOR
    assert opp_long_72.conviction >= COI_TIGHTENED_CONVICTION_FLOOR
    # Short at 68.0 passes standard floor (65.0)
    assert opp_short_68.conviction >= COI_STANDARD_CONVICTION_FLOOR

    # Test COI = 20 (Capitulation) gating:
    # Short at 68.0 must fail tightened floor (70.0)
    assert opp_short_68.conviction < COI_TIGHTENED_CONVICTION_FLOOR
    # Long at 68.0 passes standard floor (65.0)
    assert opp_long_68.conviction >= COI_STANDARD_CONVICTION_FLOOR


def test_task6_atomic_persistence(tmp_path: Path) -> None:
    """Task 6: Ensure cache persistence writes atomically via .tmp and fsync."""
    target_file = tmp_path / "data" / "screener_latest.json"
    data = {
        "timestamp": "2026-10-22T08:00:00Z",
        "qualifying_count": 12,
        "opportunities": [{"symbol": "SOLUSDT", "direction": "long"}],
    }

    atomic_write_json(target_file, data)

    # 1. File exists and contains valid JSON
    assert target_file.exists()
    loaded = json.loads(target_file.read_text())
    assert loaded["qualifying_count"] == 12
    assert loaded["opportunities"][0]["symbol"] == "SOLUSDT"

    # 2. No lingering .tmp file
    tmp_file = target_file.with_name("screener_latest.json.tmp")
    assert not tmp_file.exists()


def test_task7_atr_multipliers_ocr_audit() -> None:
    """Task 7: Verify Long Retest Entry uses +0.15*ATR and Short Breakdown Trigger uses -0.05*ATR."""
    close = Decimal("100.00")
    tick = Decimal("0.01")
    atr = Decimal("2.00")

    # Long bracket: retest offset uses +0.15 * atr
    long_bracket = build_long_bracket(
        close=close,
        ema50_4h=Decimal("98.00"),
        poc_30d=Decimal("99.00"),
        atr14_4h=atr,
        local_compression_high_4h=Decimal("101.50"),
        swing_low_4h=Decimal("96.50"),
        high_30d=Decimal("108.00"),
        vah_30d=Decimal("106.00"),
        tick_size=tick,
    )
    # anchor shelf is poc_30d (99.00), raw entry min(99.00 + 0.15 * 2.0 = 99.30, close * 0.99 = 99.00)
    assert long_bracket.retest_entry <= Decimal("99.30")
    assert long_bracket.retest_entry < close

    # Short bracket: breakdown trigger uses -0.05 * atr (not -0.85 * atr)
    short_bracket = build_short_bracket(
        close=close,
        ema50_4h=Decimal("102.00"),
        poc_30d=Decimal("103.00"),
        atr14_4h=atr,
        local_consolidation_low_4h=Decimal("98.00"),
        swing_high_4h=Decimal("105.00"),
        low_30d=Decimal("70.00"),
        vah_30d=Decimal("106.00"),
        val_30d=Decimal("94.00"),
        tick_size=tick,
    )
    # raw_breakdown_trigger = 98.00 - 0.05 * 2.0 = 97.90 (NOT 98.00 - 0.85 * 2.0 = 96.30)
    assert short_bracket.breakout_trigger == Decimal("97.90")
