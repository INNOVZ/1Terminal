from dataclasses import replace
from decimal import Decimal

import pytest

from algotrader.application.alpha import (
    AlphaDecision,
    EarlyMomentumAlpha,
    EmpiricalTrendAlpha,
    LayeredMomentumAlpha,
    RegimeBreakoutAlpha,
    TrendQualityAlpha,
    directional_momentum_score,
    impulse_retest,
)
from algotrader.application.screener import MultiFactorScreener
from algotrader.application.strategy import TrendMomentumStrategy
from algotrader.config import EmpiricalSetup, Settings, StrategyProfile
from algotrader.domain.indicators import ContinuationTrigger, snapshot
from algotrader.domain.models import (
    Candidate,
    Candle,
    Direction,
    MarketKind,
    MomentumState,
    Quote,
    Venue,
)
from algotrader.domain.structure import classify
from tests.conftest import make_instrument, trending_candles


def _breakout_history() -> tuple[list[Candle], list[Candle]]:
    one_hour = trending_candles(count=260)
    previous_high = max(candle.high for candle in one_hour[-41:-1])
    signal = one_hour[-1]
    one_hour[-1] = replace(
        signal,
        open=previous_high,
        high=previous_high + Decimal("0.20"),
        low=previous_high - Decimal("0.02"),
        close=previous_high + Decimal("0.15"),
        volume=signal.volume * Decimal("1.5"),
    )
    four_hour = trending_candles(count=210)
    return one_hour, four_hour


def _settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "strategy_profile": StrategyProfile.REGIME_BREAKOUT,
            "min_volatility_percentile": Decimal("0"),
            "max_volatility_percentile": Decimal("1"),
            "require_structure_alignment": False,
        }
    )


def _empirical_history() -> tuple[list[Candle], list[Candle], list[Candle]]:
    one_hour = trending_candles(count=210)
    four_hour = trending_candles(count=210)
    previous_high = max(candle.high for candle in four_hour[-31:-1])
    signal = four_hour[-1]
    four_hour[-1] = replace(
        signal,
        open=previous_high,
        high=previous_high + Decimal("0.20"),
        low=previous_high - Decimal("0.02"),
        close=previous_high + Decimal("0.15"),
    )
    daily = trending_candles(count=260)
    return one_hour, four_hour, daily


def _empirical_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "strategy_profile": StrategyProfile.EMPIRICAL_TREND,
            "empirical_setup": EmpiricalSetup.BREAKOUT,
        }
    )


def _layered_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "strategy_profile": StrategyProfile.LAYERED_MOMENTUM,
            "layered_setup": EmpiricalSetup.BREAKOUT,
        }
    )


def _declining_breakout_history() -> tuple[list[Candle], list[Candle], list[Candle]]:
    one_hour = trending_candles(up=False, count=210)
    four_hour = trending_candles(up=False, count=210)
    previous_low = min(candle.low for candle in four_hour[-31:-1])
    signal = four_hour[-1]
    four_hour[-1] = replace(
        signal,
        open=previous_low,
        high=previous_low + Decimal("0.02"),
        low=previous_low - Decimal("0.20"),
        close=previous_low - Decimal("0.15"),
    )
    daily = trending_candles(up=False, count=260)
    return one_hour, four_hour, daily


