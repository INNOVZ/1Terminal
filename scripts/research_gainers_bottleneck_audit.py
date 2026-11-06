"""Offline, fixed-window descriptive audit; not production replay or a predictive backtest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

HOUR = 3_600_000
CUTOFF = int(datetime(2026, 11, 5, 9, tzinfo=UTC).timestamp() * 1000)
DAY_START = int(datetime(2026, 11, 3, 23, tzinfo=UTC).timestamp() * 1000)


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def number(value):
    result = Decimal(str(value))
    assert result.is_finite()
    return result


def change(last, first):
    return (number(last) / number(first) - 1) * 100 if number(first) > 0 else None


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence
    market = read(root / "2026-11-05-market-input.json")
    universe = sorted(
        s["symbol"]
        for s in market["exchange"]["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
    )
    datasets = {}
    for kind in ("hourly", "oi"):
        data = {}
        for path in sorted(root.glob(f"2026-11-05-universe-{kind}-*.json")):
            part = read(path)["results"]
            assert not data.keys() & part.keys(), "duplicate symbol across input chunks"
            data.update(part)
        assert set(data) == set(universe), "missing/extra cohort symbols"
        datasets[kind] = data
    candles, oi, oi_gaps = {}, {}, {}
    for symbol in universe:
        bars = datasets["hourly"][symbol]["result"]
        assert len(bars) == 120
        expected = list(range(CUTOFF - 120 * HOUR, CUTOFF, HOUR))
        assert [b[0] for b in bars] == expected
        for bar in bars:
            assert bar[6] == bar[0] + HOUR - 1 and bar[6] < CUTOFF
            op, hi, lo, cl, vol, quote, buy = map(number, [bar[i] for i in (1, 2, 3, 4, 5, 7, 10)])
            assert 0 < lo <= min(op, cl) <= max(op, cl) <= hi
            assert vol >= 0 and 0 <= buy <= quote
        candles[symbol] = {b[0]: b for b in bars}
        samples = datasets["oi"][symbol]["result"]
        assert len(samples) == 120
        times = [int(x["timestamp"]) for x in samples]
        assert times == sorted(set(times)) and times[-1] == CUTOFF
        assert all(t % HOUR == 0 and CUTOFF - 120 * HOUR <= t <= CUTOFF for t in times)
        gaps = [stamp(t) for t in range(times[0], CUTOFF + 1, HOUR) if t not in times]
        if gaps:
            oi_gaps[symbol] = gaps
        assert all(x["symbol"] == symbol and number(x["sumOpenInterest"]) > 0 for x in samples)
        oi[symbol] = {int(x["timestamp"]): number(x["sumOpenInterest"]) for x in samples}

    def window(symbol, end, hours):
        return [candles[symbol][t] for t in range(end - hours * HOUR, end, HOUR)]

    def features(symbol, at):
        bars = window(symbol, at, 24)
        recent, earlier = bars[-6:], bars[:-6]
        q6, q18 = sum(number(b[7]) for b in recent), sum(number(b[7]) for b in earlier)
        # One full hour of OI lag; timestamps alone do not prove API arrival availability.
        latest = at - HOUR
        base = latest - 6 * HOUR
        return {
            "as_of": stamp(at),
            "pre1h_return_pct": change(bars[-1][4], bars[-1][1]),
            "pre6h_return_pct": change(recent[-1][4], recent[0][1]),
            "pre24h_return_pct": change(bars[-1][4], bars[0][1]),
            "pre6h_quote_volume_vs_prior18h": (q6 / 6) / (q18 / 18) if q18 else None,
            "pre24h_quote_volume": q6 + q18,
            "lagged_6h_oi_units_change_pct": change(oi[symbol][latest], oi[symbol][base])
            if latest in oi[symbol] and base in oi[symbol]
            else None,
            "oi_latest_event_time": stamp(latest),
            "oi_lag_hours": 1,
        }

    def returns(start, end):
        result = []
        for symbol in universe:
            bars = [candles[symbol][t] for t in range(start, end, HOUR)]
            result.append({"symbol": symbol, "return_pct": change(bars[-1][4], bars[0][1])})
        return sorted(result, key=lambda r: (-r["return_pct"], r["symbol"]))

    tickers = [t for t in market["tickers"] if t["symbol"] in set(universe)]
    newest = max(t["closeTime"] for t in tickers)
    stale = [t["symbol"] for t in tickers if newest - t["closeTime"] > 60_000]
    fresh = sorted(
        (t for t in tickers if t["symbol"] not in stale),
        key=lambda t: (-number(t["priceChangePercent"]), t["symbol"]),
    )
    current = [
        {
            "observed_rank": i,
            "rank_lower": i,
            "rank_upper": i + len(stale),
            "certified_top10": i + len(stale) <= 10,
            "symbol": t["symbol"],
            "change_pct": number(t["priceChangePercent"]),
            "quote_volume": number(t["quoteVolume"]),
        }
        for i, t in enumerate(fresh[:10], 1)
    ]
    yesterday = returns(DAY_START, DAY_START + 24 * HOUR)
    proxy = returns(CUTOFF - 24 * HOUR, CUTOFF)

    # Hourly close-to-close rolling-return ranking; retains all members at every boundary.
    timeline = []
    begin = int(datetime(2026, 11, 4, 0, tzinfo=UTC).timestamp() * 1000)
    for at in range(begin, CUTOFF + 1, HOUR):
        ranked = returns(at - 24 * HOUR, at)
        timeline.append((at, {r["symbol"] for r in ranked[:10]}))

    def entry(symbol):
        seen_outside = False
        for at, leaders in timeline:
            if symbol in leaders:
                return {
                    "first_observed_top10_boundary": stamp(at),
                    "left_censored": not seen_outside,
                    "features_one_hour_before_boundary": features(symbol, at - HOUR),
                }
            seen_outside = True
        return None

    def event(symbol, start, end):
        bars = [candles[symbol][t] for t in range(start, end, HOUR)]
        strongest = max(bars, key=lambda b: (number(b[4]) / number(b[1]), -b[0]))
        first = None
        # Frozen descriptive transition definition; not the production breakout rule.
        for bar in bars:
            prior = window(symbol, bar[0], 24)
            prior20 = prior[-20:]
            ratio = number(bar[7]) / (sum(number(b[7]) for b in prior20) / 20)
            if (
                number(bar[4]) > max(number(b[2]) for b in prior)
                and change(bar[4], bar[1]) >= 3
                and ratio >= 2
            ):
                first = {
                    "hour_open": stamp(bar[0]),
                    "confirmed_close": stamp(bar[6]),
                    "hour_return_pct": change(bar[4], bar[1]),
                    "quote_volume_vs_previous20": ratio,
                    "price_change_from_window_start_pct": change(bar[4], bars[0][1]),
                }
                break
        return {
            "symbol": symbol,
            "strongest_hour_open": stamp(strongest[0]),
            "strongest_hour_return_pct": change(strongest[4], strongest[1]),
            "price_change_before_strongest_hour_pct": change(strongest[1], bars[0][1]),
            "before_strongest_hour": features(symbol, strongest[0]),
            "first_descriptive_breakout": first,
            "hourly_rank_entry": entry(symbol),
        }

    scans = {}
    for day in ("04", "05"):
        scan = read(root / f"2026-11-{day}-local-screener.json")
        longs = sorted(o["symbol"] for o in scan["opportunities"] if o["direction"] == "long")
        scans[day] = {
            "timestamp": scan["timestamp"],
            "evaluated": scan.get("total_scanned"),
            "longs": longs,
            "scan_id": scan.get("scan_id"),
            "archive_receipt": scan.get("scan_archive"),
            "current_top10_overlap": sorted(set(longs) & {r["symbol"] for r in current}),
            "yesterday_top10_overlap": sorted(set(longs) & {r["symbol"] for r in yesterday[:10]}),
        }

    references = []
    for label, start in [
        ("yesterday_calendar_day", DAY_START),
        ("current_closed_hour_proxy", CUTOFF - 24 * HOUR),
    ]:
        outcome = returns(start, start + 24 * HOUR)
        winners = {r["symbol"] for r in outcome[:10]}
        rows = {s: features(s, start) for s in universe}
        volume_rank = {
            r["symbol"]: i
            for i, r in enumerate(
                sorted(
                    ({"symbol": s, "volume": rows[s]["pre24h_quote_volume"]} for s in universe),
                    key=lambda r: (-r["volume"], r["symbol"]),
                ),
                1,
            )
        }
        comparisons = []
        for field in [
            "pre24h_return_pct",
            "pre6h_quote_volume_vs_prior18h",
            "lagged_6h_oi_units_change_pct",
        ]:
            ordered = sorted(
                (s for s in universe if rows[s][field] is not None),
                key=lambda s: (-rows[s][field], s),
            )
            picked = ordered[:10]
            comparisons.append(
                {
                    "reference": field,
                    "selected_count": len(picked),
                    "feature_available_population": len(ordered),
                    "top10_hits": sorted(winners & set(picked)),
                    "hit_count": len(winners & set(picked)),
                    "symbols": picked,
                }
            )
        combined = [
            s
            for s in universe
            if rows[s]["lagged_6h_oi_units_change_pct"] is not None
            and rows[s]["pre6h_quote_volume_vs_prior18h"] >= Decimal("1.5")
            and rows[s]["lagged_6h_oi_units_change_pct"] >= 3
        ]
        references.append(
            {
                "period": label,
                "feature_cutoff": stamp(start),
                "outcome_end": stamp(start + 24 * HOUR),
                "population": len(universe),
                "top10": outcome[:10],
                "references": comparisons,
                "volume_1_5x_and_oi_3pct": {
                    "selected": len(combined),
                    "hits": sorted(winners & set(combined)),
                },
                "winner_features": [
                    {
                        "symbol": r["symbol"],
                        "forward_24h_return_pct": r["return_pct"],
                        "pre24h_quote_volume_rank": volume_rank[r["symbol"]],
                        **rows[r["symbol"]],
                    }
                    for r in outcome[:10]
                ],
                "all_symbol_features_and_outcomes": [
                    {
                        "symbol": r["symbol"],
                        "forward_24h_return_pct": r["return_pct"],
                        **rows[r["symbol"]],
                    }
                    for r in outcome
                ],
            }
        )

    events = {
        "current": [event(r["symbol"], CUTOFF - 24 * HOUR, CUTOFF) for r in current],
        "yesterday": [event(r["symbol"], DAY_START, DAY_START + 24 * HOUR) for r in yesterday[:10]],
    }
    fine = read(root / "2026-11-05-winner-five-minute.json")["results"]
    transitions = []
    for symbol, response in sorted(fine.items()):
        bars = response["result"]
        step = 300_000
        assert len(bars) == 1200
        assert [b[0] for b in bars] == list(range(CUTOFF - 1200 * step, CUTOFF, step))
        for bar in bars:
            op, hi, lo, cl, vol, quote, buy = map(number, [bar[i] for i in (1, 2, 3, 4, 5, 7, 10)])
            assert bar[6] == bar[0] + step - 1 and bar[6] < CUTOFF
            assert 0 < lo <= min(op, cl) <= max(op, cl) <= hi
            assert vol >= 0 and 0 <= buy <= quote
        signals = []
        for index in range(288, len(bars)):
            bar = bars[index]
            if bar[0] < DAY_START:
                continue
            mean = sum(number(b[7]) for b in bars[index - 20 : index]) / 20
            prior_high = max(number(b[2]) for b in bars[index - 288 : index])
            if mean and number(bar[4]) > prior_high and number(bar[7]) >= mean * 2:
                # Fully closed-bar observational transition, not a production entry signal.
                confirm = bar[6] + 1
                next_scan = (confirm // (4 * HOUR)) * 4 * HOUR + 60_000
                if next_scan < confirm:
                    next_scan += 4 * HOUR
                signals.append(
                    {
                        "confirmed_boundary": stamp(confirm),
                        "quote_volume_ratio": number(bar[7]) / mean,
                        "distance_above_prior_24h_high_pct": change(bar[4], prior_high),
                        "next_nominal_four_hour_scan": stamp(next_scan),
                        "minutes_to_next_nominal_scan": (next_scan - confirm) / 60_000,
                    }
                )
        first_entry = entry(symbol)
        lead = None
        if signals and first_entry and not first_entry["left_censored"]:
            first_ms = int(
                datetime.fromisoformat(first_entry["first_observed_top10_boundary"]).timestamp()
                * 1000
            )
            confirm_ms = int(
                datetime.fromisoformat(signals[0]["confirmed_boundary"]).timestamp() * 1000
            )
            lead = (first_ms - confirm_ms) / 60_000
        transitions.append(
            {
                "symbol": symbol,
                "transition_count": len(signals),
                "first_transition": signals[0] if signals else None,
                "hourly_rank_entry": first_entry,
                "minutes_to_first_observed_hourly_entry": lead,
                "lead_time_caution": "hourly observation boundary; not exact leaderboard-entry lead time",
            }
        )
    paths = (
        sorted(root.glob("2026-11-05-universe-hourly-*.json"))
        + sorted(root.glob("2026-11-05-universe-oi-*.json"))
        + [
            root / "2026-11-05-market-input.json",
            root / "2026-11-04-local-screener.json",
            root / "2026-11-05-local-screener.json",
            root / "2026-11-05-winner-five-minute.json",
        ]
    )
    result = {
        "mode": "descriptive_research_only",
        "performance_claims_permitted": False,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "universe_count": len(universe),
        "hourly_rows_validated": len(universe) * 120,
        "oi_rows_validated": len(universe) * 120,
        "oi_missing_event_times": oi_gaps,
        "snapshot_newest_close": stamp(newest),
        "unknown_stale_tickers": stale,
        "current_observed_top10": current,
        "yesterday_rome_top10": yesterday[:10],
        "current_closed_hour_proxy_top10": proxy[:10],
        "scans": scans,
        "events": events,
        "fixed_landmark_comparisons": references,
        "winner_five_minute_transitions": transitions,
        "five_minute_rows_validated": len(fine) * 1200,
        "hourly_top10_timeline": [
            {"boundary": stamp(at), "symbols": sorted(s)} for at, s in timeline
        ],
        "input_sha256": {
            str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.resolve() != args.output.resolve()
        },
        "limitations": [
            "current-universe survivorship",
            "retrospective event selection",
            "no historic API arrival provenance",
            "hourly ranks are proxies, not archived leaderboards",
            "two overlapping windows, not independent out-of-sample evidence",
            "absent candidate receipts/history prevent exact live exclusion attribution",
        ],
    }
    with args.output.open("x") as f:
        json.dump(result, f, indent=2, default=lambda x: str(x) if isinstance(x, Decimal) else x)
        f.write("\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "universe_count",
                    "hourly_rows_validated",
                    "oi_rows_validated",
                    "current_observed_top10",
                    "yesterday_rome_top10",
                    "scans",
                ]
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
