from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import mean

from algotrader.domain.models import (
    Candidate,
    Candle,
    Direction,
    OpportunityObservation,
)

PREREGISTERED_POSITIVE_FEATURES = (
    "return_30d",
    "return_90d",
    "return_180d",
    "aligned_momentum_horizons",
    "directional_momentum_score",
    "breakout_relative_volume",
    "residual_return_6h",
    "residual_return_24h",
    "residual_return_7d",
    "momentum_acceleration_6h",
    "volume_participation_6h",
    "setup_strength",
    "setup_breakout",
    "setup_pullback",
    "technical_quality_score",
)


def beta_correlation(
    asset: Sequence[Candle], benchmark: Sequence[Candle], *, lookback: int = 168
) -> tuple[Decimal | None, Decimal | None]:
    """Hourly OLS beta and Pearson correlation on aligned, closed-bar returns."""
    asset_close = {bar.closed_at: bar.close for bar in asset if bar.closed and bar.close > 0}
    benchmark_close = {
        bar.closed_at: bar.close for bar in benchmark if bar.closed and bar.close > 0
    }
    timestamps = sorted(set(asset_close) & set(benchmark_close))[-(lookback + 1) :]
    if len(timestamps) < 25:
        return None, None
    asset_returns = [
        float(asset_close[current] / asset_close[previous] - 1)
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ]
    benchmark_returns = [
        float(benchmark_close[current] / benchmark_close[previous] - 1)
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ]
    benchmark_mean = mean(benchmark_returns)
    asset_mean = mean(asset_returns)
    covariance = sum(
        (asset_value - asset_mean) * (benchmark_value - benchmark_mean)
        for asset_value, benchmark_value in zip(asset_returns, benchmark_returns, strict=True)
    )
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark_returns)
    correlation = _correlation(asset_returns, benchmark_returns)
    if variance <= 0 or correlation is None:
        return None, None
    return Decimal(str(covariance / variance)), Decimal(str(correlation))


def _text(value: object) -> str | int | float | bool | None:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def candidate_features(candidate: Candidate) -> dict[str, str | int | float | bool | None]:
    """Stable, flat feature vector captured before any forward outcome is known."""
    names = (
        "rsi_1h",
        "rsi_4h",
        "macd_hist",
        "adx_1h",
        "ema20_1h",
        "ema50_1h",
        "ema200_1h",
        "ema20_4h",
        "ema50_4h",
        "ema200_4h",
        "relative_volume",
        "return_7d",
        "return_6h",
        "return_24h",
        "return_30d",
        "return_90d",
        "return_180d",
        "volatility_percentile",
        "cross_sectional_rank",
        "setup_strength",
        "aligned_momentum_horizons",
        "directional_momentum_score",
        "breakout_relative_volume",
        "breakout_age_bars",
        "technical_quality_score",
        "funding_rate",
        "basis_rate",
        "open_interest",
        "open_interest_change_1h",
        "taker_buy_sell_ratio",
        "bid_depth_notional",
        "ask_depth_notional",
        "depth_imbalance",
        "btc_beta",
        "btc_correlation",
        "residual_return_6h",
        "residual_return_24h",
        "residual_return_7d",
        "momentum_acceleration_6h",
        "volume_participation_6h",
    )
    raw = asdict(candidate)
    features = {name: _text(raw[name]) for name in names}
    features["setup_signal"] = candidate.setup_signal
    features["alt_momentum_phase"] = candidate.alt_momentum_phase
    setup_confirmed = candidate.setup_strength is not None and candidate.setup_strength > 0
    features["setup_breakout"] = candidate.setup_signal == "breakout" and setup_confirmed
    features["setup_pullback"] = candidate.setup_signal == "pullback" and setup_confirmed
    features["signal_bar_close"] = str(candidate.signal_bar_close)
    if candidate.signal_bar_close > 0:
        features["atr_fraction"] = str(candidate.atr / candidate.signal_bar_close)
        if candidate.macd_hist is not None:
            features["macd_hist_fraction"] = str(candidate.macd_hist / candidate.signal_bar_close)
    if candidate.atr > 0:
        for name in ("ema20_1h", "ema50_1h", "ema200_1h"):
            value = getattr(candidate, name)
            if value is not None:
                features[f"close_minus_{name}_atr"] = str(
                    (candidate.signal_bar_close - value) / candidate.atr
                )
    return features