def _early_momentum_history(*, up: bool) -> tuple[list[Candle], list[Candle]]:
    one_hour = trending_candles(up=up, count=260)
    event_index = len(one_hour) - 4
    prior = one_hour[event_index - 20 : event_index]
    event = one_hour[event_index]
    retest = one_hour[event_index + 1]
    pause = one_hour[event_index + 2]
    current = one_hour[event_index + 3]
    if up:
        level = max(candle.high for candle in prior)
        one_hour[event_index] = replace(
            event,
            open=level - Decimal("0.02"),
            high=level + Decimal("0.10"),
            low=level - Decimal("0.03"),
            close=level + Decimal("0.08"),
            volume=event.volume * Decimal("2"),
        )
        one_hour[event_index + 1] = replace(
            retest,
            open=level + Decimal("0.05"),
            high=level + Decimal("0.06"),
            low=level - Decimal("0.02"),
            close=level + Decimal("0.02"),
        )
        one_hour[event_index + 2] = replace(
            pause,
            open=level + Decimal("0.02"),
            high=level + Decimal("0.05"),
            low=level,
            close=level + Decimal("0.04"),
        )
        one_hour[event_index + 3] = replace(
            current,
            open=level + Decimal("0.04"),
            high=level + Decimal("0.17"),
            low=level + Decimal("0.03"),
            close=level + Decimal("0.15"),
        )
    else:
        level = min(candle.low for candle in prior)
        one_hour[event_index] = replace(
            event,
            open=level + Decimal("0.02"),
            high=level + Decimal("0.03"),
            low=level - Decimal("0.10"),
            close=level - Decimal("0.08"),
            volume=event.volume * Decimal("2"),
        )
        one_hour[event_index + 1] = replace(
            retest,
            open=level - Decimal("0.05"),
            high=level + Decimal("0.02"),
            low=level - Decimal("0.06"),
            close=level - Decimal("0.02"),
        )
        one_hour[event_index + 2] = replace(
            pause,
            open=level - Decimal("0.02"),
            high=level,
            low=level - Decimal("0.05"),
            close=level - Decimal("0.04"),
        )
        one_hour[event_index + 3] = replace(
            current,
            open=level - Decimal("0.04"),
            high=level - Decimal("0.03"),
            low=level - Decimal("0.17"),
            close=level - Decimal("0.15"),
        )
    return one_hour, trending_candles(up=up, count=210)


def _early_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "strategy_profile": StrategyProfile.EARLY_MOMENTUM,
            "early_momentum_shorts_enabled": True,
        }
    )


def test_regime_breakout_does_not_require_macd_rsi_or_adx_confirmation(
    settings: Settings,
) -> None:
    configured = _settings(settings)
    one_hour, four_hour = _breakout_history()
    one = replace(
        snapshot(one_hour),
        macd_hist=Decimal("-1"),
        rsi14=Decimal("40"),
        adx14=Decimal("5"),
    )
    four = snapshot(four_hour)
    instrument = make_instrument()
    quote = Quote(
        instrument_id=instrument.id,
        bid=one.close - Decimal("0.01"),
        ask=one.close,
        last=one.close,
        quote_volume_24h=Decimal("100000000"),
    )

    decision = RegimeBreakoutAlpha(configured).evaluate(
        one_hour, four_hour, one, four, quote, Direction.LONG
    )

    assert decision.eligible
    assert "MACD/RSI/ADX retained as low-weight diagnostics only" in decision.evidence


def test_regime_breakout_candidate_carries_auditable_regime_evidence(
    settings: Settings,
) -> None:
    configured = _settings(settings)
    one_hour, four_hour = _breakout_history()
    instrument = make_instrument()
    quote = Quote(
        instrument_id=instrument.id,
        bid=one_hour[-1].close - Decimal("0.01"),
        ask=one_hour[-1].close,
        last=one_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
    )

    candidate = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour
    )[0]

    assert candidate.score >= configured.min_candidate_score
    assert candidate.strategy_profile == StrategyProfile.REGIME_BREAKOUT.value
    assert candidate.market_regime == "trend_bull"
    assert candidate.breakout_level is not None
    assert candidate.return_7d is not None and candidate.return_7d > 0
    assert candidate.return_30d is not None and candidate.return_30d > 0
    assert candidate.volatility_percentile is not None
    assert candidate.resistance_price is None, "broken resistance must not cap a breakout target"


