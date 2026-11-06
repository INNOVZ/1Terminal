"""Recheck eligibility sensitivity with frozen exclusions, never ambient settings."""

import json
from pathlib import Path

root = Path(__file__).resolve().parent
expected = json.loads((root / "2026-11-05-eligible-reference-sensitivity.json").read_text())
analysis = json.loads((root / "2026-11-05-gainers-bottleneck-analysis-verified.json").read_text())
exchange = json.loads((root / "2026-11-05-market-input.json").read_text())["exchange"]
excluded = set(expected["excluded_bases"])
toxic = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}
eligible = {
    s["symbol"]
    for s in exchange["symbols"]
    if s["status"] == "TRADING"
    and s["quoteAsset"] == "USDT"
    and s["contractType"] == "PERPETUAL"
    and s["baseAsset"].isascii()
    and s["baseAsset"] not in excluded
    and s["symbol"] not in toxic
}
assert len(eligible) == expected["eligible_count"] == 512
for c, result in zip(analysis["fixed_landmark_comparisons"], expected["periods"], strict=True):
    rows = [r for r in c["all_symbol_features_and_outcomes"] if r["symbol"] in eligible]
    rows.sort(key=lambda r: (-float(r["forward_24h_return_pct"]), r["symbol"]))
    winners = {r["symbol"] for r in rows[:10]}
    assert [r["symbol"] for r in rows[:10]] == result["top10"]
    for ref in result["references"]:
        chosen = sorted(rows, key=lambda r: (-float(r[ref["reference"]]), r["symbol"]))[:10]
        assert [r["symbol"] for r in chosen] == ref["selections"]
        assert sorted({r["symbol"] for r in chosen} & winners) == ref["hits"]
    selected = {
        r["symbol"]
        for r in rows
        if float(r["pre6h_quote_volume_vs_prior18h"]) >= 1.5
        and float(r["lagged_6h_oi_units_change_pct"]) >= 3
    }
    assert len(selected) == result["combined"]["selected"]
    assert sorted(selected & winners) == result["combined"]["hits"]
print("512-member eligible-policy sensitivity reproduced without loading configuration or runtime.")
