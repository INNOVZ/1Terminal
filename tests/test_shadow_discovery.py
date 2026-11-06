from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel
from algotrader.domain.models import MarketKind, Quote, Venue
from algotrader.domain.scan_observations import RecordedCall, ScanRecord
from algotrader.domain.shadow_discovery import (
    ShadowPolicy,
    admit_shadow,
    classify_shadow,
    setup_family,
)
from tests.conftest import make_instrument

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def parent_fixture(count=20):
    cohort = tuple(
        make_instrument(
            symbol=f"T{i}USDT", venue=Venue.BINANCE_USDM, market_kind=MarketKind.FUTURES
        )
        for i in range(count)
    )
    quotes = tuple(
        Quote(
            inst.id,
            Decimal("99.9"),
            Decimal("100.1"),
            Decimal("100"),
            Decimal("1500000"),
            NOW,
            price_change_pct_24h=Decimal(i),
            funding_rate=Decimal("-0.001"),
            basis_pct=Decimal("0.01"),
        )
        for i, inst in enumerate(cohort)
    )
    return ScanRecord(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        NOW - timedelta(seconds=20),
        NOW,
        "complete",
        {},
        {"mode": ScreenerMode.ALL, "enforce_quality": True},
        (RecordedCall("quotes", 0, {}, quotes),),
        ({"symbol": "T0USDT", "stage": "selection", "reason": "selected"},),
        ({"symbol": "T1USDT"},),
        {},
        cohort,
    )


def ticker_fixture(parent):
    return [
        {
            "symbol": inst.id.symbol,
            "lastPrice": "100",
            "priceChangePercent": str(i),
            "closeTime": int(NOW.timestamp() * 1000),
        }
        for i, inst in enumerate(parent.cohort)
    ]


def test_admission_reaches_outside_selected_and_preserves_parent():
    parent = parent_fixture()
    result = admit_shadow(parent, ShadowPolicy(), NOW)
    assert len(result.selected) == 15
    assert len(result.decisions) == 20
    assert result.selected[0].instrument.id.symbol == "T10USDT"  # lexical anomaly tie-break
    assert result.selected[0].bucket == "quote_anomaly"
    assert result.selected[1].instrument.id.symbol == "T19USDT"
    assert len({item.instrument.base_asset for item in result.selected}) == 15
    assert not {"T0USDT", "T1USDT"} & {item.instrument.id.symbol for item in result.selected}
    assert parent.opportunities == ({"symbol": "T1USDT"},)
    assert result == admit_shadow(parent, ShadowPolicy(), NOW)
    small = admit_shadow(parent, ShadowPolicy(max_tokens=2), NOW)
    assert [item.bucket for item in small.selected] == ["quote_anomaly", "momentum_control"]


@pytest.mark.parametrize(
    "volume,bid,ask,accepted",
    [
        ("1499999", "99.9", "100.1", False),
        ("1500000", "99.875", "100.125", True),
        ("1500000", "99.874", "100.126", False),
        ("NaN", "99", "101", False),
    ],
)
def test_admission_hygiene(volume, bid, ask, accepted):
    parent = parent_fixture(1)
    quote = replace(
        parent.inputs[0].result[0],
        quote_volume_24h=Decimal(volume),
        bid=Decimal(bid),
        ask=Decimal(ask),
    )
    parent = replace(parent, decisions=(), inputs=(replace(parent.inputs[0], result=(quote,)),))
    assert bool(admit_shadow(parent, ShadowPolicy(), NOW).selected) is accepted


@pytest.mark.parametrize(
    "changes,now",
    [
        ({}, NOW + timedelta(seconds=301)),
        ({}, NOW - timedelta(seconds=1)),
        ({"status": "incomplete"}, NOW),
        ({"status": "failed"}, NOW),
        ({"request": {"mode": ScreenerMode.BREAKOUT}}, NOW),
        ({"request": {"mode": ScreenerMode.ALL, "enforce_quality": False}}, NOW),
    ],
)
def test_parent_admission_rejects_unsafe_experiments(changes, now):
    with pytest.raises(ValueError):
        admit_shadow(replace(parent_fixture(), **changes), ShadowPolicy(), now)