def test_trend_quality_profile_keeps_indicators_as_a_consensus_layer(settings: Settings) -> None:
    configured = _settings(settings).model_copy(
        update={"strategy_profile": StrategyProfile.TREND_QUALITY}
    )
    one_hour, four_hour = _breakout_history()
    instrument = make_instrument()
    quote = Quote(
        instrument_id=instrument.id,
        bid=one_hour[-1].close - Decimal("0.01"),
        ask=one_hour[-1].close,
        last=one_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
    )

    candidate = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour
    )[0]

    assert candidate.score >= configured.min_candidate_score
    assert candidate.strategy_profile == StrategyProfile.TREND_QUALITY.value
    assert len(candidate.indicator_confirmations) >= 2
    assert any(item.startswith("EMA:") for item in candidate.indicator_confirmations)
    assert candidate.macd_cross_1h == "bullish"
    assert any("2-of-4" in reason or "3-of-4" in reason for reason in candidate.reasons)


def test_trend_quality_consensus_cannot_be_bypassed_by_one_indicator(
    settings: Settings,
) -> None:
    configured = _settings(settings).model_copy(
        update={
            "strategy_profile": StrategyProfile.TREND_QUALITY,
            "trend_quality_min_confirmations": 4,
        }
    )
    one_hour, four_hour = _breakout_history()
    one = snapshot(one_hour)
    four = snapshot(four_hour)
    instrument = make_instrument()
    quote = Quote(
        instrument_id=instrument.id,
        bid=one.close - Decimal("0.01"),
        ask=one.close,
        last=one.close,
        quote_volume_24h=Decimal("100000000"),
    )

    decision = TrendQualityAlpha(configured).evaluate(
        one_hour, four_hour, one, four, quote, Direction.LONG
    )

    assert not decision.eligible
    assert len(decision.indicator_confirmations) == 3
    assert any("below 4-of-4" in failure for failure in decision.failures)


def test_breakout_short_sleeve_requires_separate_explicit_enablement(settings: Settings) -> None:
    configured = _settings(settings)
    one_hour, four_hour = _breakout_history()
    # Construct a futures instrument without coupling this test to exchange adapters.
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=one_hour[-1].close - Decimal("0.01"),
        ask=one_hour[-1].close,
        last=one_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
    )

    candidates = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour
    )

    assert [candidate.direction for candidate in candidates] == [Direction.LONG]


def test_breakout_strategy_accepts_mixed_oscillators_but_rejects_profile_mismatch(
    settings: Settings,
) -> None:
    configured = _settings(settings)
    candidate = Candidate(
        instrument=make_instrument(),
        direction=Direction.LONG,
        score=Decimal("85"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.MIXED,
        reasons=("40h breakout",),
        support_price=Decimal("98"),
        strategy_profile=StrategyProfile.REGIME_BREAKOUT.value,
        market_regime="trend_bull",
        breakout_level=Decimal("99.5"),
    )

    signal = TrendMomentumStrategy(configured).signal(candidate)

    assert signal.strategy == StrategyProfile.REGIME_BREAKOUT.value
    assert signal.reward_risk_ratio == configured.reward_risk_multiple

    with pytest.raises(ValueError, match="does not match"):
        TrendMomentumStrategy(settings).signal(candidate)


def test_cross_sectional_rank_changes_priority_not_eligibility(settings: Settings) -> None:
    configured = _settings(settings)
    screener = MultiFactorScreener(configured)
    base = Candidate(
        instrument=make_instrument(symbol="BTCUSDT"),
        direction=Direction.LONG,
        score=Decimal("80"),
        entry_price=Decimal("100"),
        signal_bar_close=Decimal("100"),
        atr=Decimal("1"),
        spread_bps=Decimal("1"),
        quote_volume_24h=Decimal("1000000"),
        momentum_state=MomentumState.MIXED,
        reasons=("eligible",),
        strategy_profile=StrategyProfile.REGIME_BREAKOUT.value,
        return_30d=Decimal("0.05"),
    )
    stronger = replace(
        base,
        instrument=make_instrument(symbol="ETHUSDT"),
        return_30d=Decimal("0.20"),
    )

    ranked = screener._apply_cross_sectional_ranking([base, stronger])

    by_symbol = {candidate.instrument.id.symbol: candidate for candidate in ranked}
    assert by_symbol["ETHUSDT"].score > by_symbol["BTCUSDT"].score
    assert all(candidate.score >= configured.min_candidate_score for candidate in ranked)


def test_empirical_profile_separates_regime_setup_and_quality(settings: Settings) -> None:
    configured = _empirical_settings(settings)
    one_hour, four_hour, daily = _empirical_history()
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four_hour[-1].close - Decimal("0.01"),
        ask=four_hour[-1].close,
        last=four_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
        observed_at=four_hour[-1].closed_at,
    )

    candidates = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour, daily=daily
    )

    assert [candidate.direction for candidate in candidates] == [Direction.LONG]
    candidate = candidates[0]
    assert candidate.score >= configured.min_candidate_score
    assert candidate.strategy_profile == StrategyProfile.EMPIRICAL_TREND.value
    assert candidate.setup_signal == EmpiricalSetup.BREAKOUT.value
    assert candidate.aligned_momentum_horizons == 3
    assert candidate.technical_quality_score is not None
    assert candidate.atr == snapshot(four_hour).atr14
    assert any("one configured setup" in reason for reason in candidate.reasons)


