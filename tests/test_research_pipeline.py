from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.research import beta_correlation, feature_information_report
from algotrader.domain.models import (
    AssetClass,
    Candle,
    Direction,
    Instrument,
    InstrumentId,
    MarketKind,
    OpportunityObservation,
    Venue,
)
from algotrader.infrastructure.sqlite_state import SqliteStateRepository


def _instrument() -> Instrument:
    return Instrument(
        id=InstrumentId(Venue.BINANCE_USDM, "BTCUSDT"),
        base_asset="BTC",
        quote_asset="USDT",
        asset_class=AssetClass.CRYPTO,
        market_kind=MarketKind.FUTURES,
        trading=True,
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=3,
    )


def _candle(closed_at: datetime, close: Decimal) -> Candle:
    return Candle(
        opened_at=closed_at - timedelta(hours=1),
        closed_at=closed_at,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
    )


async def test_opportunity_store_is_hourly_idempotent_and_keeps_rejections(
    tmp_path: Path,
) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3")
    await repository.initialize()
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    observation = OpportunityObservation(
        instrument_id=_instrument().id,
        direction=Direction.LONG,
        strategy_profile="research",
        signal_bar_closed_at=observed_at,
        observed_at=observed_at,
        eligible=False,
        score=Decimal("0"),
        entry_price=Decimal("100"),
        features={"funding_rate": "0.0001", "rsi_1h": "51"},
        rejection_reasons=("breakout not confirmed",),
    )

    await repository.record_opportunities((observation,))
    await repository.record_opportunities((observation,))

    stored = await repository.list_opportunities()
    assert len(stored) == 1
    assert stored[0].eligible is False
    assert stored[0].rejection_reasons == ("breakout not confirmed",)


async def test_opportunity_store_prunes_rows_outside_retention(tmp_path: Path) -> None:
    repository = SqliteStateRepository(tmp_path / "state.sqlite3", research_retention_days=30)
    await repository.initialize()
    started = datetime(2026, 1, 1, tzinfo=UTC)

    def observation(observed_at: datetime) -> OpportunityObservation:
        return OpportunityObservation(
            instrument_id=_instrument().id,
            direction=Direction.LONG,
            strategy_profile="research",
            signal_bar_closed_at=observed_at,
            observed_at=observed_at,
            eligible=False,
            score=Decimal("0"),
            entry_price=Decimal("100"),
            features={"signal_bar_close": "100"},
        )

    await repository.record_opportunities((observation(started),))
    await repository.record_opportunities((observation(started + timedelta(days=31)),))

    stored = await repository.list_opportunities()
    assert tuple(item.signal_bar_closed_at for item in stored) == (started + timedelta(days=31),)


def test_feature_report_uses_chronological_untouched_holdout() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    observations = tuple(
        OpportunityObservation(
            instrument_id=_instrument().id,
            direction=Direction.LONG,
            strategy_profile="research",
            signal_bar_closed_at=started + timedelta(hours=index),
            observed_at=started + timedelta(hours=index),
            eligible=index % 2 == 0,
            score=Decimal(index),
            entry_price=Decimal("100") + Decimal(index),
            features={"ordered_feature": str(index), "missing_feature": None},
        )
        for index in range(50)
    )

    report = feature_information_report(observations, horizon_hours=1, minimum_samples=5)

    assert report["split"] == "chronological_unique_timestamp_with_purge"
    assert report["purged_rows"] > 0
    assert "ordered_feature" in report["training"]  # type: ignore[operator]
    assert "ordered_feature" in report["untouched_holdout"]  # type: ignore[operator]


def test_feature_report_never_splits_one_timestamp_across_partitions() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    observations = tuple(
        OpportunityObservation(
            instrument_id=_instrument().id,
            direction=direction,
            strategy_profile="research",
            signal_bar_closed_at=started + timedelta(hours=index),
            observed_at=started + timedelta(hours=index),
            eligible=False,
            score=Decimal("0"),
            entry_price=Decimal("100") + Decimal(index),
            features={
                "signal_bar_close": str(Decimal("100") + Decimal(index)),
                "ordered_feature": str(index),
            },
        )
        for index in range(80)
        for direction in Direction
    )

    report = feature_information_report(observations, horizon_hours=1, minimum_samples=5)

    assert report["training_rows"] % 2 == 0
    assert report["holdout_rows"] % 2 == 0
    assert report["purged_rows"] % 2 == 0


