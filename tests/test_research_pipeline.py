from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.application.research import beta_correlation, feature_information_report
from algotrader.domain.errors import VenueError
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
    def __init__(
        self,
        *,
        missing_hour: int | None = None,
        malformed_hour: int | None = None,
        missing_taker_hour: int | None = None,
        missing_funding: bool = False,
        provider_lag_hours: int = 0,
        zero_sell_hour: int | None = None,
    ) -> None:
        self.missing_hour = missing_hour
        self.malformed_hour = malformed_hour
        self.missing_taker_hour = missing_taker_hour
        self.missing_funding = missing_funding
        self.provider_lag_hours = provider_lag_hours
        self.zero_sell_hour = zero_sell_hour
        self.oi_request: dict[str, object] | None = None
        self.taker_request: dict[str, object] | None = None

    async def request(
        self, method: str, path: str, *, params: dict[str, object] | None = None, **_: Any
    ) -> object:
        del method
        hour_ms = 3_600_000
        provider_now_ms = int(datetime.now(UTC).timestamp() * 1000) - self.provider_lag_hours * hour_ms
        reference_ms = (provider_now_ms // hour_ms) * hour_ms
        started_ms = reference_ms - 168 * hour_ms
        quantities = {0: 50, 144: 80, 164: 90, 167: 100, 168: 110}
        oi_history = [
            {
                "timestamp": started_ms + index * hour_ms,
                "sumOpenInterest": "not-a-number" if index == self.malformed_hour else str(quantities.get(index, 100)),
                "sumOpenInterestValue": str(1000 + index * 10),
            }
            for index in range(169)
            if index != self.missing_hour
        ]
        taker_history = [
            {
                "buySellRatio": "2.0" if offset == 1 else "1.0",
                "buyVol": "20" if offset == 1 else "10",
                "sellVol": "0" if offset == self.zero_sell_hour else "10",
                "timestamp": reference_ms - offset * hour_ms,
            }
            for offset in range(24, 0, -1)
            if offset != self.missing_taker_hour
        ]
        taker_history.append(
            {"buySellRatio": "100", "buyVol": "1000", "sellVol": "10", "timestamp": reference_ms}
        )  # Current, still-open hourly period must not affect a closed-window ratio.
        payloads: dict[str, object] = {
            "/fapi/v1/premiumIndex": {
                "markPrice": "101",
                "indexPrice": "100",
                "lastFundingRate": "0.0002",
                "time": provider_now_ms,
            },
            "/fapi/v1/openInterest": {
                "openInterest": "110",
                "time": provider_now_ms,
            },
            "/futures/data/openInterestHist": oi_history,
            "/futures/data/takerlongshortRatio": taker_history,
            "/fapi/v1/depth": {
                "bids": [["100", "2"]],
                "asks": [["101", "1"]],
            },
        }
        if path == "/futures/data/openInterestHist":
            self.oi_request = params
        if path == "/futures/data/takerlongshortRatio":
            self.taker_request = params
        if path == "/fapi/v1/premiumIndex" and self.missing_funding:
            return {"markPrice": "101", "indexPrice": "100", "time": provider_now_ms}
        return payloads[path]


async def test_usdm_derivatives_snapshot_uses_public_market_data() -> None:
    client = DerivativesClient()
    adapter = BinanceUsdmAdapter(client)  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.funding_rate == Decimal("0.0002")
    assert snapshot.basis_rate == Decimal("0.01")
    assert snapshot.open_interest_change_1h == Decimal("0.1")
    assert snapshot.open_interest_change_4h == Decimal(20) / Decimal(90)
    assert snapshot.open_interest_change_24h == Decimal("0.375")
    assert snapshot.open_interest_change_7d == Decimal("1.2")
    assert snapshot.taker_buy_sell_ratio == Decimal("2")
    assert snapshot.taker_buy_sell_ratio_4h == Decimal("1.25")
    assert snapshot.taker_buy_sell_ratio_24h == Decimal(250) / Decimal(240)
    assert snapshot.depth_imbalance == Decimal("99") / Decimal("301")
    assert client.oi_request is not None
    assert client.oi_request["period"] == "1h"
    assert int(client.oi_request["limit"]) >= 169
    assert client.taker_request == {"symbol": "BTCUSDT", "period": "1h", "limit": 26}


async def test_usdm_derivatives_snapshot_does_not_invent_missing_one_hour_oi() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(missing_hour=167))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.open_interest_change_1h is None
    assert snapshot.open_interest_change_4h == Decimal(20) / Decimal(90)
    assert snapshot.open_interest_change_7d == Decimal("1.2")


async def test_missing_funding_is_not_reported_as_observed_zero_funding() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(missing_funding=True))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.funding_rate is None
    assert snapshot.degraded_telemetry is True


async def test_live_derivatives_do_not_accept_stale_provider_clock() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(provider_lag_hours=6))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.degraded_telemetry is True
    assert snapshot.open_interest_change_1h is None
    assert snapshot.open_interest_change_7d is None
    assert snapshot.taker_buy_sell_ratio is None


async def test_usdm_derivatives_snapshot_skips_malformed_oi_observation() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(malformed_hour=167))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.open_interest_change_1h is None
    assert snapshot.open_interest_change_4h == Decimal(20) / Decimal(90)


async def test_usdm_derivatives_snapshot_does_not_invent_taker_windows_with_gaps() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(missing_taker_hour=2))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.taker_buy_sell_ratio == Decimal("2")
    assert snapshot.taker_buy_sell_ratio_4h is None
    assert snapshot.taker_buy_sell_ratio_24h is None


async def test_taker_window_includes_an_observed_zero_sell_hour() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(zero_sell_hour=2))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.taker_buy_sell_ratio == Decimal("2")
    assert snapshot.taker_buy_sell_ratio_4h == Decimal(50) / Decimal(30)
    assert snapshot.taker_buy_sell_ratio_24h == Decimal(250) / Decimal(230)


async def test_derivatives_snapshot_does_not_substitute_one_hour_oi_for_four_hours() -> None:
    adapter = BinanceUsdmAdapter(DerivativesClient(missing_hour=164))  # type: ignore[arg-type]

    snapshot = await adapter.derivatives_snapshot(_instrument())

    assert snapshot.open_interest_change_1h == Decimal("0.1")
    assert snapshot.open_interest_change_4h is None


async def test_live_usdm_candles_reject_stale_closed_bar() -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    class StaleClient:
        async def request(self, *_: Any, **__: Any) -> list[list[object]]:
            close_ms = now_ms - 3 * 3_600_000
            return [[close_ms - 3_600_000, "100", "101", "99", "100", "10", close_ms,
                     "1000", 1, "5", "500", "0"]]

    adapter = BinanceUsdmAdapter(StaleClient())  # type: ignore[arg-type]

    with pytest.raises(VenueError, match="stale"):
        await adapter.candles(_instrument(), "1h", 240)
