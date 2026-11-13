from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from algotrader.api.portal import reconcile_radar_cohort
from algotrader.domain.archetypes import LongArchetypeFeatures, classify_long_archetype
from algotrader.domain.crypto_screener_models import SetupLabel
from algotrader.domain.models import Candle, Quote
from algotrader.domain.radar_lifecycle import (
    RadarLifecycleState,
    evaluate_radar_candidate_lifecycle,
)
from tests.conftest import make_instrument


def test_long_candidate_pulling_back_to_limit_entry_is_retained_at_entry_zone() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "HOMEUSDT",
        "direction": "long",
        "setup_label": "COIL_SPRING_LONG",
        "current_price": "0.005463",
        "suggested_entry": "0.005338",
        "pullback_entry": "0.005338",
        "breakout_trigger": "0.005649",
        "suggested_stop": "0.004933",
        "suggested_targets": ["0.005946", "0.006351", "0.006877"],
        "poc_price": "0.005203",
        "val_price": "0.005024",
        "vah_price": "0.005596",
        "evaluated_at": (now - timedelta(hours=16)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=2)).timestamp()),
            "open": 0.00542,
            "high": 0.00544,
            "low": 0.005344,
            "close": 0.00538,
            "volume": 1000,
        },
        {
            "time": int((now - timedelta(hours=1)).timestamp()),
            "open": 0.00538,
            "high": 0.00541,
            "low": 0.00536,
            "close": 0.00539,
            "volume": 1200,
        },
    ]

    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("0.005390"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is True
    assert decision.state is RadarLifecycleState.AT_ENTRY_ZONE
    assert "AT ENTRY ZONE" in decision.label


def test_long_candidate_that_bounced_from_limit_entry_is_in_active_trade() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "ZECUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "1313.98",
        "suggested_entry": "1257.18",
        "breakout_trigger": "1325.66",
        "suggested_stop": "1096.55",
        "suggested_targets": ["1484.75", "1590.81", "1728.69"],
        "poc_price": "1227.20",
        "val_price": "1192.25",
        "vah_price": "1323.31",
        "evaluated_at": (now - timedelta(hours=16)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=6)).timestamp()),
            "open": 1310.0,
            "high": 1387.98,
            "low": 1247.56,
            "close": 1311.79,
            "volume": 5000,
        },
    ]

    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("1311.79"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is True
    assert decision.state is RadarLifecycleState.IN_ACTIVE_TRADE
    assert decision.pnl_from_entry_pct == Decimal("4.3")
    assert "TRIGGER OBSERVED" in decision.label
    assert decision.triggered_at is not None


def test_candidate_breaching_stop_loss_is_eliminated() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    long_cand = {
        "symbol": "FAILUSDT",
        "direction": "long",
        "suggested_entry": "10.0",
        "suggested_stop": "9.20",
        "suggested_targets": ["11.5", "12.5", "14.0"],
        "evaluated_at": (now - timedelta(hours=8)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=2)).timestamp()),
            "open": 9.8,
            "high": 9.9,
            "low": 9.15,
            "close": 9.5,
            "volume": 100,
        },
    ]

    decision = evaluate_radar_candidate_lifecycle(
        long_cand,
        current_price=Decimal("9.50"),
        recent_candles_1h=candles,
        now=now,
    )

    assert decision.retained is False
    assert decision.state is RadarLifecycleState.INVALIDATED

    # Short candidate squeezing >6% above entry (like TAUSDT / LYNUSDT) is also eliminated
    short_cand = {
        "symbol": "LYNUSDT",
        "direction": "short",
        "suggested_entry": "0.02037",
        "suggested_stop": "0.02250",
        "suggested_targets": ["0.01800", "0.01650"],
        "evaluated_at": (now - timedelta(hours=12)).isoformat(),
    }
    short_decision = evaluate_radar_candidate_lifecycle(
        short_cand,
        current_price=Decimal("0.03017"),
        recent_candles_1h=[],
        now=now,
    )
    assert short_decision.retained is False
    assert short_decision.state is RadarLifecycleState.INVALIDATED