def test_same_snapshot_rank_and_trigger_not_parent_return_define_early():
    parent = parent_fixture()
    candidates = [{"symbol": "T0USDT", "direction": "long", "breakout_trigger": "101"}]
    result = classify_shadow(parent.cohort, candidates, ticker_fixture(parent), NOW, ShadowPolicy())
    assert result.labels[0]["rank"] == 20
    assert result.labels[0]["early_candidate"] is True
    rows = ticker_fixture(parent)
    rows[0]["priceChangePercent"] = "30"
    result = classify_shadow(parent.cohort, candidates, rows, NOW, ShadowPolicy())
    assert result.labels[0]["decision_gainer_status"] == "within_top_10"
    assert result.labels[0]["early_candidate"] is False
    rows[0]["lastPrice"] = "102"
    assert (
        classify_shadow(parent.cohort, candidates, rows, NOW, ShadowPolicy()).labels[0][
            "price_phase"
        ]
        == "at_or_above_trigger"
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "nan", "malformed", "price", "old", "future", "span"]
)
def test_invalid_cohort_cannot_earn_early_label(mutation):
    parent = parent_fixture()
    rows = ticker_fixture(parent)
    if mutation == "missing":
        rows.pop()
    if mutation == "duplicate":
        rows.append(dict(rows[-1]))
    if mutation == "nan":
        rows[-1]["priceChangePercent"] = "NaN"
    if mutation == "malformed":
        rows[-1]["priceChangePercent"] = None
    if mutation == "price":
        rows[-1]["lastPrice"] = "0"
    if mutation == "old":
        rows[-1]["closeTime"] -= 61000
    if mutation == "future":
        rows[-1]["closeTime"] += 6000
    if mutation == "span":
        rows[0]["closeTime"] -= 60000
        rows[-1]["closeTime"] += 5000
    candidate = {"symbol": "T0USDT", "direction": "long", "breakout_trigger": "101"}
    label = classify_shadow(parent.cohort, [candidate], rows, NOW, ShadowPolicy()).labels[0]
    assert label["decision_gainer_status"] == "unknown"
    assert label["early_candidate"] is False


def test_cutoff_ties_and_small_cohort_are_not_outside_top_ten():
    parent = parent_fixture(12)
    rows = ticker_fixture(parent)
    rows[0]["priceChangePercent"] = "2"
    rows[1]["priceChangePercent"] = "2"
    candidates = [{"symbol": "T0USDT", "direction": "long", "breakout_trigger": "101"}]
    label = classify_shadow(parent.cohort, candidates, rows, NOW, ShadowPolicy()).labels[0]
    assert label["rank"] == 10
    assert label["early_candidate"] is False
    assert (
        classify_shadow(parent.cohort[:3], candidates, rows[:3], NOW, ShadowPolicy()).labels[0][
            "rank"
        ]
        == 1
    )


def test_setup_inventory_keeps_short_and_funding_families():
    for name, member in SetupLabel.__members__.items():
        assert setup_family(name) == setup_family(member.value)
    assert setup_family("FUNDING_SQUEEZE_SPRING_LONG") == "positioning_squeeze"
    for label in [
        "UTAD_DIVERGENCE_SHORT",
        "EXHAUSTION_REVERSAL_SHORT",
        "DISTRIBUTION_SHORT",
        "VCP_DISTRIBUTION_SHORT",
    ]:
        assert setup_family(label) == "distribution_reversal_short"
    assert setup_family("COIL_BREAKDOWN_SHORT") == "legacy_compatibility"
    with pytest.raises(ValueError):
        setup_family("NEW_UNMAPPED_LABEL")


def test_parent_id_and_diagnostic_score_floor_are_rejected():
    parent = parent_fixture()
    with pytest.raises(ValueError):
        admit_shadow(replace(parent, scan_id="../unsafe"), ShadowPolicy(), NOW)
    with pytest.raises(ValueError):
        admit_shadow(
            replace(parent, request={**parent.request, "min_score": Decimal("40")}),
            ShadowPolicy(),
            NOW,
        )
