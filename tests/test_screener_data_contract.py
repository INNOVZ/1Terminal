from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.domain.archetypes import (
    LongArchetypeFeatures,
    ShortArchetypeFeatures,
    classify_long_archetype,
    classify_short_archetype,
)
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.execution_brackets import build_short_bracket
from algotrader.domain.indicators import resample_candles, taker_buy_ratio_from_candles
from algotrader.domain.models import Candle
from algotrader.domain.resistance_absorption import compute_resistance_absorption


def test_unavailable_candle_flow_does_not_become_neutral_taker_confirmation() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candle = Candle(
        opened_at=started,
        closed_at=started + timedelta(hours=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
    )

    assert taker_buy_ratio_from_candles([]) is None
    assert taker_buy_ratio_from_candles([candle]) is None


@pytest.mark.parametrize("missing_data", ["gap", "open_bar", "missing_tape", "short_window"])
def test_candle_taker_window_requires_complete_hourly_observations(missing_data: str) -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            opened_at=started + timedelta(hours=index),
            closed_at=started + timedelta(hours=index + 1),
            open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"),
            volume=Decimal("10"), quote_volume=Decimal("100"),
            taker_buy_volume=Decimal("5"), taker_buy_quote_volume=Decimal("50"),
        )
        for index in range(4)
    ]
    assert taker_buy_ratio_from_candles(candles, hours=4) == Decimal("1.0000")
    if missing_data == "gap":
        candles[0] = replace(candles[0], opened_at=started - timedelta(hours=1), closed_at=started)
    elif missing_data == "open_bar":
        candles[-1] = replace(candles[-1], closed=False)
    elif missing_data == "missing_tape":
        candles[-1] = replace(candles[-1], taker_buy_volume=Decimal("0"), taker_buy_quote_volume=Decimal("0"))
    else:
        candles.pop()

    assert taker_buy_ratio_from_candles(candles, hours=4) is None


def test_candle_taker_ratio_does_not_invent_a_denominator_when_sell_volume_is_zero() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candle = Candle(
        opened_at=started, closed_at=started + timedelta(hours=1),
        open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"),
        volume=Decimal("10"), quote_volume=Decimal("100"),
        taker_buy_volume=Decimal("10"), taker_buy_quote_volume=Decimal("100"),
    )

    assert taker_buy_ratio_from_candles([candle]) is None


@pytest.mark.parametrize(("rule", "bars_per_period"), [("1d", 6), ("1w", 42)])
@pytest.mark.parametrize("gap", [False, True])
def test_resampling_requires_a_complete_calendar_period(rule: str, bars_per_period: int, gap: bool) -> None:
    started = datetime(2026, 1, 5, tzinfo=UTC)  # Monday, the exchange's weekly boundary.
    candles = [
        Candle(
            opened_at=started + timedelta(hours=4 * index),
            closed_at=started + timedelta(hours=4 * (index + 1)),
            open=Decimal("10"), high=Decimal("11"), low=Decimal("9"), close=Decimal("10"),
            volume=Decimal("10"), quote_volume=Decimal("100"),
        )
        for index in range(bars_per_period + 1)  # Only one bar of the following period.
    ]
    if gap:
        candles.pop(2)

    result = resample_candles(candles, rule)

    assert len(result) == (0 if gap else 1)
    if result:
        assert result[0].opened_at == started
        assert result[0].closed_at == started + timedelta(hours=4 * bars_per_period)
        assert result[0].volume == Decimal(10 * bars_per_period)


def _long_features() -> LongArchetypeFeatures:
    return LongArchetypeFeatures(
        close=Decimal("100"),
        low_24h=Decimal("99"),
        ema50_4h=Decimal("100"),
        poc_30d=Decimal("100"),
        vah_30d=Decimal("110"),
        atr14_4h=Decimal("2"),
        vcr_4h=Decimal("1.3"),
        oi_delta_7d=Decimal("0.18"),
        oi_delta_4h=Decimal("0.04"),
        ret_7d=Decimal("0"),
        bb_inside_kc_4h=False,
        bb_bandwidth_4h=Decimal("0.3"),
        local_swing_high_4h=Decimal("110"),
        rvol_4h=Decimal("1"),
        norm_momentum_1h=Decimal("0"),
        btc_residual_alpha_24h=Decimal("0"),
        macd_hist_4h=Decimal("-0.1"),
        macd_hist_4h_prev=Decimal("0"),
        macd_hist_1h=Decimal("-0.1"),
        taker_buy_ratio_4h=None,
        funding_rate_8h=Decimal("-0.0001"),
        cvd_24h=Decimal("0"),
    )