def test_wyckoff_spring_48h_memory_retains_coil_spring_archetype() -> None:
    features = LongArchetypeFeatures(
        close=Decimal("0.005398"),
        low_24h=Decimal("0.005344"),  # 24h low is above EMA50 because the shakeout wick was 30h ago
        low_48h=Decimal(
            "0.005120"
        ),  # 48h low captured the Wyckoff Spring shakeout wick below POC & EMA50
        ema50_4h=Decimal("0.005342"),
        poc_30d=Decimal("0.005213"),
        vah_30d=Decimal("0.005596"),
        atr14_4h=Decimal("0.00012"),
        vcr_4h=Decimal("0.82"),
        oi_delta_7d=Decimal("0.091"),
        oi_delta_4h=Decimal("0.0"),
        ret_7d=Decimal("0.02"),
        bb_inside_kc_4h=True,
        bb_bandwidth_4h=Decimal("0.077"),
        local_swing_high_4h=Decimal("0.005649"),
        rvol_4h=Decimal("0.95"),
        norm_momentum_1h=Decimal("0.1"),
        btc_residual_alpha_24h=Decimal("0.01"),
        macd_hist_4h=Decimal("-0.00001"),
        macd_hist_4h_prev=Decimal("0.0"),
        macd_hist_1h=Decimal("0.00001"),
        taker_buy_ratio_4h=Decimal("0.92"),
        val_30d=Decimal("0.005007"),
    )

    assert classify_long_archetype(features) is SetupLabel.COIL_SPRING_LONG


@pytest.mark.asyncio
async def test_reconcile_radar_cohort_retains_active_and_eliminates_stopped() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    home_inst = make_instrument(symbol="HOMEUSDT")
    lyn_inst = make_instrument(symbol="LYNUSDT")

    class FakeFutures:
        async def instruments(self) -> list[Any]:
            return [home_inst, lyn_inst]

        async def quotes(self, instruments: list[Any]) -> list[Quote]:
            return [
                Quote(
                    instrument_id=i.id,
                    bid=Decimal("1"),
                    ask=Decimal("1"),
                    last=Decimal("0.00539") if i.id.symbol == "HOMEUSDT" else Decimal("0.03017"),
                    quote_volume_24h=Decimal("10000000"),
                    observed_at=now,
                )
                for i in instruments
            ]

        async def candles(self, inst: Any, interval: str, limit: int) -> list[Candle]:
            del interval, limit
            if inst.id.symbol == "HOMEUSDT":
                return [
                    Candle(
                        opened_at=now - timedelta(hours=1),
                        closed_at=now,
                        open=Decimal("0.00536"),
                        high=Decimal("0.00541"),
                        low=Decimal("0.00535"),
                        close=Decimal("0.00539"),
                        volume=Decimal("10000"),
                    )
                ]
            return [
                Candle(
                    opened_at=now - timedelta(hours=1),
                    closed_at=now,
                    open=Decimal("0.025"),
                    high=Decimal("0.031"),
                    low=Decimal("0.024"),
                    close=Decimal("0.03017"),
                    volume=Decimal("10000"),
                )
            ]

    prior = [
        {
            "symbol": "HOMEUSDT",
            "direction": "long",
            "setup_label": "COIL_SPRING_LONG",
            "current_price": "0.005463",
            "suggested_entry": "0.005338",
            "breakout_trigger": "0.005649",
            "suggested_stop": "0.004933",
            "suggested_targets": ["0.005946", "0.006351", "0.006877"],
            "poc_price": "0.005203",
            "val_price": "0.005024",
            "evaluated_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "symbol": "LYNUSDT",
            "direction": "short",
            "setup_label": "UTAD_DIVERGENCE_SHORT",
            "current_price": "0.02037",
            "suggested_entry": "0.02037",
            "suggested_stop": "0.02250",
            "suggested_targets": ["0.01800", "0.01650"],
            "evaluated_at": (now - timedelta(hours=14)).isoformat(),
        },
    ]

    seen_bases: set[str] = set()
    retained = await reconcile_radar_cohort(prior, seen_bases, FakeFutures(), now=now)

    assert [r["symbol"] for r in retained] == ["HOMEUSDT"]
    assert retained[0]["current_price"] == "0.00539"
    assert retained[0]["radar_status"] == "AT_ENTRY_ZONE"
    assert retained[0]["gates_passed"] == 1
    assert any(c["status"] == "unknown" for c in retained[0]["checklist"])
    assert retained[0]["failed_gates"] == []


