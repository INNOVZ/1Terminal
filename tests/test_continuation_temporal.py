"""Live/replay time, component alignment, ranking and private-canary isolation."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from algotrader.application.bearish_continuation import score_continuation
from algotrader.application.continuation_canary import evaluate_canary, write_canary
from algotrader.application.continuation_context import ContinuationContext
from algotrader.application.scan_recording import CallTape, capture_scan
from algotrader.application.scan_replay import replay_scan
from algotrader.config import Settings
from algotrader.domain.continuation_inputs import HOUR_MS, aligned_inputs
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.models import Direction
from algotrader.domain.scan_observations import decode_value, encode_value
from algotrader.domain.screener_policy import publication_rejection_reasons
from algotrader.infrastructure.runtime import Runtime
from algotrader.infrastructure.scan_archive import canonical_bytes
from tests.test_bearish_continuation import NOW, fixture, with_inputs
from tests.test_top_gainer_short_watch import _macro

D = Decimal


def test_raw_series_alignment_and_no_silent_rollover_fallback():
    end = int(NOW.timestamp() * 1000)
    oi = {end - offset * HOUR_MS: D("1000") - offset for offset in range(169)}
    taker = {end - offset * HOUR_MS: (D("80"), D("100")) for offset in range(1, 5)}
    premium = {"time": end + 15_000, "lastFundingRate": "0.0001", "nextFundingTime": end + HOUR_MS}
    result = aligned_inputs(oi, taker, premium, end + 15_000, NOW + timedelta(seconds=15))
    assert result is not None
    assert result["oi_window_end"] == result["taker_window_end"] == NOW
    assert result["taker_ratio_4h"] == D("0.8")
    assert result["oi_change_4h"] == D("4") / D("996")
    assert result["funding_interval_hours"] is None
    assert result["funding_next_at"] == NOW + timedelta(hours=1)
    # A fresh fetch of previous-hour taker data is not aligned confirmation.
    del taker[end - HOUR_MS]
    assert aligned_inputs(oi, taker, premium, end + 15_000, NOW + timedelta(seconds=15)) is None


def test_unknown_window_metadata_stays_unknown_and_old_wire_bytes_survive():
    from algotrader.domain.bearish_continuation import evaluate_continuation

    h, q, _, quote, d = fixture()
    legacy = replace(d, continuation_inputs=None)
    result = evaluate_continuation(
        h, q, now=NOW, price=quote.last, tick=D(".01"), derivatives=legacy
    )
    assert result.gates[-1] is None
    encoded = encode_value(legacy)
    assert "continuation_inputs" not in encoded["fields"]
    assert canonical_bytes(decode_value(encoded)) == canonical_bytes(legacy)
    wrong = replace(
        d,
        continuation_inputs={**d.continuation_inputs, "taker_window_end": NOW - timedelta(hours=1)},
    )
    assert (
        evaluate_continuation(
            h, q, now=NOW, price=quote.last, tick=D(".01"), derivatives=wrong
        ).gates[-1]
        is None
    )


def test_ranking_uses_evidence_and_preserves_shared_discovery_floor():
    h, q, inst, quote, d = fixture()
    kwargs = dict(
        now=NOW,
        note=lambda *a, **kw: None,
        macro_narrative="test",
        beta=D(1),
        residual=D(0),
        is_discovery_tier=True,
    )
    normal = score_continuation(inst, quote, h, q, derivatives=d, **kwargs)
    strong = score_continuation(
        inst,
        quote,
        h,
        q,
        derivatives=with_inputs(d, taker_buy_sell_ratio=D(".75"), open_interest_change_4h=D(".05")),
        **kwargs,
    )
    assert normal is not None and strong is not None
    assert D(0) < normal.score < strong.score <= D(100)
    assert not publication_rejection_reasons(normal)
    assert (
        normal.continuation_evidence["ranking_components"]
        != strong.continuation_evidence["ranking_components"]
    )
    assert "score_floor" in publication_rejection_reasons(
        replace(normal, score=D(70), conviction=D(70))
    )
    assert normal.estimated_win_rate_pct == D(
        50
    )  # labelled uncalibrated prior, never inferred from ranking


@pytest.mark.parametrize("publication_delay,expected_count", [(15, 1), (75, 0), (3610, 0)])
async def test_live_delayed_enrichment_uses_recorded_decision_and_replays_offline(
    monkeypatch, publication_delay, expected_count
):
    from algotrader.application import continuation_context, scan_recording
    from scripts.research_verified_replay import offline

    h, q, inst, quote, d = fixture()
    market, macro = AsyncMock(), AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q if interval == "4h" else []

    market.candles.side_effect = candles
    market.derivatives_snapshot.return_value = with_inputs(
        d, observed_at=NOW + timedelta(seconds=15)
    )
    macro.get_context.return_value = _macro(NOW)

    class CaptureClock:
        ticks = iter((NOW, NOW + timedelta(seconds=30)))

        @classmethod
        def now(cls, tz):
            return next(cls.ticks)

    class DecisionClock:
        reads = 0

        @classmethod
        def now(cls, tz):
            cls.reads += 1
            return NOW + timedelta(seconds=15 if cls.reads <= 3 else publication_delay)

    monkeypatch.setattr(scan_recording, "datetime", CaptureClock)
    monkeypatch.setattr(continuation_context, "datetime", DecisionClock)
    captured = await capture_scan(
        market,
        market,
        macro,
        Settings(_env_file=None, bearish_continuation_enabled=True),
        {
            "min_score": D(0),
            "max_symbols": 1,
            "mode": ScreenerMode.DISTRIBUTION,
            "target_direction": Direction.SHORT,
            "enforce_quality": True,
        },
    )
    assert captured.failure is None, captured.failure
    assert len(captured.opportunities) == expected_count
    if expected_count:
        assert captured.opportunities[0].evaluated_at == NOW + timedelta(seconds=15)
    assert any(c.method == "continuation_context" for c in captured.record.inputs)
    assert any(c.method == "continuation_publication_time" for c in captured.record.inputs)
    assert captured.record.counts["continuation_refresh_requests"] == 0
    with offline():
        replay = await replay_scan(captured.record)
    assert replay.matches


async def test_stale_inputs_refresh_is_bounded_and_budget_failures_withhold():
    h, q, inst, quote, _ = fixture()
    market = AsyncMock()
    market.quotes.return_value = [replace(quote, observed_at=NOW + timedelta(minutes=6))]
    source = ContinuationContext(market, CallTape(), clock=lambda: NOW + timedelta(minutes=6))
    context = await source.get(inst, quote, h, q)
    assert context["refresh_requests"] == 1
    assert context["refresh_weight"] == 55
    assert context["quote"].observed_at == NOW + timedelta(minutes=6)
    source.reset()
    source.set_budget(2300)
    unavailable = await source.get(inst, quote, h, q)
    assert unavailable["status"] == "unavailable"
    assert unavailable["error_type"] == "ValueError"
    assert unavailable["refresh_requests"] == 0
    market.quotes.assert_awaited_once()


async def baseline_capture(monkeypatch):
    from algotrader.application import scan_recording

    h, q, inst, quote, _ = fixture()
    market, macro = AsyncMock(), AsyncMock()
    market.instruments.return_value = [inst]
    market.quotes.return_value = [quote]

    async def candles(instrument, interval, limit):
        return h if interval == "1h" else q if interval == "4h" else []

    market.candles.side_effect = candles
    macro.get_context.return_value = _macro(NOW)

    class Clock:
        @staticmethod
        def now(tz):
            return NOW

    monkeypatch.setattr(scan_recording, "datetime", Clock)
    captured = await capture_scan(
        market,
        None,
        macro,
        Settings(_env_file=None),
        {
            "min_score": D(0),
            "max_symbols": 1,
            "mode": ScreenerMode.DISTRIBUTION,
            "target_direction": Direction.SHORT,
        },
    )
    assert captured.record.status == "complete"
    return captured


async def test_canary_is_networkless_private_bounded_and_preserves_baseline(monkeypatch, tmp_path):
    from algotrader.infrastructure import runtime as runtime_module
    from scripts.research_verified_replay import offline

    captured = await baseline_capture(monkeypatch)
    original = canonical_bytes(captured.record)
    with offline():
        report = evaluate_canary(captured.record, max_symbols=1)
    assert report["evaluated"] <= 1 and report["network_requests"] == 0
    assert report["publication_enabled"] is False
    assert canonical_bytes(captured.record) == original
    saved = write_canary(tmp_path, report, keep=1)
    assert saved.stat().st_mode & 0o777 == 0o600
    assert saved.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            bearish_continuation_enabled=True,
            bearish_continuation_canary_enabled=True,
        )
    runtime = Runtime(
        Settings(
            _env_file=None, bearish_continuation_canary_enabled=True, research_archive_dir=tmp_path
        ),
        (),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "capture_scan", AsyncMock(return_value=captured))
    result = await runtime.run_crypto_scan()
    assert result.opportunities == captured.opportunities
    assert result.counts == captured.record.counts
    await runtime._canary_task
    assert runtime._canary_task.done()
    runtime.futures_market.quotes.assert_not_called()
    runtime.futures_market.derivatives_snapshot.assert_not_called()
    await runtime.close()


async def test_canary_budget_and_private_path_safety(monkeypatch, tmp_path):
    captured = await baseline_capture(monkeypatch)
    ticks = iter((0.0, 100.0))
    report = evaluate_canary(captured.record, seconds=0.01, clock=lambda: next(ticks))
    assert report["status"] == "budget_exhausted" and report["rows"] == []
    target = tmp_path / "other"
    target.mkdir()
    (tmp_path / "private-continuation").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_canary(tmp_path, report)


def test_raw_input_provenance_cannot_be_replaced_by_mismatched_aggregates():
    from algotrader.domain.bearish_continuation import evaluate_continuation
    from algotrader.domain.continuation_inputs import reproducible_inputs
    from scripts.research_evidence_contract import normalized

    h, q, _, quote, d = fixture()
    assert reproducible_inputs(d.continuation_inputs)
    normalized_d = normalized(d)
    assert (
        normalized_d.continuation_inputs["oi_window_end"] == d.continuation_inputs["oi_window_end"]
    )
    assert (
        normalized_d.continuation_inputs["funding_observed_at"]
        == d.continuation_inputs["funding_observed_at"]
    )
    bad = {**d.continuation_inputs, "oi_change_4h": D("0.5")}
    assert not reproducible_inputs(bad)
    result = evaluate_continuation(
        h,
        q,
        now=NOW,
        price=quote.last,
        tick=D(".01"),
        derivatives=replace(d, continuation_inputs=bad),
    )
    assert result.gates[-1] is None


async def test_failed_refresh_is_recorded_with_consumed_budget():
    h, q, inst, quote, _ = fixture()
    market = AsyncMock()
    market.quotes.side_effect = TimeoutError("unavailable")
    tape = CallTape()
    source = ContinuationContext(market, tape, clock=lambda: NOW + timedelta(minutes=6))
    result = await source.get(inst, quote, h, q)
    assert result["status"] == "unavailable"
    assert result["refresh_requests"] == 1 and result["refresh_weight"] == 55
    assert tape.calls[0].result == result


async def test_real_adapter_adds_aligned_evidence_without_extra_endpoints(monkeypatch):
    from algotrader.adapters.binance import futures
    from algotrader.domain.continuation_inputs import reproducible_inputs

    ms = int(NOW.timestamp() * 1000)
    paths = []

    class Clock:
        @staticmethod
        def now(tz):
            return NOW

    class Client:
        async def request(self, method, path, *, params=None):
            paths.append(path)
            return {
                "/fapi/v1/premiumIndex": {
                    "time": ms,
                    "lastFundingRate": "0.0001",
                    "markPrice": "100",
                    "indexPrice": "100",
                    "nextFundingTime": ms + HOUR_MS,
                },
                "/fapi/v1/openInterest": {"time": ms, "openInterest": "1000"},
                "/futures/data/openInterestHist": [
                    {"timestamp": ms - i * HOUR_MS, "sumOpenInterest": str(1000 - i)}
                    for i in range(169)
                ],
                "/futures/data/takerlongshortRatio": [
                    {"timestamp": ms - i * HOUR_MS, "buyVol": "80", "sellVol": "100"}
                    for i in range(1, 27)
                ],
                "/fapi/v1/depth": {"bids": [["100", "1000"]], "asks": [["101", "1000"]]},
            }[path]

    monkeypatch.setattr(futures, "datetime", Clock)
    _, _, instrument, _, _ = fixture()
    result = await futures.BinanceUsdmAdapter(Client()).derivatives_snapshot(instrument)
    assert len(paths) == 5
    assert result.open_interest_change_1h == D(1) / D(999)
    assert result.taker_buy_sell_ratio_4h == D("0.8")
    assert result.continuation_inputs["oi_window_end"] == NOW
    assert reproducible_inputs(result.continuation_inputs)


def test_publication_barrier_rechecks_archive_delay_and_hour_rollover():
    from algotrader.application.bearish_continuation import publication_inputs_current

    evidence = {
        "quote_as_of": NOW.isoformat(),
        "decision_at": NOW.isoformat(),
        "aligned_window_end": NOW.isoformat(),
    }
    assert publication_inputs_current(evidence, NOW + timedelta(seconds=60))
    assert not publication_inputs_current(evidence, NOW + timedelta(seconds=61))
    assert not publication_inputs_current(evidence, NOW - timedelta(seconds=6))
    assert not publication_inputs_current({}, NOW)
    assert not publication_inputs_current(None, NOW)
    newer = {**evidence, "quote_as_of": (NOW + timedelta(hours=1)).isoformat(),
             "decision_at": (NOW + timedelta(hours=1)).isoformat()}
    assert not publication_inputs_current(newer, NOW + timedelta(hours=1))


async def test_raw_v4_capture_rejects_unattested_live_context_before_acquisition():
    from scripts.research_immutable_capture import capture_control

    market = AsyncMock()
    with pytest.raises(ValueError, match="does not attest"):
        await capture_control(
            market, market, AsyncMock(),
            Settings(_env_file=None, bearish_continuation_enabled=True), {},
        )
    assert not market.mock_calls