def test_short_squeeze_archetype_requires_observed_buyer_absorption() -> None:
    features = _long_features()
    assert classify_long_archetype(features) is None
    assert classify_long_archetype(replace(features, taker_buy_ratio_4h=Decimal("1.05"))) is SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG


def test_breakout_volume_gate_does_not_treat_missing_oi_as_nonnegative_oi() -> None:
    features = replace(
        _long_features(),
        close=Decimal("110"),
        funding_rate_8h=None,
        oi_delta_7d=None,
        oi_delta_4h=None,
        rvol_4h=Decimal("1.3"),
        norm_momentum_1h=Decimal("0.2"),
        taker_buy_ratio_4h=Decimal("1"),
    )

    assert classify_long_archetype(features) is None
    assert classify_long_archetype(replace(features, oi_delta_4h=Decimal("0"))) is SetupLabel.BREAKOUT_LONG


def test_distribution_archetype_requires_observed_seller_dominance() -> None:
    features = ShortArchetypeFeatures(
        close=Decimal("100"),
        high_24h=Decimal("110"),
        high_30d=Decimal("120"),
        ema50_4h=Decimal("102"),
        poc_30d=Decimal("103"),
        atr14_4h=Decimal("2"),
        has_bearish_div=False,
        rsi_4h=Decimal("55"),
        rsi_1d=Decimal("55"),
        rsi_1w=Decimal("55"),
        oi_delta_7d=Decimal("0.15"),
        upper_wick_ratio_4h=Decimal("0.1"),
        max_ret_30d=Decimal("0.4"),
        taker_buy_ratio_24h=None,
        macd_hist_4h=Decimal("-0.2"),
        macd_hist_4h_prev=Decimal("-0.1"),
        pos_in_range_30d=Decimal("0.6"),
    )

    assert classify_short_archetype(features) is None
    assert classify_short_archetype(replace(features, taker_buy_ratio_24h=Decimal("0.9"))) is SetupLabel.DISTRIBUTION_SHORT


def test_missing_derivatives_do_not_confirm_absorption_pillars() -> None:
    _, status, flags = compute_resistance_absorption(
        taker_buy_ratio_4h=None,
        oi_delta_7d=None,
        oi_delta_4h=None,
        funding_bps=None,
    )

    assert flags["pillar1_cvd_absorption"] is False
    assert flags["pillar2_oi_expansion"] is False
    assert flags["pillar3_funding_squeeze"] is False
    assert status != "BREAKOUT_CONFIRMED"


def test_seller_dominated_taker_ratio_does_not_confirm_buyer_absorption() -> None:
    _, _, flags = compute_resistance_absorption(
        cvd_zscore_24h=0.6,
        taker_buy_ratio_4h=0.8,  # Buy/sell ratio: sellers are larger than buyers.
    )

    assert flags["pillar1_cvd_absorption"] is False


def test_short_bracket_measures_risk_from_the_published_entry() -> None:
    bracket = build_short_bracket(
        close=Decimal("100"), ema50_4h=Decimal("100.5"), poc_30d=Decimal("100.5"),
        atr14_4h=Decimal("2"), local_consolidation_low_4h=Decimal("96"),
        swing_high_4h=Decimal("104"), low_30d=Decimal("80"),
        vah_30d=Decimal("110"), val_30d=Decimal("95"), tick_size=Decimal("0.01"),
    )

    assert bracket.suggested_entry == Decimal("100")
    assert bracket.effective_entry == bracket.suggested_entry
    assert bracket.risk_unit == bracket.invalidation_stop - bracket.suggested_entry
    assert bracket.r_t2 == Decimal("2.45")