def test_retained_candidate_with_gate_drift_reports_failed_gates_for_dashboard() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    gmx_cand = {
        "symbol": "GMXUSDT",
        "direction": "long",
        "setup_label": "COIL_SPRING_LONG",
        "current_price": "8.440",
        "suggested_entry": "8.375",
        "breakout_trigger": "8.850",
        "suggested_stop": "7.672",
        "suggested_targets": ["9.450", "10.200", "11.500"],
        "poc_price": "8.440",
        "val_price": "7.980",
        "vah_price": "8.920",
        "checklist": [
            {"title": "Trend Baseline", "passed": True, "detail": "Holding above 4H EMA50 ($8.39)"},
            {
                "title": "Anti-Chasing Validated",
                "passed": True,
                "detail": "72h range tight (11.2%)",
            },
            {
                "title": "Value Acceptance",
                "passed": True,
                "detail": "Accepted above 30d POC ($8.44)",
            },
            {
                "title": "Volatility Compression",
                "passed": True,
                "detail": "Bandwidth 0.12 (BB inside KC Squeeze)",
            },
            {
                "title": "Smart Money Absorption",
                "passed": True,
                "detail": "7d OI +4.2%, funding +0.50 bps",
            },
        ],
        "evaluated_at": (now - timedelta(hours=12)).isoformat(),
    }

    decision = evaluate_radar_candidate_lifecycle(
        gmx_cand,
        current_price=Decimal("8.061"),
        ema50_4h=Decimal("8.390"),
        now=now,
    )

    assert decision.retained is True
    assert decision.state is RadarLifecycleState.ACTIVE_COIL
    assert decision.gates_passed == 0
    assert decision.gates_total == 5
    assert "Trend Baseline" in decision.failed_gates
    assert "Value Acceptance" in decision.failed_gates
    assert "0/5 GATES" in decision.label
    assert sum(c["status"] == "unknown" for c in decision.updated_checklist) == 3


@pytest.fixture
def lifecycle_case():
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    candidate = {
        "symbol": "TESTUSDT",
        "direction": "long",
        "suggested_entry": "100",
        "suggested_stop": "80",
        "breakout_trigger": "120",
        "suggested_targets": ["140", "160"],
        "evaluated_at": (now - timedelta(hours=1)).isoformat(),
    }
    return candidate, now


def test_missing_gate_data_is_unknown_not_five_passes():
    from algotrader.domain.radar_lifecycle import reverify_candidate_gates

    checks, passed, total, failed = reverify_candidate_gates({}, current_price=Decimal("100"))
    assert passed == 0 and total == 5 and failed == ()
    assert all(c["status"] == "unknown" for c in checks)


def test_frozen_metrics_and_checklist_do_not_certify_live_gates(lifecycle_case):
    from algotrader.domain.radar_lifecycle import reverify_candidate_gates

    c, _ = lifecycle_case
    c.update(
        ema50_4h="90", bb_bandwidth_4h="0.1", oi_change_7d_pct="10", volume_contraction_ratio="0.8"
    )
    checks, _, _, _ = reverify_candidate_gates(c, current_price=Decimal("100"))
    assert checks[0]["status"] == checks[3]["status"] == checks[4]["status"] == "unknown"


@pytest.mark.parametrize("hours", [72, 100])
def test_untriggered_entry_shelf_expires(lifecycle_case, hours):
    c, now = lifecycle_case
    c["evaluated_at"] = (now - timedelta(hours=hours)).isoformat()
    assert (
        evaluate_radar_candidate_lifecycle(c, current_price=Decimal("100"), now=now).state
        == RadarLifecycleState.EXPIRED
    )