def test_empirical_technical_quality_is_bounded_ranking_not_a_veto(
    settings: Settings,
) -> None:
    configured = _empirical_settings(settings)
    one_hour, four_hour, daily = _empirical_history()
    one = replace(
        snapshot(one_hour),
        ema20=Decimal("8"),
        ema50=Decimal("9"),
        rsi14=Decimal("40"),
        adx14=Decimal("25"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    four = replace(
        snapshot(four_hour),
        ema20=Decimal("8"),
        ema50=Decimal("9"),
        rsi14=Decimal("40"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four.close - Decimal("0.01"),
        ask=four.close,
        last=four.close,
        quote_volume_24h=Decimal("100000000"),
    )
    unused_pullback = ContinuationTrigger(False, False, True, False, False, False)

    decision = EmpiricalTrendAlpha(configured).evaluate(
        one_hour,
        four_hour,
        daily,
        one,
        four,
        snapshot(daily),
        quote,
        Direction.LONG,
        unused_pullback,
    )

    assert decision.eligible
    assert decision.technical_quality_score is not None
    assert Decimal("-4") <= decision.technical_quality_score <= Decimal("8")
    assert decision.indicator_conflicts


def test_empirical_pullback_and_breakout_are_mutually_exclusive_hypotheses(
    settings: Settings,
) -> None:
    configured = _empirical_settings(settings).model_copy(
        update={"empirical_setup": EmpiricalSetup.PULLBACK}
    )
    one_hour, four_hour, daily = _empirical_history()
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four_hour[-1].close - Decimal("0.01"),
        ask=four_hour[-1].close,
        last=four_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
    )
    failed_pullback = ContinuationTrigger(False, False, True, False, False, False)

    decision = EmpiricalTrendAlpha(configured).evaluate(
        one_hour,
        four_hour,
        daily,
        snapshot(one_hour),
        snapshot(four_hour),
        snapshot(daily),
        quote,
        Direction.LONG,
        failed_pullback,
    )

    assert not decision.eligible, "a breakout must not bypass the selected pullback hypothesis"
    assert decision.setup_signal == EmpiricalSetup.PULLBACK.value
    assert any("pullback setup" in failure for failure in decision.failures)


def test_empirical_short_sleeve_requires_separate_authority(settings: Settings) -> None:
    one_hour, four_hour, daily = _empirical_history()
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four_hour[-1].close - Decimal("0.01"),
        ask=four_hour[-1].close,
        last=four_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
    )

    default_candidates = MultiFactorScreener(_empirical_settings(settings)).evaluate_history(
        instrument, quote, one_hour, four_hour, daily=daily
    )
    enabled_candidates = MultiFactorScreener(
        _empirical_settings(settings).model_copy(update={"empirical_shorts_enabled": True})
    ).evaluate_history(instrument, quote, one_hour, four_hour, daily=daily)

    assert [candidate.direction for candidate in default_candidates] == [Direction.LONG]
    assert [candidate.direction for candidate in enabled_candidates] == [
        Direction.LONG,
        Direction.SHORT,
    ]


def test_layered_directional_momentum_transform_is_exactly_long_short_symmetric() -> None:
    positive = (Decimal("0.12"), Decimal("0.25"), Decimal("0.40"))
    negative = (-positive[0], -positive[1], -positive[2])

    long_score = directional_momentum_score(positive, Direction.LONG)
    short_score = directional_momentum_score(negative, Direction.SHORT)

    assert long_score == short_score
    assert long_score > 0
    assert directional_momentum_score(positive, Direction.SHORT) == -long_score


def test_layered_profile_finds_falling_momentum_for_short_only(settings: Settings) -> None:
    configured = _layered_settings(settings).model_copy(update={"layered_shorts_enabled": True})
    one_hour, four_hour, daily = _declining_breakout_history()
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four_hour[-1].close,
        ask=four_hour[-1].close + Decimal("0.01"),
        last=four_hour[-1].close,
        quote_volume_24h=Decimal("100000000"),
        observed_at=four_hour[-1].closed_at,
    )

    candidates = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour, daily=daily
    )
    by_direction = {candidate.direction: candidate for candidate in candidates}

    assert by_direction[Direction.LONG].score == 0
    long_momentum = by_direction[Direction.LONG].directional_momentum_score
    assert long_momentum is not None and long_momentum < 0
    assert by_direction[Direction.SHORT].score >= configured.min_candidate_score
    short_momentum = by_direction[Direction.SHORT].directional_momentum_score
    assert short_momentum is not None and short_momentum > 0
    assert by_direction[Direction.SHORT].strategy_profile == "layered_tsmom_v6"
    assert any(
        "ranking evidence, not an entry veto" in reason
        for reason in by_direction[Direction.SHORT].reasons
    )


