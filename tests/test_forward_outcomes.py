from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from algotrader.domain.forward_outcomes import (
    diagnose_touches,
    evaluate_forward_path,
    outcome_anchor,
    rank_cohort,
)
from algotrader.domain.models import Candle, Direction

ANCHOR = datetime(2026, 10, 24, 12, tzinfo=UTC)


def minute_path(hours: int = 24) -> list[Candle]:
    bars = []
    for i in range(hours * 60):
        opened = ANCHOR + timedelta(minutes=i)
        bars.append(Candle(opened, opened + timedelta(minutes=1), Decimal("100"), Decimal("112"), Decimal("95"), Decimal("110"), Decimal("10")))
    return bars


def evaluate(bars: list[Candle], hours: int = 24, **kwargs: object):
    return evaluate_forward_path(anchor=ANCHOR, horizon_hours=hours, candles=bars,
        now=ANCHOR + timedelta(hours=hours), **kwargs)


def test_anchor_does_not_include_any_pre_publication_minute() -> None:
    assert outcome_anchor(ANCHOR) == ANCHOR
    assert outcome_anchor(ANCHOR + timedelta(microseconds=1)) == ANCHOR + timedelta(minutes=1)
    with pytest.raises(ValueError):
        outcome_anchor(ANCHOR.replace(tzinfo=None))


@pytest.mark.parametrize("hours", [24, 48])
def test_exact_horizon_has_independently_measured_return_and_excursions(hours: int) -> None:
    observation = evaluate(minute_path(hours), hours, direction=Direction.SHORT)
    assert observation.status == "complete"
    assert observation.baseline == Decimal("100")
    assert observation.end_price == Decimal("110")
    assert observation.raw_return_pct == Decimal("10")
    assert observation.directional_return_pct == Decimal("-10")
    assert observation.high_excursion_pct == Decimal("12")
    assert observation.low_excursion_pct == Decimal("-5")


def test_not_due_and_missing_bars_never_become_zero_returns() -> None:
    bars = minute_path()
    early = evaluate_forward_path(anchor=ANCHOR, horizon_hours=24, candles=bars, now=ANCHOR + timedelta(hours=23))
    assert early.status == "not_due"
    assert early.raw_return_pct is None
    for incomplete in (bars[1:], bars[:-1], bars[:20] + bars[21:], bars[:-1] + [replace(bars[-1], closed=False)]):
        result = evaluate(incomplete)
        assert result.status == "missing"
        assert result.raw_return_pct is None


@pytest.mark.parametrize("change", [
    {"open": Decimal("0")}, {"high": Decimal("90")}, {"low": Decimal("NaN")},
    {"close": Decimal("Infinity")}, {"volume": Decimal("-1")},
    {"closed_at": ANCHOR + timedelta(seconds=30)},
])
def test_invalid_ohlc_is_explicit_instead_of_ranked(change: dict[str, object]) -> None:
    bars = minute_path()
    bars[0] = replace(bars[0], **change)
    result = evaluate(bars)
    assert result.status == "invalid"
    assert result.raw_return_pct is None


def test_duplicates_are_invalid_but_outside_window_bars_are_excluded() -> None:
    bars = minute_path()
    assert evaluate(bars + [bars[0]]).status == "invalid"
    preceding = replace(bars[0], opened_at=ANCHOR - timedelta(minutes=1), closed_at=ANCHOR)
    assert evaluate([preceding, *bars]).raw_return_pct == Decimal("10")
    exchange_close = [replace(bar, closed_at=bar.closed_at - timedelta(milliseconds=1)) for bar in bars]
    assert evaluate(exchange_close).status == "complete"


def test_ranked_cohort_reports_ties_and_missing_coverage_not_false_full_recall() -> None:
    complete = evaluate(minute_path())
    missing = evaluate([])
    summary = rank_cohort({"AAA": complete, "BBB": complete, "DELISTED": missing})
    assert summary.ranks == {"AAA": 1, "BBB": 1}
    assert summary.observed == 2
    assert summary.total == 3
    assert summary.full_coverage is False
    assert summary.missing == {"DELISTED": "missing"}


def test_stop_target_or_entry_order_within_a_bar_is_ambiguous_not_a_trade_win() -> None:
    touches = diagnose_touches(minute_path()[:1], direction=Direction.LONG, entry=Decimal("100"),
        stop=Decimal("96"), targets=(Decimal("111"), Decimal("120")))
    assert touches["entry_touched"] is True
    assert touches["stop_touched"] is True
    assert touches["targets_touched"] == (True, False)
    assert touches["ambiguous_order"] is True
    assert "pnl" not in touches