def candidate_observation(candidate: Candidate, minimum_score: Decimal) -> OpportunityObservation:
    return OpportunityObservation(
        instrument_id=candidate.instrument.id,
        direction=candidate.direction,
        strategy_profile=candidate.strategy_profile,
        signal_bar_closed_at=candidate.signal_bar_closed_at or candidate.evaluated_at,
        observed_at=candidate.evaluated_at,
        eligible=candidate.score >= minimum_score,
        score=candidate.score,
        entry_price=candidate.entry_price,
        features=candidate_features(candidate),
        rejection_reasons=tuple(
            reason.removeprefix("entry blocked: ")
            for reason in candidate.reasons
            if reason.startswith("entry blocked: ")
        ),
    )


def _ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + end - 1) / 2.0
        for original, _ in ordered[index:end]:
            result[original] = rank
        index = end
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator > 0 else None


def _float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _directional_feature(name: str, value: float, direction: Direction) -> float:
    sign = 1.0 if direction is Direction.LONG else -1.0
    if name in {"rsi_1h", "rsi_4h"}:
        return (value - 50.0) * sign
    if name == "taker_buy_sell_ratio":
        return (value - 1.0) * sign
    if (
        name.startswith("return_")
        or name
        in {
            "funding_rate",
            "basis_rate",
            "depth_imbalance",
            "macd_hist_fraction",
        }
        or name.startswith("close_minus_")
        or name.startswith("residual_return_")
        or name == "momentum_acceleration_6h"
    ):
        return value * sign
    return value