@pytest.mark.parametrize(
    "direction,price,stop,trigger,targets",
    [
        ("long", "110", "80", "120", ["140", "160"]),
        ("short", "90", "110", "80", ["70", "60"]),
    ],
)
def test_confirmed_trigger_persists_past_ttl_without_recent_trigger_candles(
    lifecycle_case, direction, price, stop, trigger, targets
):
    c, now = lifecycle_case
    c.update(
        direction=direction,
        suggested_stop=stop,
        breakout_trigger=trigger,
        suggested_targets=targets,
        evaluated_at=(now - timedelta(hours=100)).isoformat(),
        radar_triggered_at=(now - timedelta(hours=90)).isoformat(),
    )
    d = evaluate_radar_candidate_lifecycle(c, current_price=Decimal(price), now=now)
    assert d.retained and d.state == RadarLifecycleState.IN_ACTIVE_TRADE
    assert d.triggered_at == c["radar_triggered_at"]


@pytest.mark.parametrize("offset", [8, 0.5])
def test_pre_discovery_and_overlapping_candles_cannot_trigger(lifecycle_case, offset):
    c, now = lifecycle_case
    candles = [
        {
            "time": int((now - timedelta(hours=offset)).timestamp()),
            "high": 130,
            "low": 90,
            "close": 110,
        }
    ]
    d = evaluate_radar_candidate_lifecycle(
        c, current_price=Decimal("110"), recent_candles_1h=candles, now=now
    )
    assert d.state == RadarLifecycleState.ACTIVE_COIL
    assert d.triggered_at is None


@pytest.mark.parametrize(
    "direction,price,stop,high,low,targets",
    [
        ("long", "110", "80", 165, 105, ["140", "160"]),
        ("short", "90", "110", 95, 55, ["70", "60"]),
    ],
)
def test_post_discovery_final_target_wick_completes(
    lifecycle_case, direction, price, stop, high, low, targets
):
    c, now = lifecycle_case
    c.update(
        direction=direction,
        suggested_stop=stop,
        suggested_targets=targets,
        evaluated_at=(now - timedelta(hours=3)).isoformat(),
    )
    candles = [{"time": int(now.timestamp()), "high": high, "low": low, "close": price}]
    assert (
        evaluate_radar_candidate_lifecycle(
            c, current_price=Decimal(price), recent_candles_1h=candles, now=now
        ).state
        == RadarLifecycleState.COMPLETED_TARGET
    )


def test_stop_has_conservative_priority_when_bar_also_touches_target(lifecycle_case):
    c, now = lifecycle_case
    c["evaluated_at"] = (now - timedelta(hours=3)).isoformat()
    d = evaluate_radar_candidate_lifecycle(
        c,
        current_price=Decimal("110"),
        now=now,
        recent_candles_1h=[{"time": int(now.timestamp()), "high": 165, "low": 75}],
    )
    assert d.reason == "long_stop_loss_breached"


def test_structural_breakdown_requires_both_references(lifecycle_case):
    c, now = lifecycle_case
    c["val_price"] = "115"
    d = evaluate_radar_candidate_lifecycle(c, current_price=Decimal("110"), now=now)
    assert d.retained


@pytest.mark.asyncio
async def test_provider_failure_retains_candidate_as_unknown_and_records_event(lifecycle_case):
    c, now = lifecycle_case

    class Broken:
        async def instruments(self):
            raise TimeoutError()

    events = []
    rows = await reconcile_radar_cohort([c], set(), Broken(), now, events)
    assert len(rows) == 1
    assert rows[0]["radar_data_status"] == "unavailable"
    assert rows[0]["gates_passed"] == 0
    assert all(x["status"] == "unknown" for x in rows[0]["checklist"])
    assert events[0]["retained"] is True


