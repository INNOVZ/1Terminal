"""Acceptance fixtures run the real evaluator and publication helper, never stub scores."""

import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from algotrader.adapters.external.models import (
    FearGreedReading,
    MacroSentimentContext,
    StablecoinSupplyMetric,
)
from algotrader.application import scan_recording
from algotrader.application.scan_recording import capture_scan
from algotrader.application.scan_replay import replay_scan
from algotrader.application.shadow_observer import ShadowObserver
from algotrader.application.shadow_replay import replay_shadow
from algotrader.application.shadow_runner import ShadowRunner
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.forward_outcomes import outcome_anchor
from algotrader.domain.models import Candle
from algotrader.domain.shadow_discovery import ShadowPolicy
from algotrader.infrastructure.scan_archive import LocalScanArchive
from algotrader.infrastructure.shadow_archive import LocalShadowArchive
from tests.test_forward_outcomes import ANCHOR, minute_path
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture
from tests.test_shadow_runner import Market


class AcceptanceMarket(Market):
    async def instruments(self):
        parent = parent_fixture()
        btc = replace(
            parent.cohort[0], id=replace(parent.cohort[0].id, symbol="BTCUSDT"), base_asset="BTC"
        )
        return [*parent.cohort, btc]

    async def quotes(self, instruments):
        quotes = parent_fixture().inputs[0].result
        result = []
        for quote in quotes:
            quote = replace(quote, funding_rate=None, basis_pct=None)
            if quote.instrument_id.symbol == "T0USDT":
                quote = replace(quote, quote_volume_24h=Decimal("50000000"))
            if quote.instrument_id.symbol == "T10USDT":
                quote = replace(
                    quote,
                    bid=Decimal("10.21"),
                    ask=Decimal("10.23"),
                    last=Decimal("10.22"),
                    funding_rate=Decimal("-0.00015"),
                    basis_pct=Decimal("0.0008"),
                    price_change_pct_24h=Decimal("1"),
                )
            result.append(quote)
        return [
            quote for quote in result if quote.instrument_id in {inst.id for inst in instruments}
        ]

    async def candles(self, instrument, interval, limit):
        if instrument.id.symbol != "T10USDT":
            return await super().candles(instrument, interval, limit)
        if interval == "1w":
            return []
        count, hours = (240, 1) if interval == "1h" else (70, 4)
        bars = []
        for i in range(count):
            if interval == "1h":
                p = Decimal("10") + Decimal(
                    str(round(0.20 if i >= count - 24 else 0.25 * math.sin(i * 0.2), 2))
                )
                high, low, close = p + Decimal("0.08"), p - Decimal("0.08"), p + Decimal("0.02")
                volume, turnover = ("2500", "250000") if i == count - 1 else ("1500", "150000")
            else:
                p, high, low, close = (
                    Decimal("10.15"),
                    Decimal("10.25"),
                    Decimal("10.05"),
                    Decimal("10.15"),
                )
                volume, turnover = "6000", "600000"
            bars.append(
                Candle(
                    NOW - timedelta(hours=(count - i) * hours),
                    NOW - timedelta(hours=(count - i - 1) * hours),
                    p,
                    high,
                    low,
                    close,
                    Decimal(volume),
                    Decimal(turnover),
                )
            )
        return bars

    async def derivatives_snapshot(self, instrument):
        value = await super().derivatives_snapshot(instrument)
        if instrument.id.symbol == "T10USDT":
            return replace(
                value,
                funding_rate=Decimal("-0.00015"),
                open_interest=Decimal("50000000"),
                open_interest_change_1h=Decimal("0.04"),
                open_interest_change_7d=Decimal("0.18"),
                taker_buy_sell_ratio=Decimal("1.25"),
                taker_buy_sell_ratio_4h=Decimal("1.35"),
                bid_depth_notional=Decimal("3000000"),
                ask_depth_notional=Decimal("1500000"),
            )
        return value

    async def decision_tickers(self):
        rows = ticker_fixture(parent_fixture())
        rows[10].update(lastPrice="10.22", priceChangePercent="1")
        return rows


class Macro:
    async def get_context(self):
        return MacroSentimentContext(
            FearGreedReading(55, "Neutral", NOW),
            StablecoinSupplyMetric(
                Decimal("160000000000"),
                Decimal("0.1"),
                Decimal("0.8"),
                Decimal("2"),
                observed_at=NOW,
            ),
            observed_at=NOW,
        )


async def capture_parent(settings, monkeypatch, *, limit):
    class Clock:
        @staticmethod
        def now(tz):
            return NOW

    monkeypatch.setattr(scan_recording, "datetime", Clock)
    market = AcceptanceMarket(ShadowPolicy())
    captured = await capture_scan(
        market,
        market,
        Macro(),
        settings,
        {
            "mode": ScreenerMode.ALL,
            "enforce_quality": True,
            "min_score": Decimal("68"),
            "max_symbols": limit,
        },
    )
    assert captured.failure is None
    return captured.record


async def test_real_qualified_discovery_and_rejection_archive_replay_and_outcomes(
    settings, monkeypatch, tmp_path
):
    parent = await capture_parent(settings, monkeypatch, limit=1)
    assert not any(
        event.get("symbol") == "T10USDT" and event.get("reason") == "selected"
        for event in parent.decisions
    )
    policy = ShadowPolicy(max_tokens=2)
    child = await ShadowRunner(AcceptanceMarket(policy), clock=lambda: NOW).run(parent, policy)
    assert child.status == "complete", child.journal
    qualified = [item for item in child.results if item["qualified"]]
    assert [item["symbol"] for item in qualified] == ["T10USDT"]
    assert any(
        event["symbol"] == "T19USDT" and event["reason"] == "no_setup" for event in child.journal
    )
    assert any(label["symbol"] == "T10USDT" and label["early_candidate"] for label in child.labels)
    LocalScanArchive(tmp_path).write_scan(parent)
    archive = LocalShadowArchive(tmp_path)
    archive.write_run(child)
    assert (await replay_shadow(archive.read_run(child.shadow_id), parent))["matches"]
    anchor = outcome_anchor(child.completed_at)

    class History:
        async def minute_candles_between(self, instrument, start, end):
            shift = start - ANCHOR
            scale = Decimal("0.1022") if instrument.id.symbol == "T10USDT" else Decimal("1")
            return [
                replace(
                    bar,
                    opened_at=bar.opened_at + shift,
                    closed_at=bar.closed_at + shift,
                    open=bar.open * scale,
                    high=bar.high * scale,
                    low=bar.low * scale,
                    close=bar.close * scale,
                )
                for bar in minute_path()
            ]

    summary = await ShadowObserver(archive, History(), pace_seconds=0).observe(
        child.shadow_id, 24, 20, now=anchor + timedelta(hours=25)
    )
    assert summary["full_coverage"]
    assert summary["qualified_symbols"] == ("T10USDT",)
    assert summary["additional_qualified_symbols"] == ("T10USDT",)


async def test_nonempty_production_all_core_is_identical_after_shadow_child(settings, monkeypatch):
    baseline = await capture_parent(settings, monkeypatch, limit=20)
    assert baseline.request["mode"] == ScreenerMode.ALL
    assert baseline.request["enforce_quality"] is True
    assert any(item["symbol"] == "T10USDT" for item in baseline.opportunities)
    assert (await replay_scan(baseline)).matches
    parent = await capture_parent(settings, monkeypatch, limit=1)
    policy = ShadowPolicy(max_tokens=2)
    await ShadowRunner(AcceptanceMarket(policy), clock=lambda: NOW).run(parent, policy)
    assert (await replay_scan(baseline)).matches