def feature_information_report(
    observations: Sequence[OpportunityObservation],
    *,
    horizon_hours: int = 24,
    holdout_fraction: Decimal = Decimal("0.25"),
    minimum_samples: int = 20,
) -> dict[str, object]:
    """Measure each feature alone against direction-adjusted forward returns.

    Labels are formed only from later persisted observations. The last chronological
    fraction is an untouched holdout; no threshold or sign is selected from it.
    """
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if not Decimal("0") < holdout_fraction < Decimal("1"):
        raise ValueError("holdout_fraction must be strictly between zero and one")
    if minimum_samples <= 0:
        raise ValueError("minimum_samples must be positive")

    def experiment_key(item: OpportunityObservation) -> str:
        setup = item.features.get("setup_signal")
        return (
            f"{item.strategy_profile}:{setup}"
            if isinstance(setup, str) and setup
            else item.strategy_profile
        )

    experiments = sorted({experiment_key(item) for item in observations})
    if len(experiments) > 1:
        return {
            "scope": "segmented_by_strategy_experiment",
            "observations": len(observations),
            "profiles": {
                experiment: feature_information_report(
                    tuple(item for item in observations if experiment_key(item) == experiment),
                    horizon_hours=horizon_hours,
                    holdout_fraction=holdout_fraction,
                    minimum_samples=minimum_samples,
                )
                for experiment in experiments
            },
            "warning": (
                "Feature information is never pooled across strategy profiles or empirical "
                "setup hypotheses."
            ),
        }
    ordered = sorted(observations, key=lambda item: item.signal_bar_closed_at)
    price_by_instrument: dict[tuple[str, str], dict[datetime, float]] = defaultdict(dict)
    for item in ordered:
        price = _float(item.features.get("signal_bar_close"))
        if price is None:
            price = float(item.entry_price)
        if price > 0:
            price_by_instrument[
                (item.instrument_id.venue.value, item.instrument_id.symbol)
            ].setdefault(item.signal_bar_closed_at, price)

    price_series = {key: sorted(values.items()) for key, values in price_by_instrument.items()}
    timestamps = {key: [row[0] for row in rows] for key, rows in price_series.items()}

    labelled: list[tuple[OpportunityObservation, float]] = []
    horizon = timedelta(hours=horizon_hours)
    tolerance = timedelta(hours=2)
    for item in ordered:
        key = (item.instrument_id.venue.value, item.instrument_id.symbol)
        target = item.signal_bar_closed_at + horizon
        instrument_times = timestamps.get(key, [])
        future_index = bisect_left(instrument_times, target)
        future_row = (
            price_series[key][future_index] if future_index < len(instrument_times) else None
        )
        current_price = _float(item.features.get("signal_bar_close"))
        if current_price is None:
            current_price = float(item.entry_price)
        if future_row is None or future_row[0] > target + tolerance or current_price <= 0:
            continue
        future_price = future_row[1]
        raw_return = (future_price - current_price) / current_price
        directional = raw_return if item.direction is Direction.LONG else -raw_return
        labelled.append((item, directional))

    labelled_timestamps = sorted({item.signal_bar_closed_at for item, _ in labelled})
    holdout_start = None
    training_rows: list[tuple[OpportunityObservation, float]] = []
    holdout_rows: list[tuple[OpportunityObservation, float]] = []
    purged_rows = 0
    if len(labelled_timestamps) >= 2:
        split_index = min(
            len(labelled_timestamps) - 1,
            max(1, int(len(labelled_timestamps) * (1.0 - float(holdout_fraction)))),
        )
        holdout_start = labelled_timestamps[split_index]
        purge_before = holdout_start - horizon - tolerance
        training_rows = [row for row in labelled if row[0].signal_bar_closed_at < purge_before]
        holdout_rows = [row for row in labelled if row[0].signal_bar_closed_at >= holdout_start]
        purged_rows = len(labelled) - len(training_rows) - len(holdout_rows)
    feature_names = sorted(
        {name for item, _ in labelled for name, value in item.features.items() if value is not None}
    )

    def evaluate(rows: Iterable[tuple[OpportunityObservation, float]]) -> dict[str, object]:
        vectors: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for item, outcome in rows:
            for name, raw_value in item.features.items():
                if name == "signal_bar_close":
                    continue
                value = _float(raw_value)
                if value is not None:
                    vectors[name].append(
                        (_directional_feature(name, value, item.direction), outcome)
                    )
        result: dict[str, object] = {}
        for name in feature_names:
            pairs = vectors.get(name, [])
            if len(pairs) < minimum_samples:
                continue
            values = [pair[0] for pair in pairs]
            outcomes = [pair[1] for pair in pairs]
            ic = _correlation(_ranks(values), _ranks(outcomes))
            sorted_pairs = sorted(pairs)
            bucket = max(1, len(sorted_pairs) // 5)
            low = mean(outcome for _, outcome in sorted_pairs[:bucket])
            high = mean(outcome for _, outcome in sorted_pairs[-bucket:])
            result[name] = {
                "samples": len(pairs),
                "spearman_rank_ic": round(ic, 6) if ic is not None else None,
                "top_minus_bottom_quintile_return": round(high - low, 8),
            }
        return result

    training = evaluate(training_rows)
    holdout = evaluate(holdout_rows)

    def preregistered_checks() -> dict[str, object]:
        checks: dict[str, object] = {}
        for name in PREREGISTERED_POSITIVE_FEATURES:
            training_metric = training.get(name)
            holdout_metric = holdout.get(name)
            if not isinstance(training_metric, dict) or not isinstance(holdout_metric, dict):
                status = "insufficient_data"
            else:
                train_ic = training_metric.get("spearman_rank_ic")
                holdout_ic = holdout_metric.get("spearman_rank_ic")
                train_spread = training_metric.get("top_minus_bottom_quintile_return")
                holdout_spread = holdout_metric.get("top_minus_bottom_quintile_return")
                positive = all(
                    isinstance(value, int | float) and value > 0
                    for value in (train_ic, holdout_ic, train_spread, holdout_spread)
                )
                status = "positive_in_both_partitions" if positive else "inconsistent"
            checks[name] = {
                "expected_relationship": "higher feature -> higher direction-adjusted return",
                "status": status,
                "training": training_metric,
                "holdout": holdout_metric,
            }
        return checks

    return {
        "horizon_hours": horizon_hours,
        "observations": len(observations),
        "labelled_rows": len(labelled),
        "split": "chronological_unique_timestamp_with_purge",
        "holdout_fraction": str(holdout_fraction),
        "holdout_started_at": holdout_start.isoformat() if holdout_start else None,
        "purge_hours": horizon_hours + int(tolerance.total_seconds() // 3600),
        "training_rows": len(training_rows),
        "purged_rows": purged_rows,
        "holdout_rows": len(holdout_rows),
        "strategy_profile": observations[0].strategy_profile if observations else None,
        "experiment": experiments[0] if experiments else None,
        "training": training,
        "untouched_holdout": holdout,
        "preregistered_feature_checks": preregistered_checks(),
        "feature_admission_rule": (
            "A positive check is evidence for governed review only; it cannot promote a feature "
            "or strategy without an immutable experiment record and cost-aware portfolio replay."
        ),
        "warning": (
            "No forward labels yet; retain hourly observations until horizons mature."
            if not labelled
            else (
                "At least two distinct labelled timestamps are required for a holdout."
                if holdout_start is None
                else None
            )
        ),
    }