def test_requalification_timestamp_cannot_reset_original_age(lifecycle_case):
    c, now = lifecycle_case
    c["first_seen_at"] = (now - timedelta(hours=100)).isoformat()
    assert (
        evaluate_radar_candidate_lifecycle(c, current_price=Decimal("110"), now=now).state
        == RadarLifecycleState.EXPIRED
    )


@pytest.mark.parametrize("quote_age", [120, -120])
@pytest.mark.asyncio
async def test_stale_or_future_quotes_are_unknown(lifecycle_case, quote_age):
    c, now = lifecycle_case
    inst = make_instrument(symbol=c["symbol"])

    class Provider:
        async def instruments(self):
            return [inst]

        async def quotes(self, instruments):
            return [
                Quote(
                    instrument_id=inst.id,
                    bid=Decimal("100"),
                    ask=Decimal("100"),
                    last=Decimal("100"),
                    quote_volume_24h=Decimal("10000000"),
                    observed_at=now - timedelta(seconds=quote_age),
                )
            ]

        async def candles(self, inst, interval, limit):
            return [
                Candle(
                    opened_at=now - timedelta(hours=1),
                    closed_at=now,
                    open=Decimal("100"),
                    high=Decimal("105"),
                    low=Decimal("95"),
                    close=Decimal("100"),
                    volume=Decimal("100"),
                )
            ]

    rows = await reconcile_radar_cohort([c], set(), Provider(), now)
    assert rows[0]["radar_data_status"] == "unavailable"
    assert "current_price" not in rows[0]


def test_score_and_bracket_refresh_do_not_create_a_new_episode(lifecycle_case):
    from algotrader.domain.radar_lifecycle import same_radar_episode

    c, _ = lifecycle_case
    assert same_radar_episode(c, dict(c, suggested_stop="85", score="99"))
    assert not same_radar_episode(c, dict(c, direction="short"))


@pytest.mark.asyncio
async def test_live_quote_stop_is_used_instead_of_last_closed_candle(lifecycle_case):
    c, now = lifecycle_case
    inst = make_instrument(symbol=c["symbol"])

    class Provider:
        async def instruments(self):
            return [inst]

        async def quotes(self, instruments):
            return [
                Quote(
                    instrument_id=inst.id,
                    bid=Decimal("75"),
                    ask=Decimal("75"),
                    last=Decimal("75"),
                    quote_volume_24h=Decimal("10000000"),
                    observed_at=now,
                )
            ]

        async def candles(self, inst, interval, limit):
            raise TimeoutError("Candle outage must not conceal a fresh quote stop")

    events = []
    assert await reconcile_radar_cohort([c], set(), Provider(), now, events) == []
    assert events[0]["reason"] == "long_stop_loss_breached"
    assert events[0]["candidate"]["suggested_stop"] == "80"