def test_feature_report_never_pools_different_strategy_profiles() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    observations = tuple(
        OpportunityObservation(
            instrument_id=_instrument().id,
            direction=Direction.LONG,
            strategy_profile=profile,
            signal_bar_closed_at=started + timedelta(hours=index),
            observed_at=started + timedelta(hours=index),
            eligible=False,
            score=Decimal("0"),
            entry_price=Decimal("100") + Decimal(index),
            features={
                "signal_bar_close": str(Decimal("100") + Decimal(index)),
                "technical_quality_score": str(index),
            },
        )
        for profile in ("legacy_momentum_v1", "empirical_tsmom_v5")
        for index in range(40)
    )

    report = feature_information_report(observations, horizon_hours=1, minimum_samples=5)

    assert report["scope"] == "segmented_by_strategy_experiment"
    assert set(report["profiles"]) == {  # type: ignore[arg-type]
        "legacy_momentum_v1",
        "empirical_tsmom_v5",
    }


def test_feature_report_never_pools_empirical_setup_hypotheses() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    observations = tuple(
        OpportunityObservation(
            instrument_id=_instrument().id,
            direction=Direction.LONG,
            strategy_profile="empirical_tsmom_v5",
            signal_bar_closed_at=started + timedelta(hours=index),
            observed_at=started + timedelta(hours=index),
            eligible=False,
            score=Decimal("0"),
            entry_price=Decimal("100") + Decimal(index),
            features={
                "signal_bar_close": str(Decimal("100") + Decimal(index)),
                "setup_signal": setup,
                "setup_strength": str(index),
            },
        )
        for setup in ("breakout", "pullback")
        for index in range(40)
    )

    report = feature_information_report(observations, horizon_hours=1, minimum_samples=5)

    assert set(report["profiles"]) == {  # type: ignore[arg-type]
        "empirical_tsmom_v5:breakout",
        "empirical_tsmom_v5:pullback",
    }


def test_directional_momentum_feature_is_measured_against_long_short_outcomes() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    observations = tuple(
        OpportunityObservation(
            instrument_id=_instrument().id,
            direction=direction,
            strategy_profile="layered_tsmom_v6",
            signal_bar_closed_at=started + timedelta(hours=index),
            observed_at=started + timedelta(hours=index),
            eligible=False,
            score=Decimal("0"),
            entry_price=Decimal("100") + Decimal(index),
            features={
                "signal_bar_close": str(Decimal("100") + Decimal(index)),
                "directional_momentum_score": str(
                    Decimal(index) / Decimal("100")
                    if direction is Direction.LONG
                    else -Decimal(index) / Decimal("100")
                ),
            },
        )
        for index in range(80)
        for direction in Direction
    )

    report = feature_information_report(observations, horizon_hours=1, minimum_samples=10)
    checks = report["preregistered_feature_checks"]  # type: ignore[assignment]
    momentum = checks["directional_momentum_score"]  # type: ignore[index]

    assert momentum["status"] == "positive_in_both_partitions"  # type: ignore[index]


def test_beta_correlation_uses_aligned_returns() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    benchmark = [
        _candle(started + timedelta(hours=index), Decimal("100") + Decimal(index))
        for index in range(40)
    ]
    asset = [
        _candle(started + timedelta(hours=index), Decimal("200") + Decimal(index * 2))
        for index in range(40)
    ]

    beta, correlation = beta_correlation(asset, benchmark)

    assert beta is not None and Decimal("0.99") < beta < Decimal("1.01")
    assert correlation is not None and correlation > Decimal("0.99")


class DerivativesClient:
    async def request(
        self, method: str, path: str, *, params: dict[str, object] | None = None, **_: Any
    ) -> object:
        del method, params
        payloads: dict[str, object] = {
            "/fapi/v1/premiumIndex": {
                "markPrice": "101",
                "indexPrice": "100",
                "lastFundingRate": "0.0002",
            },
            "/fapi/v1/openInterest": {"openInterest": "1000"},
            "/futures/data/openInterestHist": [
                {"sumOpenInterestValue": "100"},
                {"sumOpenInterestValue": "110"},
            ],
            "/futures/data/takerlongshortRatio": [{"buySellRatio": "1.25"}],
            "/fapi/v1/depth": {
                "bids": [["100", "2"]],
                "asks": [["101", "1"]],
            },
        }
        return payloads[path]


async def test_usdm_derivatives_snapshot_uses_public_market_data() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient())  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.funding_rate == Decimal("0.0002")
    assert snapshot.basis_rate == Decimal("0.01")
    assert snapshot.open_interest_change_1h == Decimal("0.1")
    assert snapshot.taker_buy_sell_ratio == Decimal("1.25")
    assert snapshot.depth_imbalance == Decimal("99") / Decimal("301")
