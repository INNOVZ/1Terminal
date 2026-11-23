from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import daily_radar_x_brief as brief_mod


def _make_opp(
    symbol: str,
    *,
    gates_passed: int = 5,
    origin: str = "fresh",
    conviction: float = 80.0,
    score: float = 85.0,
    radar_status: str = "FRESH_COIL",
    data_status: str | None = "current",
) -> dict:
    checklist = [
        {"title": f"Gate {i}", "passed": i < gates_passed, "detail": f"detail {i}"}
        for i in range(5)
    ]
    return {
        "symbol": symbol,
        "base_asset": symbol.removesuffix("USDT"),
        "direction": "long",
        "setup_label": "VCP_COMPRESSION_LONG",
        "regime": "volatility_squeeze",
        "execution_mode": "CEILING_BREAKOUT",
        "score": str(score),
        "conviction": str(conviction),
        "current_price": "0.3813",
        "pullback_entry": "0.3605",
        "breakout_trigger": "0.3833",
        "suggested_stop": "0.3526",
        "suggested_targets": ["0.4294", "0.4651"],
        "risk_reward_ratio": "2.66",
        "poc_price": "0.3579",
        "funding_rate_bps": "+0.50 bps",
        "oi_change_7d_pct": "-2.15%",
        "taker_buy_sell_ratio_4h": "1.2947",
        "residual_return_24h_pct": "+7.43%",
        "radar_origin": origin,
        "radar_status": radar_status,
        "radar_status_label": radar_status.replace("_", " "),
        "radar_data_status": data_status,
        "gates_passed": gates_passed,
        "gates_total": 5,
        "checklist": checklist,
        "candles": [
            {"time": 1700000000 + i * 3600, "open": 0.36, "high": 0.38, "low": 0.35, "close": 0.37, "volume": 1000.0}
            for i in range(20)
        ],
    }


def test_strict_five_of_five_filter_and_dynamic_format(tmp_path: Path) -> None:
    now = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    payload = {
        "timestamp": now.isoformat(),
        "data_status": "current",
        "macro_narrative": "F&G: 29/100 (Fear) | Stablecoins 7d: -1.70%",
        "opportunities": [
            _make_opp("SPXUSDT", gates_passed=5, origin="fresh", conviction=82.0),
            _make_opp("BADUSDT", gates_passed=4, origin="fresh", conviction=95.0),
            _make_opp("UNAVAILUSDT", gates_passed=5, data_status="unavailable", conviction=99.0),
            _make_opp("FOLKSUSDT", gates_passed=5, origin="retained", conviction=75.0),
        ],
    }
    brief, featured = brief_mod.build_daily_brief(payload, history=[], events_dir=tmp_path, now=now)
    assert brief["total_verified_5_of_5"] == 2
    assert brief["recommended_format"] == "single_post"
    assert featured is not None and featured["symbol"] == "SPXUSDT"

    # 3+ fresh setups -> mini_thread capped at 4
    payload["opportunities"].extend([
        _make_opp("SUIUSDT", gates_passed=5, origin="fresh", conviction=79.0),
        _make_opp("TAOUSDT", gates_passed=5, origin="fresh", conviction=77.0),
    ])
    brief2, _ = brief_mod.build_daily_brief(payload, history=[], events_dir=tmp_path, now=now)
    assert brief2["recommended_format"] == "mini_thread"
    assert brief2["recommended_tweet_count"] == 4


def test_dedupes_recently_drafted_and_detects_followups(tmp_path: Path) -> None:
    now = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    history = [
        {
            "at": (now - timedelta(hours=24)).isoformat(),
            "status": "drafted",
            "angle": "relative_strength_vs_fear",
            "symbols": ["SPX"],
        }
    ]
    event_file = tmp_path / "ev.json"
    event_file.write_text(
        json.dumps({
            "events": [
                {
                    "symbol": "SPXUSDT",
                    "at": (now - timedelta(hours=3)).isoformat(),
                    "state": "STOP_BREACHED",
                    "reason": "long_stop_loss_breached",
                    "observed_price": "0.3510",
                }
            ]
        }),
        encoding="utf-8",
    )
    payload = {
        "timestamp": now.isoformat(),
        "data_status": "current",
        "macro_narrative": "F&G: 31/100 (Fear)",
        "opportunities": [
            _make_opp("SPXUSDT", gates_passed=5, origin="retained", radar_status="COILING"),
            _make_opp("FOLKSUSDT", gates_passed=5, origin="fresh", conviction=78.0),
        ],
    }
    brief, featured = brief_mod.build_daily_brief(payload, history=history, events_dir=tmp_path, now=now)
    assert brief["skipped_recent_symbols"] == ["SPX"]
    assert featured is not None and featured["symbol"] == "FOLKSUSDT"
    assert brief["recommended_angle"] == "accountability_followup"
    assert len(brief["recent_followups"]) == 1
    assert brief["recent_followups"][0]["reason"] == "long_stop_loss_breached"


def test_anti_ai_and_280_char_linter() -> None:
    good = [
        "f&g sitting at 29 and stables bleeding, yet $SPX quietly compressed into a tight 17% range right above its 30d poc (0.358).\n\nfunding flat at +0.5 bps while 4h takers sit 1.29x bid. dip near 0.360 or reclaim 0.383, dead below 0.352.\n\nanyone actually bidding alts here?"
    ]
    res_good = brief_mod.lint_tweets(good)
    assert res_good["ok"] is True, res_good["errors"]

    bad = [
        "🚀 Good morning CT! Let's delve into this compelling 5/5 setup on $SPX $BTC $ETH! Check https://example.com #Crypto #Altcoins " + "x" * 200
    ]
    res_bad = brief_mod.lint_tweets(bad)
    assert res_bad["ok"] is False
    joined = " ".join(res_bad["errors"])
    assert "exceeds Standard 280-char cap" in joined
    assert "hashtag" in joined
    assert "cashtags" in joined
    assert "external link" in joined
    assert "shill/bot emoji" in joined
    assert "delve" in joined


def test_social_chart_html_renders_clean_levels() -> None:
    opp = _make_opp("SPXUSDT")
    html = brief_mod.render_social_chart_html(opp, macro_narrative="F&G: 29/100 (Fear)")
    assert "$SPX" in html
    assert "INVALIDATION 0.3526" in html
    assert "30D POC 0.3579" in html
    assert "TRIGGER 0.3833" in html
    assert "__ATLAS_CHART_READY__" in html


def test_draft_preview_html_renders_intent_and_copy_controls(tmp_path: Path) -> None:
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    brief = {
        "generated_at": "2026-11-23T08:00:00+00:00",
        "macro_narrative": "F&G: 11/100 (Extreme Fear)",
        "recommended_format": "single_post",
        "recommended_angle": "relative_strength_vs_fear",
        "total_verified_5_of_5": 1,
    }
    tweets = ["fear & greed at 11 yet $USUAL holding 0.0106 poc. dead below 0.0107. bidding or waiting?"]
    out = brief_mod.render_draft_preview_html(brief, tweets, chart_png_path=png)
    assert "https://x.com/intent/post?text=" in out
    assert "Copy Chart Image" in out
    assert "Copy Tweet 1" in out
    assert "data:image/png;base64," in out