@pytest.mark.asyncio
async def test_scan_reconciliation_archives_removals_without_resetting_episode(
    lifecycle_case, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from algotrader.api import portal

    c, now = lifecycle_case
    c["setup_label"] = "TEST_LONG"
    # A fresh score and moved bracket must not silently revive the existing episode.
    fresh = dict(c, evaluated_at=now.isoformat(), suggested_stop="70", score="99")

    fresh["checklist"] = [{"title": str(i), "passed": True, "detail": "test"} for i in range(5)]

    class Opportunity:
        checklist = tuple((str(i), True, "test") for i in range(5))
        instrument = make_instrument(symbol=c["symbol"])
        macro_narrative = "test"

        def as_dict(self):
            return dict(fresh, candles=[{"time": int(now.timestamp())}])

    class Runtime:
        settings = object()
        futures_market = object()

        async def run_crypto_scan(self, **kwargs):
            return SimpleNamespace(
                opportunities=[Opportunity()],
                counts={"evaluated": 1},
                receipt=SimpleNamespace(scan_id="test-scan", as_dict=lambda: {}),
            )

    async def runtime():
        return Runtime()

    async def reconcile(**kwargs):
        assert kwargs["prior_opportunities"][0]["evaluated_at"] == c["evaluated_at"]
        kwargs["events"].append(
            {"symbol": c["symbol"], "retained": False, "reason": "long_stop_loss_breached"}
        )
        return []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portal, "_screener_cache", {"opportunities": [c]})
    monkeypatch.setattr(portal, "_cache_timestamp", None)
    monkeypatch.setattr(portal, "get_portal_runtime", runtime)
    monkeypatch.setattr(portal, "reconcile_radar_cohort", reconcile)
    result = await portal._do_crypto_screen()
    assert result["opportunities"] == []
    assert result["radar_audit_status"] == "persisted"
    import json

    files = list((tmp_path / "data/radar_events").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["events"][0]["retained"] is False


@pytest.mark.asyncio
async def test_refresh_uses_real_four_hour_metrics_and_complete_volume_window(lifecycle_case):
    from types import SimpleNamespace

    c, now = lifecycle_case
    c.update(poc_price="100", ema50_4h="999", bb_bandwidth_4h="99")
    inst = make_instrument(symbol=c["symbol"])

    class Provider:
        async def instruments(self):
            return [inst]

        async def quotes(self, instruments):
            return [
                Quote(
                    instrument_id=inst.id,
                    bid=Decimal("100"),
                    ask=Decimal("100"),
                    last=Decimal("100"),
                    quote_volume_24h=Decimal("10000000"),
                    observed_at=now,
                )
            ]

        async def candles(self, inst, interval, limit):
            hours, count = (1, 338) if interval == "1h" else (4, 200)
            return [
                Candle(
                    opened_at=now - timedelta(hours=hours * (count - i)),
                    closed_at=now - timedelta(hours=hours * (count - i - 1)),
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("100"),
                )
                for i in range(count)
            ]

        async def derivatives_snapshot(self, inst):
            return SimpleNamespace(observed_at=now, open_interest_change_7d=Decimal("0.1"))

    rows = await reconcile_radar_cohort([c], set(), Provider(), now)
    assert rows[0]["gates_passed"] == 5
    assert rows[0]["radar_data_status"] == "current"
    assert Decimal(rows[0]["radar_metrics"]["bb_bandwidth_4h"]) == 0


def test_missing_discovery_timestamp_does_not_hide_confirmed_stop(lifecycle_case):
    c, now = lifecycle_case
    c.pop("evaluated_at")
    assert (
        evaluate_radar_candidate_lifecycle(c, current_price=Decimal("75"), now=now).reason
        == "long_stop_loss_breached"
    )


def test_breakout_after_untriggered_expiry_does_not_revive_setup(lifecycle_case):
    c, now = lifecycle_case
    c["evaluated_at"] = (now - timedelta(hours=100)).isoformat()
    assert (
        evaluate_radar_candidate_lifecycle(c, current_price=Decimal("125"), now=now).state
        == RadarLifecycleState.EXPIRED
    )


@pytest.mark.parametrize("passes,expected", [(4, 0), (5, 1)])
@pytest.mark.asyncio
async def test_fresh_radar_admission_requires_five_displayed_gates(
    lifecycle_case, monkeypatch, tmp_path, passes, expected
):
    from types import SimpleNamespace

    from algotrader.api import portal

    c, _ = lifecycle_case

    class Opportunity:
        instrument = make_instrument(symbol=c["symbol"])
        macro_narrative = "test"
        checklist = tuple((str(i), i < passes, "test") for i in range(5))

        def as_dict(self):
            return dict(
                c,
                checklist=[
                    {"title": title, "passed": ok, "detail": detail}
                    for title, ok, detail in self.checklist
                ],
                candles=[{}],
            )

    class Runtime:
        settings = object()
        futures_market = object()

        async def run_crypto_scan(self, **kwargs):
            return SimpleNamespace(
                opportunities=[Opportunity()],
                counts={"evaluated": 1},
                receipt=SimpleNamespace(scan_id="test-scan", as_dict=lambda: {}),
            )

    async def runtime():
        return Runtime()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portal, "_screener_cache", {})
    monkeypatch.setattr(portal, "_cache_timestamp", None)
    monkeypatch.setattr(portal, "get_portal_runtime", runtime)
    result = await portal._do_crypto_screen()
    assert len(result["opportunities"]) == expected
    assert result["fresh_count"] == expected


