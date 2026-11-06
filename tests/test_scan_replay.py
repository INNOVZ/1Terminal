from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application.scan_recording import CallTape, RecordingMarketData, capture_scan
from algotrader.application.scan_replay import ReplayMismatch, ReplayTape, replay_scan
from algotrader.config import Settings
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.models import DerivativesSnapshot, MarketKind, Venue
from tests.conftest import make_instrument, trending_candles
from tests.test_crypto_screener import FakeCryptoMarketData, FakeDerivativesData


class Macro:
    async def get_context(self) -> MacroSentimentContext:
        return MacroSentimentContext(
            fear_greed=FearGreedReading(55, "Neutral", datetime(2026, 10, 24, tzinfo=UTC)),
            stablecoin_supply=StablecoinSupplyMetric(Decimal("160000000000"), Decimal("0.1"), Decimal("0.8"), Decimal("2")),
        )


def market_fixture() -> Any:
    instruments = [make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol=symbol)
                   for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    return FakeCryptoMarketData(instruments,
        {inst.id.symbol: trending_candles(up=True, count=260) for inst in instruments},
        {inst.id.symbol: trending_candles(up=True, count=70) for inst in instruments})


async def captured_fixture(settings: Settings) -> Any:
    deriv = FakeDerivativesData({"SOLUSDT": DerivativesSnapshot(
        funding_rate=Decimal("-0.0002"), basis_rate=Decimal("0"), open_interest=Decimal("100000000"),
        open_interest_change_1h=Decimal("0.05"), taker_buy_sell_ratio=Decimal("1.30"),
        bid_depth_notional=Decimal("2000000"), ask_depth_notional=Decimal("1000000"))})
    return await capture_scan(market_fixture(), deriv, Macro(), settings,
        {"min_score": Decimal("40"), "mode": ScreenerMode.BREAKOUT, "enforce_quality": False})


async def test_real_screen_record_and_offline_replay_have_identical_nonempty_decisions(settings: Settings) -> None:
    captured = await captured_fixture(settings)
    assert captured.failure is None
    assert any(item["symbol"] == "SOLUSDT" for item in captured.record.opportunities)
    report = await replay_scan(captured.record)
    assert report.matches
    assert report.counts == captured.record.counts


async def test_replay_refuses_missing_calls_changed_outputs_and_code_identity(settings: Settings) -> None:
    captured = await captured_fixture(settings)
    for broken in (
        replace(captured.record, inputs=captured.record.inputs[:-1]),
        replace(captured.record, opportunities=()),
        replace(captured.record, identity={**captured.record.identity, "source_hash": "changed"}),
    ):
        with pytest.raises(ReplayMismatch):
            await replay_scan(broken)


async def test_empty_scan_replays_without_constructing_network_clients(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = FakeCryptoMarketData([], {}, {})
    captured = await capture_scan(empty, None, Macro(), settings, {"mode": ScreenerMode.ALL})
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay attempted to construct an HTTP client")
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    assert (await replay_scan(captured.record)).matches


async def test_repeated_calls_and_failure_types_are_preserved_without_exception_text() -> None:
    tape = CallTape()
    class Market:
        venue = Venue.BINANCE_USDM
        async def instruments(self) -> list[Any]:
            raise TimeoutError("sensitive provider message")
    recorder = RecordingMarketData(Market(), None, tape)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await recorder.instruments()
    assert [call.index for call in tape.calls] == [0, 1]
    assert [call.error for call in tape.calls] == ["TimeoutError", "TimeoutError"]
    replay = ReplayTape(tuple(tape.calls))
    for _ in range(2):
        with pytest.raises(TimeoutError):
            replay.take("instruments", {})
    replay.assert_consumed()


async def test_cancelled_provider_call_is_recorded_as_unavailable() -> None:
    tape = CallTape()
    async def blocked() -> None:
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await tape.capture("candles", {}, blocked)
    assert tape.calls[0].error == "CancelledError"
    with pytest.raises(asyncio.CancelledError):
        ReplayTape(tuple(tape.calls)).take("candles", {})


async def test_complete_scan_with_a_cancelled_candle_fetch_replays_without_extra_retries(settings: Settings) -> None:
    instrument = make_instrument(venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES, symbol="SOLUSDT")
    class CancelledMarket(FakeCryptoMarketData):
        async def candles(self, instrument, interval, limit):
            if interval == "1h":
                raise asyncio.CancelledError
            return []
    captured = await capture_scan(CancelledMarket([instrument], {}, {}), None, Macro(), settings, {})
    assert captured.record.status == "complete"
    assert (await replay_scan(captured.record)).matches
