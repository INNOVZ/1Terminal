"""Read-only synthetic checks of architectural claims; not a historical backtest."""

import inspect
import json
from dataclasses import fields
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path

from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
from algotrader.domain.scoring import calculate_continuous_long_factor_scores
from algotrader.domain.screening_gates import LongGateInputs, evaluate_long_gates


def main():
    gate = LongGateInputs(
        close=D("100"),
        ema50_4h=D("96"),
        ema20_1h=D("98"),
        ema200_4h=D("101"),
        low_24h=D("99"),
        atr14_4h=D("2"),
        vcr_4h=D("0.85"),
        ema20_4h=D("98"),
        excursion_72h=D("0.12"),
        ret_24h=D("0.05"),
        rsi_1h=D("56"),
        rsi_4h=D("58"),
        high_30d=D("110"),
        low_30d=D("88"),
        poc_30d=D("98"),
        val_30d=D("94"),
        vah_30d=D("106"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=D("0.14"),
        rvol_4h=D("1.25"),
        oi_delta_7d=D("0.08"),
        oi_delta_4h=D("0.03"),
        has_kinetic_ignition=True,
    )
    accumulation = evaluate_long_gates(gate, "OI_ACCUMULATION_LONG")
    breakout = evaluate_long_gates(gate, "BREAKOUT_LONG")
    assert accumulation[0] and not breakout[0]
    assert not breakout[1]["L1"]
    assert all(v for k, v in breakout[1].items() if k != "L1")
    feature = LongArchetypeFeatures(
        close=D("100"),
        low_24h=D("99"),
        ema50_4h=D("96"),
        poc_30d=D("98"),
        vah_30d=D("99"),
        atr14_4h=D("2"),
        vcr_4h=D("1"),
        oi_delta_7d=D("0.2"),
        oi_delta_4h=D("0.03"),
        ret_7d=D("0.05"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=D("0.15"),
        local_swing_high_4h=D("99"),
        rvol_4h=D("1.5"),
        norm_momentum_1h=D("0.1"),
        btc_residual_alpha_24h=D("0"),
        macd_hist_4h=D("0.2"),
        macd_hist_4h_prev=D("0.1"),
        macd_hist_1h=D("0.1"),
        taker_buy_ratio_4h=D("1.1"),
        range_height_30d=D("0.6"),
        pos_in_range_30d=D("0.5"),
        dist_to_ceiling=D("0.1"),
        val_30d=D("94"),
        funding_rate_8h=D("0"),
    )
    label = classify_long_archetype(feature)
    breakout_conditions = (
        (feature.close >= feature.vah_30d or feature.close >= feature.local_swing_high_4h)
        and feature.rvol_4h >= D("1.25")
        and feature.oi_delta_4h >= 0
        and feature.norm_momentum_1h > 0
    )
    assert breakout_conditions and label.value == "OI_ACCUMULATION_LONG"
    params = inspect.signature(calculate_continuous_long_factor_scores).parameters
    source_paths = {
        Path(inspect.getsourcefile(x)).resolve()
        for x in (
            classify_long_archetype,
            evaluate_long_gates,
            calculate_continuous_long_factor_scores,
        )
    }
    print(
        json.dumps(
            {
                "kind": "synthetic_source_checks_not_historical_backtest",
                "gate_inputs": {f.name: str(getattr(gate, f.name)) for f in fields(gate)},
                "same_inputs_accumulation_gate_result": accumulation,
                "same_inputs_breakout_gate_result": breakout,
                "both_archetypes_conditions_satisfied": breakout_conditions,
                "chosen_archetype": label.value,
                "factor_scorer_oi_inputs": [p for p in params if p.startswith("oi_change")],
                "independence_example_085_pow_10": str(D("0.85") ** 10),
                "source_sha256": {
                    str(p): sha256(p.read_bytes()).hexdigest() for p in sorted(source_paths)
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
