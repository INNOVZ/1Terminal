"""Representative core classifier contracts before/after an isolated failed child."""

from decimal import Decimal as D

import pytest

from algotrader.application.shadow_budget import ShadowBudgetExceeded
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.archetypes import (
    ShortArchetypeFeatures,
    classify_long_archetype,
    classify_short_archetype,
)
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.shadow_discovery import ShadowPolicy
from tests.test_shadow_discovery import NOW
from tests.test_shadow_runner import Market, runner_parent
from tests.test_v27_ignition_upgrade import _make_archetype_features


@pytest.mark.parametrize(
    "label",
    [
        "COIL_SPRING_LONG",
        "OI_ACCUMULATION_LONG",
        "FUNDING_SQUEEZE_SPRING_LONG",
        "UTAD_DIVERGENCE_SHORT",
        "EXHAUSTION_REVERSAL_SHORT",
        "DISTRIBUTION_SHORT",
        "VCP_DISTRIBUTION_SHORT",
    ],
)
async def test_core_family_contract_survives_child_failure(settings, label):
    if label.endswith("LONG"):
        values = dict(
            close=D("100.50"),
            low_24h=D("98"),
            ema50_4h=D("99"),
            poc_30d=D("99"),
            vah_30d=D("102"),
            atr14_4h=D("2"),
            vcr_4h=D("0.85"),
            oi_delta_7d=D("0.05"),
            oi_delta_4h=D("0.02"),
            ret_7d=D("0.01"),
            bb_inside_kc_4h=True,
            bb_bandwidth_4h=D("0.12"),
            local_swing_high_4h=D("101"),
            rvol_4h=D("1.1"),
            norm_momentum_1h=D("0.2"),
            btc_residual_alpha_24h=D("0.01"),
            macd_hist_4h=D("0.05"),
            macd_hist_4h_prev=D("0.02"),
            macd_hist_1h=D("0.01"),
            taker_buy_ratio_4h=D("0.52"),
        )
        if label == "OI_ACCUMULATION_LONG":
            values.update(
                close=D("100"),
                low_24h=D("99.5"),
                vcr_4h=D("0.95"),
                oi_delta_7d=D("0.25"),
                ret_7d=D("0.03"),
            )
        if label == "FUNDING_SQUEEZE_SPRING_LONG":
            values.update(
                close=D("93.5"),
                low_24h=D("91.5"),
                ema50_4h=D("94"),
                poc_30d=D("98"),
                vah_30d=D("105"),
                val_30d=D("91"),
                funding_rate_8h=D("-0.0002"),
                oi_delta_24h=D("0.09"),
                taker_buy_ratio_1h=D("1.25"),
                low_30d=D("88"),
                low_4h=D("91.2"),
                pos_in_range_30d=D("0.25"),
                ema20_4h=D("92.5"),
                rvol_1h=D("1.5"),
            )
        features = _make_archetype_features(**values)
        classifier = classify_long_archetype
    else:
        values = dict(
            close=D("88"),
            high_24h=D("91"),
            high_30d=D("130"),
            ema50_4h=D("92"),
            poc_30d=D("90"),
            atr14_4h=D("3"),
            has_bearish_div=False,
            rsi_4h=D("42"),
            rsi_1d=D("52"),
            rsi_1w=D("55"),
            oi_delta_7d=D("0.1"),
            upper_wick_ratio_4h=D("0.25"),
            max_ret_30d=D("0.75"),
            taker_buy_ratio_24h=D("0.46"),
            macd_hist_4h=D("-0.15"),
        )
        if label == "UTAD_DIVERGENCE_SHORT":
            values.update(
                close=D("106"),
                high_24h=D("109.5"),
                high_30d=D("110"),
                ema50_4h=D("95"),
                poc_30d=D("100"),
                has_bearish_div=True,
                rsi_4h=D("68"),
                rsi_1d=D("70"),
                rsi_1w=D("65"),
                oi_delta_7d=D("0.2"),
                upper_wick_ratio_4h=D("0.45"),
                taker_buy_ratio_24h=D("0.47"),
                macd_hist_4h=D("-0.05"),
            )
        if label == "EXHAUSTION_REVERSAL_SHORT":
            values.update(
                close=D("104"),
                high_24h=D("108"),
                high_30d=D("120"),
                ema50_4h=D("90"),
                poc_30d=D("95"),
                rsi_4h=D("74"),
                rsi_1d=D("78"),
                rsi_1w=D("70"),
                oi_delta_7d=D("0.65"),
                upper_wick_ratio_4h=D("0.45"),
                max_ret_30d=D("0.9"),
                taker_buy_ratio_24h=D("0.49"),
                macd_hist_4h=D("0.05"),
            )
        if label == "VCP_DISTRIBUTION_SHORT":
            values.update(
                close=D("95"),
                ema50_4h=D("96"),
                taker_buy_ratio_24h=D("0.94"),
                macd_hist_4h=D("0.05"),
                macd_hist_4h_prev=D("0.1"),
            )
        features = ShortArchetypeFeatures(**values)
        classifier = classify_short_archetype
    assert classifier(features) == SetupLabel(label)
    policy = ShadowPolicy(max_tokens=1)

    class Failed(Market):
        async def candles(self, *args):
            raise ShadowBudgetExceeded("bounded failure")

    result = await ShadowRunner(Failed(policy), clock=lambda: NOW).run(
        runner_parent(settings), policy
    )
    assert result.status == "partial"
    assert classifier(features) == SetupLabel(label)
