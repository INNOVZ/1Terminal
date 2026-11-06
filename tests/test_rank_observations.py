from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from algotrader.domain.rank_observations import RankPolicy, normalize_ranks
from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture


def test_competition_rank_and_whole_cohort_unknown():
    parent = parent_fixture(12)
    rows = ticker_fixture(parent)
    rows[0]["priceChangePercent"] = rows[1]["priceChangePercent"] = "2"
    result = normalize_ranks(parent.cohort, tuple(rows), NOW, RankPolicy())
    assert result["complete"] is True
    assert result["members"][0]["rank"] == result["members"][1]["rank"] == 10
    assert result["members"][0]["return_24h"] == Decimal("2")
    assert result["members"][0]["inside"] is True
    missing = normalize_ranks(parent.cohort, tuple(rows[:-1]), NOW, RankPolicy())
    assert missing["complete"] is False
    assert missing["coverage"]["expected"] == 12
    assert all(member["rank"] is None for member in missing["members"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("priceChangePercent", "NaN"),
        ("priceChangePercent", "Infinity"),
        ("priceChangePercent", True),
        ("lastPrice", "0"),
        ("closeTime", None),
    ],
)
def test_invalid_row_keeps_cohort_unknown(field, value):
    parent = parent_fixture()
    rows = ticker_fixture(parent)
    rows[-1][field] = value
    assert normalize_ranks(parent.cohort, tuple(rows), NOW, RankPolicy())["complete"] is False


@pytest.mark.parametrize(
    "offset,complete", [(-60000, True), (-60001, False), (5000, True), (5001, False)]
)
def test_exact_freshness_boundaries(offset, complete):
    parent = parent_fixture(1)
    rows = ticker_fixture(parent)
    rows[0]["closeTime"] += offset
    result = normalize_ranks(parent.cohort, tuple(rows), NOW, RankPolicy())
    assert result["complete"] is complete


@pytest.mark.parametrize("span,complete", [(60000, True), (60001, False)])
def test_provider_span_boundary(span, complete):
    parent = parent_fixture(2)
    rows = ticker_fixture(parent)
    rows[0]["closeTime"] -= 55000
    rows[1]["closeTime"] = rows[0]["closeTime"] + span
    assert (
        normalize_ranks(parent.cohort, tuple(rows), NOW + timedelta(milliseconds=1), RankPolicy())[
            "complete"
        ]
        is complete
    )


def test_duplicates_negative_returns_and_canonical_cohort():
    parent = parent_fixture(2)
    rows = ticker_fixture(parent)
    rows[0]["priceChangePercent"] = "-2"
    rows[1]["priceChangePercent"] = "-1"
    result = normalize_ranks(parent.cohort, tuple(rows), NOW, RankPolicy())
    assert result["members"][0]["rank"] == 2
    assert result["members"][0]["return_sign"] == "negative"
    assert result["members"][0]["quote_volume"] is None
    assert not normalize_ranks(parent.cohort, tuple(rows + [rows[0]]), NOW, RankPolicy())[
        "complete"
    ]
    for cohort in [
        (),
        (parent.cohort[0], parent.cohort[0]),
        (parent.cohort[0], replace(parent.cohort[1], base_asset=parent.cohort[0].base_asset)),
    ]:
        with pytest.raises(ValueError):
            normalize_ranks(cohort, tuple(rows), NOW, RankPolicy())
    with pytest.raises(ValueError):
        normalize_ranks(parent.cohort, tuple(rows), NOW.replace(tzinfo=None), RankPolicy())


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 4}, {"deadline_seconds": 16}, {"max_cohort": 1001}, {"max_attempts": True}],
)
def test_rank_policy_cannot_expand_safety_caps(kwargs):
    with pytest.raises(ValueError):
        RankPolicy(**kwargs)


def test_full_cohort_classification_does_not_compare_every_pair(monkeypatch):
    from decimal import Decimal

    from algotrader.domain import shadow_discovery
    from tests.test_shadow_discovery import NOW, parent_fixture, ticker_fixture

    comparisons = [0]
    lookups = [0]
    original = shadow_discovery._number

    class CountedDecimal(Decimal):
        def __gt__(self, other):
            comparisons[0] += 1
            return super().__gt__(other)

    class CountedRow(dict):
        def get(self, key, default=None):
            if key == "symbol":
                lookups[0] += 1
            return super().get(key, default)

    def observed_number(value):
        number = original(value)
        return CountedDecimal(number) if number is not None else None

    monkeypatch.setattr(shadow_discovery, "_number", observed_number)
    parent = parent_fixture(200)
    normalized = normalize_ranks(
        parent.cohort, tuple(CountedRow(row) for row in ticker_fixture(parent)), NOW, RankPolicy()
    )
    assert normalized["complete"] is True
    assert normalized["members"][0]["rank"] == 200
    assert normalized["members"][-1]["rank"] == 1
    assert comparisons[0] < 200 * 20
    assert lookups[0] < 200 * 10
