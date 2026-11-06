"""Independent float/fsum checks against the Decimal audit and original API replies."""

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
analysis_path = ROOT / "2026-11-05-gainers-bottleneck-analysis-verified.json"
report = json.loads(analysis_path.read_text())
bars, oi = {}, {}
for kind, destination in [("hourly", bars), ("oi", oi)]:
    for path in ROOT.glob(f"2026-11-05-universe-{kind}-*.json"):
        for symbol, response in json.loads(path.read_text())["results"].items():
            destination[symbol] = response["result"]
assert len(bars) == len(oi) == 522
assert report["performance_claims_permitted"] is False
checked = 0
for comparison in report["fixed_landmark_comparisons"]:
    start = int(datetime.fromisoformat(comparison["feature_cutoff"]).timestamp() * 1000)
    expected = []
    for row in comparison["all_symbol_features_and_outcomes"]:
        symbol = row["symbol"]
        before = [b for b in bars[symbol] if start - 24 * 3600000 <= b[0] < start]
        after = [b for b in bars[symbol] if start <= b[0] < start + 24 * 3600000]
        assert len(before) == len(after) == 24
        assert before[-1][6] < start <= after[0][0]
        ret = (float(after[-1][4]) / float(after[0][1]) - 1) * 100
        price = (float(before[-1][4]) / float(before[0][1]) - 1) * 100
        volume = (math.fsum(float(b[7]) for b in before[-6:]) / 6) / (
            math.fsum(float(b[7]) for b in before[:-6]) / 18
        )
        units = {x["timestamp"]: float(x["sumOpenInterest"]) for x in oi[symbol]}
        growth = (units[start - 3600000] / units[start - 7 * 3600000] - 1) * 100
        for actual, value in [
            (ret, row["forward_24h_return_pct"]),
            (price, row["pre24h_return_pct"]),
            (volume, row["pre6h_quote_volume_vs_prior18h"]),
            (growth, row["lagged_6h_oi_units_change_pct"]),
        ]:
            assert math.isclose(actual, float(value), rel_tol=1e-10, abs_tol=1e-10)
        expected.append((symbol, ret))
        checked += 1
    expected.sort(key=lambda item: (-item[1], item[0]))
    assert [s for s, _ in expected[:10]] == [r["symbol"] for r in comparison["top10"]]
    for reference in comparison["references"]:
        field = reference["reference"]
        rows = comparison["all_symbol_features_and_outcomes"]
        picked = sorted(rows, key=lambda row: (-float(row[field]), row["symbol"]))[:10]
        assert [r["symbol"] for r in picked] == reference["symbols"]
        assert set(reference["top10_hits"]) == {s for s, _ in expected[:10]} & {
            r["symbol"] for r in picked
        }

fine = json.loads((ROOT / "2026-11-05-winner-five-minute.json").read_text())["results"]
events_checked = 0
begin = int(datetime(2026, 11, 3, 23, tzinfo=UTC).timestamp() * 1000)
for item in report["winner_five_minute_transitions"]:
    rows = fine[item["symbol"]]["result"]
    first = None
    for index, bar in enumerate(rows):
        if index < 288 or bar[0] < begin:
            continue
        mean = math.fsum(float(b[7]) for b in rows[index - 20 : index]) / 20
        high = max(float(b[2]) for b in rows[index - 288 : index])
        if mean and float(bar[4]) > high and float(bar[7]) >= mean * 2:
            first = bar[6] + 1
            break
    assert (first is None) == (item["first_transition"] is None)
    if first is not None:
        signal = item["first_transition"]
        at = datetime.fromtimestamp(first / 1000, UTC)
        assert at.isoformat() == signal["confirmed_boundary"]
        midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
        schedules = [midnight + timedelta(hours=h, minutes=1) for h in range(0, 29, 4)]
        upcoming = min(t for t in schedules if t >= at)
        assert upcoming.isoformat() == signal["next_nominal_four_hour_scan"]
    events_checked += 1

for name, checksum in report["input_sha256"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == checksum
script = ROOT.parents[2] / "scripts/research_gainers_bottleneck_audit.py"
assert hashlib.sha256(script.read_bytes()).hexdigest() == report["script_sha256"]
result = {
    "fixed_landmark_rows_cross_checked": checked,
    "winner_transition_histories_cross_checked": events_checked,
    "input_hashes_verified": len(report["input_sha256"]),
    "reference_rankings_and_hits_verified": True,
    "same_cutoff_no_future_feature_bars": True,
    "performance_claims_permitted": False,
    "analysis_sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--output", type=Path, default=ROOT / "2026-11-05-gainers-independent-verification.json"
)
path = parser.parse_args().output
with path.open("x") as stream:
    json.dump(result, stream, indent=2)
    stream.write("\n")
print(json.dumps(result))
