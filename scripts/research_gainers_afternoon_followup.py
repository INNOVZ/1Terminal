"""Separate frozen-input checkpoint; no screener execution or strategy promotion."""

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "docs/security/evidence"
HOUR = 3600000
END = int(datetime(2026, 11, 5, 14, tzinfo=UTC).timestamp() * 1000)


def read(name):
    return json.loads((ROOT / name).read_text())


def pct(a, b):
    return (Decimal(str(a)) / Decimal(str(b)) - 1) * 100


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def feature(bars, samples, at):
    before = [b for b in bars if at - 24 * HOUR <= b[0] < at]
    assert len(before) == 24 and before[-1][6] < at
    assert all(
        Decimal(x["sumOpenInterest"]).is_finite() and Decimal(x["sumOpenInterest"]) > 0
        for x in samples
    )
    values = {x["timestamp"]: Decimal(x["sumOpenInterest"]) for x in samples}
    ratio = (sum(Decimal(b[7]) for b in before[-6:]) / 6) / (
        sum(Decimal(b[7]) for b in before[:-6]) / 18
    )
    growth = pct(values[at - HOUR], values[at - 7 * HOUR])
    independent = (math.fsum(float(b[7]) for b in before[-6:]) / 6) / (
        math.fsum(float(b[7]) for b in before[:-6]) / 18
    )
    assert math.isclose(float(ratio), independent, rel_tol=1e-10)
    return {
        "as_of": iso(at),
        "prior_24h_return_pct": pct(before[-1][4], before[0][1]),
        "prior_6h_return_pct": pct(before[-1][4], before[-6][1]),
        "quote_volume_ratio": ratio,
        "lagged_6h_oi_units_pct": growth,
        "latest_oi_event_time": iso(at - HOUR),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "2026-11-05-afternoon-analysis.json")
    output = parser.parse_args().output
    morning = read("2026-11-05-market-input.json")
    afternoon = read("2026-11-05-afternoon-market.json")
    original = read("2026-11-05-gainers-bottleneck-analysis-verified.json")
    scan = read("2026-11-05-afternoon-local-screener.json")
    history = read("2026-11-05-afternoon-new-entrants-history.json")["results"]
    first = {x["symbol"]: x for x in morning["tickers"]}
    second = {x["symbol"]: x for x in afternoon["tickers"]}
    old = {x["symbol"] for x in original["current_observed_top10"]}
    universe = {
        s["symbol"]
        for s in afternoon["exchange"]["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
    }
    old_universe = {
        s["symbol"]
        for s in morning["exchange"]["symbols"]
        if s["status"] == "TRADING"
        and s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
    }
    assert universe == old_universe and len(universe) == 522
    newest = max(second[s]["closeTime"] for s in universe)
    assert all(newest - second[s]["closeTime"] <= 60000 for s in universe)
    ordered = sorted(universe, key=lambda s: (-Decimal(second[s]["priceChangePercent"]), s))
    new = set(ordered[:10])
    assert ordered[:10] == [r["symbol"] for r in afternoon["derived"]["observed_top10"]]
    longs = {x["symbol"] for x in scan["opportunities"] if x["direction"] == "long"}
    markers = []
    for s in sorted(history):
        bars = history[s]["hourly"]["result"]
        samples = history[s]["oi"]["result"]
        fine = history[s]["five_minute"]["result"]
        assert [b[0] for b in bars] == list(range(END - 120 * HOUR, END, HOUR))
        assert [b[0] for b in fine] == list(range(END - 1200 * 300000, END, 300000))
        assert len(samples) == 120
        assert all(x["symbol"] == s for x in samples)
        times = [x["timestamp"] for x in samples]
        assert times == list(range(END - 119 * HOUR, END + 1, HOUR))
        for rows, step in [(bars, HOUR), (fine, 300000)]:
            for b in rows:
                op, hi, lo, cl, v, q, buy = map(
                    Decimal, [str(b[i]) for i in [1, 2, 3, 4, 5, 7, 10]]
                )
                assert 0 < lo <= min(op, cl) <= max(op, cl) <= hi and v >= 0 and 0 <= buy <= q
                assert b[6] == b[0] + step - 1 and b[6] < END
        event = max(
            (b for b in bars if END - 24 * HOUR <= b[0] < END),
            key=lambda b: Decimal(b[4]) / Decimal(b[1]),
        )
        transition = None
        for i in range(288, len(fine)):
            b = fine[i]
            if b[0] < END - 24 * HOUR:
                continue
            mean = sum(Decimal(p[7]) for p in fine[i - 20 : i]) / 20
            ceiling = max(Decimal(p[2]) for p in fine[i - 288 : i])
            if mean and Decimal(b[4]) > ceiling and Decimal(b[7]) >= mean * 2:
                transition = {
                    "confirmed_boundary": iso(b[6] + 1),
                    "quote_volume_ratio": Decimal(b[7]) / mean,
                }
                break
        markers.append(
            {
                "symbol": s,
                "strongest_hour_open": iso(event[0]),
                "strongest_hour_return_pct": pct(event[4], event[1]),
                "before_strongest_hour": feature(bars, samples, event[0]),
                "first_five_minute_transition": transition,
            }
        )

    old_bars, old_oi = {}, {}
    for kind, target in [("hourly", old_bars), ("oi", old_oi)]:
        for p in ROOT.glob(f"2026-11-05-universe-{kind}-*.json"):
            target.update({s: r["result"] for s, r in json.loads(p.read_text())["results"].items()})
    at = END - 5 * HOUR
    features = {s: feature(old_bars[s], old_oi[s], at) for s in sorted(universe)}
    references = []
    for field in ["prior_24h_return_pct", "quote_volume_ratio", "lagged_6h_oi_units_pct"]:
        chosen = sorted(universe, key=lambda s: (-features[s][field], s))[:10]
        references.append(
            {
                "reference": field,
                "selected": chosen,
                "afternoon_top10_overlap": sorted(set(chosen) & new),
                "new_entrant_overlap": sorted(set(chosen) & (new - old)),
            }
        )
    marks = [
        {
            "symbol": s,
            "morning_price": first[s]["lastPrice"],
            "afternoon_price": second[s]["lastPrice"],
            "mark_to_mark_change_pct": pct(second[s]["lastPrice"], first[s]["lastPrice"]),
            "morning_rolling_return_pct": first[s]["priceChangePercent"],
            "afternoon_rolling_return_pct": second[s]["priceChangePercent"],
        }
        for s in sorted(old)
    ]
    for m in marks:
        f = (float(m["afternoon_price"]) / float(m["morning_price"]) - 1) * 100
        assert math.isclose(f, float(m["mark_to_mark_change_pct"]), abs_tol=1e-10)
    paths = [
        ROOT / n
        for n in [
            "2026-11-05-market-input.json",
            "2026-11-05-afternoon-market.json",
            "2026-11-05-gainers-bottleneck-analysis-verified.json",
            "2026-11-05-afternoon-local-screener.json",
            "2026-11-05-afternoon-new-entrants-history.json",
            "2026-11-05-afternoon-api.json",
        ]
    ]
    paths += sorted(ROOT.glob("2026-11-05-universe-hourly-*.json")) + sorted(
        ROOT.glob("2026-11-05-universe-oi-*.json")
    )
    result = {
        "captured_at": afternoon["captured_at"],
        "universe_unchanged": True,
        "universe_count": len(universe),
        "unknown_tickers": [],
        "afternoon_top10": afternoon["derived"]["observed_top10"],
        "new_relative_to_morning": sorted(new - old),
        "left_relative_to_morning": sorted(old - new),
        "retained": len(new & old),
        "normal_scan_timestamp": scan["timestamp"],
        "normal_scan_longs": sorted(longs),
        "long_overlap": sorted(longs & new),
        "normal_scan_receipt": scan.get("scan_archive"),
        "morning_winner_marks": marks,
        "new_entrant_events": markers,
        "frozen_morning_feature_cutoff": iso(at),
        "exploratory_references": references,
        "new_entrant_frozen_features": {s: features[s] for s in sorted(new - old)},
        "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "performance_claims_permitted": False,
        "limitations": [
            "not preregistered",
            "later selected-winner histories are retrospective",
            "rolling rank is not forward price return",
            "mark changes are not executed returns or excursions",
            "missing intervening candidate journals prevent never-alerted claims",
        ],
    }
    with output.open("x") as f:
        json.dump(result, f, indent=2, default=str)
        f.write("\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "new_relative_to_morning",
                    "long_overlap",
                    "new_entrant_events",
                    "exploratory_references",
                    "new_entrant_frozen_features",
                ]
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