def test_layered_technical_indicators_rank_but_cannot_veto(settings: Settings) -> None:
    configured = _layered_settings(settings)
    one_hour, four_hour, daily = _empirical_history()
    one = replace(
        snapshot(one_hour),
        ema20=Decimal("8"),
        ema50=Decimal("9"),
        rsi14=Decimal("40"),
        adx14=Decimal("25"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    four = replace(
        snapshot(four_hour),
        ema20=Decimal("8"),
        ema50=Decimal("9"),
        rsi14=Decimal("40"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=four.close - Decimal("0.01"),
        ask=four.close,
        last=four.close,
        quote_volume_24h=Decimal("100000000"),
    )

    decision = LayeredMomentumAlpha(configured).evaluate(
        one_hour,
        four_hour,
        daily,
        one,
        four,
        snapshot(daily),
        quote,
        Direction.LONG,
        ContinuationTrigger(False, False, True, False, False, False),
    )

    assert decision.eligible
    assert decision.indicator_conflicts
    assert decision.technical_quality_score is not None
    assert decision.directional_momentum_score is not None
    assert decision.directional_momentum_score > 0


@pytest.mark.parametrize(
    ("up", "direction"),
    [(True, Direction.LONG), (False, Direction.SHORT)],
)
def test_early_momentum_requires_volume_breakout_retest_and_resumption(
    settings: Settings, up: bool, direction: Direction
) -> None:
    configured = _early_settings(settings)
    one_hour, _ = _early_momentum_history(up=up)

    setup = impulse_retest(one_hour, direction, configured)

    assert setup.passed
    assert setup.breakout_relative_volume is not None
    assert setup.breakout_relative_volume >= configured.early_breakout_min_relative_volume
    assert setup.breakout_age_bars == 3
    assert setup.retest_distance_atr is not None


def test_early_momentum_rejects_price_breakout_without_volume(settings: Settings) -> None:
    configured = _early_settings(settings)
    one_hour, _ = _early_momentum_history(up=True)
    event_index = len(one_hour) - 4
    one_hour[event_index] = replace(one_hour[event_index], volume=Decimal("1000"))

    setup = impulse_retest(one_hour, Direction.LONG, configured)

    assert not setup.passed
    assert setup.breakout_relative_volume is None
    assert any("volume" in failure for failure in setup.failures)


def test_early_momentum_current_bar_cannot_be_its_own_breakout(settings: Settings) -> None:
    configured = _early_settings(settings)
    one_hour = trending_candles(count=260)
    prior_high = max(candle.high for candle in one_hour[-21:-1])
    current = one_hour[-1]
    one_hour[-1] = replace(
        current,
        high=prior_high + Decimal("0.20"),
        close=prior_high + Decimal("0.15"),
        volume=current.volume * Decimal("3"),
    )

    setup = impulse_retest(one_hour, Direction.LONG, configured)

    assert not setup.passed
    assert setup.breakout_age_bars is None


@pytest.mark.parametrize(
    ("up", "expected_direction"),
    [(True, Direction.LONG), (False, Direction.SHORT)],
)
def test_early_momentum_profile_selects_only_the_aligned_side(
    settings: Settings, up: bool, expected_direction: Direction
) -> None:
    configured = _early_settings(settings)
    one_hour, four_hour = _early_momentum_history(up=up)
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    close = one_hour[-1].close
    quote = Quote(
        instrument_id=instrument.id,
        bid=close if expected_direction is Direction.SHORT else close - Decimal("0.01"),
        ask=close if expected_direction is Direction.LONG else close + Decimal("0.01"),
        last=close,
        quote_volume_24h=Decimal("100000000"),
        observed_at=one_hour[-1].closed_at,
    )

    candidates = MultiFactorScreener(configured).evaluate_history(
        instrument, quote, one_hour, four_hour
    )
    by_direction = {candidate.direction: candidate for candidate in candidates}

    assert by_direction[expected_direction].score >= configured.min_candidate_score
    assert by_direction[expected_direction].setup_signal == "volume_breakout_retest"
    assert by_direction[expected_direction].breakout_relative_volume is not None
    assert by_direction[expected_direction].directional_momentum_score is not None
    opposite = Direction.SHORT if expected_direction is Direction.LONG else Direction.LONG
    assert by_direction[opposite].score == 0


def test_early_momentum_indicators_rank_but_do_not_veto(settings: Settings) -> None:
    configured = _early_settings(settings)
    one_hour, four_hour = _early_momentum_history(up=True)
    one = replace(
        snapshot(one_hour),
        ema20=Decimal("8"),
        ema50=Decimal("9"),
        rsi14=Decimal("40"),
        adx14=Decimal("25"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    four = replace(
        snapshot(four_hour),
        ema20=Decimal("8"),
        rsi14=Decimal("40"),
        plus_di=Decimal("10"),
        minus_di=Decimal("30"),
    )
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES)
    quote = Quote(
        instrument_id=instrument.id,
        bid=one.close - Decimal("0.01"),
        ask=one.close,
        last=one.close,
        quote_volume_24h=Decimal("100000000"),
    )

    decision = EarlyMomentumAlpha(configured).evaluate(
        one_hour, four_hour, one, four, quote, Direction.LONG
    )

    assert decision.eligible
    assert decision.indicator_conflicts
    assert decision.technical_quality_score is not None


def test_soft_market_quality_warning_cannot_turn_into_a_hidden_gate(
    settings: Settings,
) -> None:
    configured = _early_settings(settings)
    screener = MultiFactorScreener(configured)
    one = snapshot(trending_candles())
    opposing_structure = classify(trending_candles(up=False))
    decision = AlphaDecision(
        eligible=True,
        intrinsic_score=configured.min_candidate_score,
        evidence=(),
        failures=(),
        market_regime="test",
        breakout_level=one.close,
        return_7d=Decimal("0.01"),
        return_30d=Decimal("0.01"),
        volatility_percentile=Decimal("0.5"),
    )

    score, evidence = screener._soft_market_quality_score(
        configured.min_candidate_score,
        one,
        decision,
        opposing_structure,
        Direction.LONG,
    )

    assert score >= configured.min_candidate_score
    assert any("warning" in reason for reason in evidence)