def test_final_target_after_expiry_is_not_a_retrospective_winner(lifecycle_case):
    c, now = lifecycle_case
    c["evaluated_at"] = (now - timedelta(hours=100)).isoformat()
    assert (
        evaluate_radar_candidate_lifecycle(c, current_price=Decimal("170"), now=now).state
        == RadarLifecycleState.EXPIRED
    )


def test_breakout_without_retest_computes_pnl_from_breakout_trigger() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "BRKUSDT",
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "current_price": "100.00",
        "suggested_entry": "95.00",
        "breakout_trigger": "102.00",
        "suggested_stop": "90.00",
        "suggested_targets": ["110.00", "118.00", "126.00"],
        "poc_price": "96.00",
        "val_price": "92.00",
        "vah_price": "101.00",
        "evaluated_at": (now - timedelta(hours=4)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=1)).timestamp()),
            "open": 100.5,
            "high": 105.0,
            "low": 99.5,  # Never touched 95.00 retest entry
            "close": 104.04,
            "volume": 5000,
        },
    ]
    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("104.04"),
        recent_candles_1h=candles,
        now=now,
    )
    assert decision.retained is True
    assert decision.state is RadarLifecycleState.IN_ACTIVE_TRADE
    # (104.04 - 102.00) / 102.00 * 100 = 2.0% (not ~9.5% from unfilled 95.00 retest_entry)
    assert decision.pnl_from_entry_pct == Decimal("2.0")


def test_wait_for_retest_only_ignores_breakout_touch() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "RETUSDT",
        "direction": "long",
        "execution_mode": "WAIT_FOR_RETEST_ONLY",
        "suggested_entry": "100.00",
        "breakout_trigger": "104.00",
        "suggested_stop": "90.00",
        "suggested_targets": ["112.00", "120.00", "130.00"],
        "poc_price": "99.00",
        "val_price": "95.00",
        "vah_price": "103.00",
        "evaluated_at": (now - timedelta(hours=4)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=1)).timestamp()),
            "open": 101.0,
            "high": 106.0,
            "low": 101.0,
            "close": 105.0,
            "volume": 5000,
        },
    ]
    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("105.0"),
        recent_candles_1h=candles,
        now=now,
    )
    assert decision.triggered_at is None
    assert decision.state is not RadarLifecycleState.IN_ACTIVE_TRADE


def test_breakout_episode_does_not_switch_return_basis_on_later_retest() -> None:
    now = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
    candidate = {
        "symbol": "LOCKUSDT",
        "direction": "long",
        "execution_mode": "RETEST_ACCUMULATION",
        "suggested_entry": "100.00",
        "breakout_trigger": "104.00",
        "suggested_stop": "90.00",
        "suggested_targets": ["112.00", "120.00", "130.00"],
        "poc_price": "99.00",
        "val_price": "95.00",
        "vah_price": "103.00",
        "evaluated_at": (now - timedelta(hours=4)).isoformat(),
    }
    candles = [
        {
            "time": int((now - timedelta(hours=2)).timestamp()),
            "open": 101.0,
            "high": 106.0,
            "low": 101.0,
            "close": 105.0,
            "volume": 5000,
        },
        {
            "time": int((now - timedelta(hours=1)).timestamp()),
            "open": 103.0,
            "high": 103.5,
            "low": 99.0,
            "close": 103.0,
            "volume": 4000,
        },
    ]
    decision = evaluate_radar_candidate_lifecycle(
        candidate,
        current_price=Decimal("103.0"),
        recent_candles_1h=candles,
        now=now,
    )
    assert decision.state is RadarLifecycleState.IN_ACTIVE_TRADE
    # Locked to breakout_trigger (104.00): (103.0 - 104.0) / 104.0 = -1.0%, NOT +3.0% from 100.00
    assert decision.pnl_from_entry_pct == Decimal("-1.0")
