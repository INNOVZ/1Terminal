"""Daily 9:00 AM CET live 5/5 Radar -> X.com draft brief, chart renderer & anti-AI linter.

Subcommands:
    fetch  Fetch live 5/5 Atlas Radar candidates, audit prior drafts, choose single vs. mini-thread,
           and render a clean 16:9 dark-mode Lightweight Charts HTML for screenshot capture.
    lint   Validate drafted tweet(s) against the Standard-tier 280-char cap and Anti-AI CT rules.
    log    Append a drafted/skipped run record to data/x_post_history.jsonl for dedupe & follow-ups.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "x_daily"
DEFAULT_HISTORY_PATH = REPO_ROOT / "data" / "x_post_history.jsonl"
DEFAULT_RADAR_EVENTS_DIR = REPO_ROOT / "data" / "radar_events"

VOICE_ANGLES: tuple[str, ...] = (
    "relative_strength_vs_fear",
    "level_to_level_plan",
    "derivatives_tell",
    "accountability_followup",
)

BANNED_AI_PHRASES: tuple[str, ...] = (
    "delve",
    "landscape",
    "tapestry",
    "beacon",
    "testament",
    "navigate",
    "unlock",
    "unpack",
    "noteworthy",
    "poised",
    "compelling",
    "robust",
    "paradigm",
    "confluence is key",
    "ever-evolving",
    "good morning ct",
    "good morning traders",
    "gm traders",
    "gm crypto twitter",
    "like and retweet",
    "like & rt",
    "like & retweet",
    "drop your thoughts",
    "what are your thoughts",
    "buckle up",
    "stay tuned",
    "let's dive in",
    "lets dive in",
    "a thread",
    "win rate",
    "win-rate",
    "expected value",
    "guaranteed",
    "100x",
    "to the moon",
    "nfa dyior",
    "not financial advice",
)

SHILL_EMOJIS: frozenset[str] = frozenset({"🚀", "🔥", "💎", "💰", "📈", "🚨", "🧵", "👇", "✅", "🎯", "📊"})

_HASHTAG_RE = re.compile(r"(?:^|\s)#[A-Za-z0-9_]+")
_CASHTAG_RE = re.compile(r"(?:^|[\s(\[])\$[A-Za-z][A-Za-z0-9]{1,9}\b")
_URL_RE = re.compile(r"https?://|www\.|t\.co/", re.IGNORECASE)
_THREAD_NUM_RE = re.compile(r"(?:^|\s)\d+/\d+(?:\s|$)")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]",
    flags=re.UNICODE,
)


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        cleaned = str(val).replace("%", "").replace("bps", "").replace("$", "").strip()
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _fmt_price(val: Any) -> str:
    num = _safe_float(val)
    if num is None:
        return "n/a"
    if num >= 1000:
        return f"{num:,.1f}"
    if num >= 10:
        return f"{num:.2f}"
    if num >= 1:
        return f"{num:.3f}"
    if num >= 0.01:
        return f"{num:.4f}"
    return f"{num:.6f}"


def is_verified_five_of_five(opp: dict[str, Any]) -> bool:
    """Return True only if an opportunity has 5 positively verified current Radar gates."""
    if not isinstance(opp, dict):
        return False
    if opp.get("radar_data_status") == "unavailable":
        return False
    checklist = opp.get("checklist")
    if not isinstance(checklist, list) or len(checklist) != 5:
        return False
    if not all(isinstance(item, dict) and item.get("passed") is True for item in checklist):
        return False
    gates_passed = opp.get("gates_passed")
    return not (gates_passed is not None and int(gates_passed) != 5)


def _base_asset(opp: dict[str, Any]) -> str:
    base = opp.get("base_asset")
    if isinstance(base, str) and base.strip():
        return base.strip().upper()
    sym = str(opp.get("symbol", "")).upper()
    return sym.removesuffix("USDT").removesuffix("USDC")


def load_post_history(history_path: Path = DEFAULT_HISTORY_PATH) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def append_post_history(entry: dict[str, Any], history_path: Path = DEFAULT_HISTORY_PATH) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_recent_radar_outcomes(
    posted_symbols: set[str],
    events_dir: Path = DEFAULT_RADAR_EVENTS_DIR,
    now: datetime | None = None,
    lookback_hours: float = 96.0,
) -> list[dict[str, Any]]:
    """Find notable lifecycle transitions (target/stop/breakout) for recently drafted symbols."""
    if not posted_symbols or not events_dir.exists():
        return []
    ref_now = now or datetime.now(UTC)
    cutoff = ref_now - timedelta(hours=lookback_hours)
    notable_states = {"TARGET_REACHED", "STOP_BREACHED", "INVALIDATED", "EXPANSION", "ACTIVE_EXPANSION"}
    notable_reasons = {
        "long_stop_loss_breached",
        "short_stop_loss_breached",
        "target_reached",
        "target_1_reached",
        "target_2_reached",
    }
    matches: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    files = sorted(events_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:120]
    for fpath in files:
        try:
            payload = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ev in payload.get("events", []):
            if not isinstance(ev, dict):
                continue
            sym = str(ev.get("symbol", "")).upper()
            base = sym.removesuffix("USDT").removesuffix("USDC")
            if base not in posted_symbols and sym not in posted_symbols:
                continue
            at_raw = ev.get("at")
            try:
                ev_at = datetime.fromisoformat(str(at_raw))
                if ev_at.tzinfo is None:
                    ev_at = ev_at.replace(tzinfo=UTC)
            except Exception:
                continue
            if ev_at < cutoff:
                continue
            state = str(ev.get("state", "")).upper()
            reason = str(ev.get("reason", "")).lower()
            if state in notable_states or reason in notable_reasons:
                key = (base, state, reason)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                matches.append({
                    "base_asset": base,
                    "symbol": sym,
                    "at": ev_at.isoformat(),
                    "state": state,
                    "reason": reason,
                    "observed_price": ev.get("observed_price"),
                })
    matches.sort(key=lambda item: str(item.get("at", "")), reverse=True)
    return matches[:5]


def choose_daily_angle(history: list[dict[str, Any]], has_followups: bool) -> str:
    last_angles = [str(item.get("angle", "")) for item in history[-3:] if item.get("angle")]
    if has_followups and (not last_angles or last_angles[-1] != "accountability_followup"):
        return "accountability_followup"
    candidates = [a for a in VOICE_ANGLES if a != "accountability_followup" or has_followups]
    for angle in candidates:
        if angle not in last_angles[-2:]:
            return angle
    return candidates[len(history) % len(candidates)]


def summarize_opportunity(opp: dict[str, Any]) -> dict[str, Any]:
    targets = opp.get("suggested_targets") or []
    checklist_notes = [
        f"{c.get('title')}: {c.get('detail')}"
        for c in (opp.get("checklist") or [])
        if isinstance(c, dict)
    ]
    return {
        "symbol": opp.get("symbol"),
        "base_asset": _base_asset(opp),
        "cashtag": f"${_base_asset(opp)}",
        "direction": str(opp.get("direction", "long")).lower(),
        "setup_label": opp.get("setup_label"),
        "regime": opp.get("regime"),
        "execution_mode": opp.get("execution_mode"),
        "breakout_readiness_status": opp.get("breakout_readiness_status"),
        "radar_origin": opp.get("radar_origin", "fresh"),
        "radar_status": opp.get("radar_status"),
        "radar_status_label": opp.get("radar_status_label"),
        "score": _safe_float(opp.get("score")),
        "conviction": _safe_float(opp.get("conviction")),
        "current_price": _safe_float(opp.get("current_price")),
        "pullback_entry": _safe_float(opp.get("pullback_entry") or opp.get("suggested_entry")),
        "breakout_trigger": _safe_float(opp.get("breakout_trigger") or opp.get("suggested_entry")),
        "suggested_stop": _safe_float(opp.get("suggested_stop")),
        "targets": [_safe_float(t) for t in targets[:3] if _safe_float(t) is not None],
        "risk_reward_ratio": _safe_float(opp.get("risk_reward_ratio")),
        "poc_price": _safe_float(opp.get("poc_price")),
        "vah_price": _safe_float(opp.get("vah_price")),
        "val_price": _safe_float(opp.get("val_price")),
        "funding_rate_bps": opp.get("funding_rate_bps"),
        "oi_change_1h_pct": opp.get("oi_change_1h_pct"),
        "oi_change_7d_pct": opp.get("oi_change_7d_pct"),
        "taker_buy_sell_ratio_4h": opp.get("taker_buy_sell_ratio_4h") or opp.get("taker_buy_sell_ratio"),
        "residual_return_24h_pct": opp.get("residual_return_24h_pct"),
        "volume_contraction_ratio": opp.get("volume_contraction_ratio"),
        "checklist_notes": checklist_notes,
        "candle_count": len(opp.get("candles") or []),
    }


def build_daily_brief(
    payload: dict[str, Any],
    history: list[dict[str, Any]],
    events_dir: Path = DEFAULT_RADAR_EVENTS_DIR,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Select verified 5/5 candidates, decide single vs. mini-thread, and return (brief, featured_opp)."""
    ref_now = now or datetime.now(UTC)
    raw_opps = payload.get("opportunities") or []
    verified = [o for o in raw_opps if isinstance(o, dict) and is_verified_five_of_five(o)]

    # Deduplicate by base asset preserving priority: fresh before retained, then higher conviction/score.
    def _rank_key(opp: dict[str, Any]) -> tuple[int, float, float]:
        is_fresh = 1 if str(opp.get("radar_origin", "")).lower() == "fresh" else 0
        conv = _safe_float(opp.get("conviction")) or 0.0
        score = _safe_float(opp.get("score")) or 0.0
        return (is_fresh, conv, score)

    verified.sort(key=_rank_key, reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_bases: set[str] = set()
    for opp in verified:
        base = _base_asset(opp)
        if base and base not in seen_bases:
            seen_bases.add(base)
            deduped.append(opp)

    # Identify recently drafted symbols (last 36 hours) to avoid repetitive spam.
    recent_cutoff = ref_now - timedelta(hours=36)
    followup_cutoff = ref_now - timedelta(hours=96)
    recently_drafted: set[str] = set()
    followup_pool: set[str] = set()
    for item in history:
        try:
            ts = datetime.fromisoformat(str(item.get("at", "")))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except Exception:
            continue
        syms = [str(s).upper() for s in (item.get("symbols") or []) if s]
        if ts >= recent_cutoff:
            recently_drafted.update(syms)
        if ts >= followup_cutoff:
            followup_pool.update(syms)

    fresh_for_post: list[dict[str, Any]] = []
    skipped_recent: list[str] = []
    material_states = {"AT_ENTRY_ZONE", "BREAKOUT", "ACTIVE_EXPANSION"}
    for opp in deduped:
        base = _base_asset(opp)
        status = str(opp.get("radar_status") or "").upper()
        if base in recently_drafted and status not in material_states:
            skipped_recent.append(base)
            continue
        fresh_for_post.append(opp)

    # If all 5/5 setups were recently drafted, keep the single highest-conviction one as a status update
    # only if its radar_origin is fresh or there are multiple; otherwise still surface top 1 with flag.
    reused_recent_fallback = False
    if not fresh_for_post and deduped:
        fresh_for_post = [deduped[0]]
        reused_recent_fallback = True

    followups = load_recent_radar_outcomes(followup_pool, events_dir=events_dir, now=ref_now)
    angle = choose_daily_angle(history, has_followups=bool(followups))

    count = len(fresh_for_post)
    if count == 0:
        recommended_format = "no_signal_macro"
        tweet_budget = 1
    elif count <= 2:
        recommended_format = "single_post"
        tweet_budget = 1
    else:
        recommended_format = "mini_thread"
        tweet_budget = min(4, count)

    featured_opp = fresh_for_post[0] if fresh_for_post else None
    brief = {
        "generated_at": ref_now.isoformat(),
        "scan_timestamp": payload.get("timestamp"),
        "scan_id": payload.get("scan_id"),
        "data_status": payload.get("data_status"),
        "macro_narrative": payload.get("macro_narrative"),
        "total_verified_5_of_5": len(deduped),
        "eligible_for_today_count": count,
        "skipped_recent_symbols": skipped_recent,
        "reused_recent_fallback": reused_recent_fallback,
        "recommended_format": recommended_format,
        "recommended_tweet_count": tweet_budget,
        "recommended_angle": angle,
        "account_constraints": {
            "tier": "standard",
            "max_chars_per_tweet": 280,
            "max_cashtags_per_tweet": 2,
            "max_hashtags": 0,
            "max_emojis_per_tweet": 1,
            "links_in_tweet_1_allowed": False,
            "publish_mode": "save_to_x_drafts",
        },
        "featured_candidate": summarize_opportunity(featured_opp) if featured_opp else None,
        "candidates": [summarize_opportunity(o) for o in fresh_for_post[:4]],
        "recent_followups": followups,
    }
    return brief, featured_opp


def render_social_chart_html(opp: dict[str, Any], macro_narrative: str = "") -> str:
    """Render a clean, high-contrast 1200x675 dark-mode chart HTML built for X.com mobile/desktop feeds."""
    candles = opp.get("candles") or []
    clean_candles = []
    seen_times: set[int] = set()
    for c in candles[-140:]:
        if not isinstance(c, dict):
            continue
        t = c.get("time")
        if t is None:
            continue
        t_int = int(t)
        if t_int in seen_times:
            continue
        seen_times.add(t_int)
        clean_candles.append({
            "time": t_int,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0.0)),
        })
    clean_candles.sort(key=lambda x: x["time"])

    base = _base_asset(opp)
    direction = str(opp.get("direction", "long")).upper()
    is_short = direction == "SHORT"
    dir_color = "#ef4444" if is_short else "#10b981"
    setup_clean = str(opp.get("setup_label", "COIL")).replace("_", " ").title()
    cur_price = _safe_float(opp.get("current_price"))
    pullback = _safe_float(opp.get("pullback_entry") or opp.get("suggested_entry"))
    trigger = _safe_float(opp.get("breakout_trigger"))
    stop = _safe_float(opp.get("suggested_stop"))
    poc = _safe_float(opp.get("poc_price"))
    targets = [_safe_float(t) for t in (opp.get("suggested_targets") or [])[:2]]
    targets = [t for t in targets if t is not None]

    price_lines: list[dict[str, Any]] = []
    if trigger is not None:
        price_lines.append({"price": trigger, "color": "#38bdf8", "title": f"TRIGGER {_fmt_price(trigger)}", "style": 2})
    if pullback is not None and (trigger is None or abs(pullback - trigger) / max(abs(trigger), 1e-9) > 0.004):
        price_lines.append({"price": pullback, "color": "#a78bfa", "title": f"DIP ZONE {_fmt_price(pullback)}", "style": 2})
    if poc is not None:
        price_lines.append({"price": poc, "color": "#f59e0b", "title": f"30D POC {_fmt_price(poc)}", "style": 3})
    if stop is not None:
        price_lines.append({"price": stop, "color": "#ef4444", "title": f"INVALIDATION {_fmt_price(stop)}", "style": 0})
    for idx, tgt in enumerate(targets, start=1):
        price_lines.append({"price": tgt, "color": "#10b981", "title": f"T{idx} {_fmt_price(tgt)}", "style": 2})

    funding = opp.get("funding_rate_bps") or "n/a"
    taker = opp.get("taker_buy_sell_ratio_4h") or opp.get("taker_buy_sell_ratio") or "n/a"
    oi7d = opp.get("oi_change_7d_pct") or "n/a"
    rel24 = opp.get("residual_return_24h_pct") or "n/a"
    status_label = str(opp.get("radar_status_label") or "5/5 RADAR")
    targets_summary = " · ".join(f"T{i} {_fmt_price(t)}" for i, t in enumerate(targets, start=1))
    header_meta = f"{setup_clean} · {targets_summary}" if targets_summary else setup_clean

    payload_json = json.dumps({
        "candles": clean_candles,
        "priceLines": price_lines,
        "precision": 4 if (cur_price or 1.0) < 1 else (3 if (cur_price or 1.0) < 10 else 2),
    })

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1200, initial-scale=1.0" />
  <title>${base} 1H Structure</title>
  <script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{
      width: 1200px;
      height: 675px;
      overflow: hidden;
      background: #070b12;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    }}
    .frame {{
      width: 1200px;
      height: 675px;
      display: flex;
      flex-direction: column;
      background: radial-gradient(circle at 12% 0%, #101a2e 0%, #070b12 58%);
      border: 1px solid #1e293b;
      padding: 18px 22px 14px 22px;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }}
    .title-group {{
      display: flex;
      align-items: baseline;
      gap: 12px;
    }}
    .ticker {{
      font-size: 30px;
      font-weight: 800;
      letter-spacing: -0.5px;
      color: #ffffff;
    }}
    .price {{
      font-size: 22px;
      font-weight: 700;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      color: #e2e8f0;
    }}
    .badge {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.6px;
      padding: 4px 10px;
      border-radius: 999px;
      text-transform: uppercase;
      background: rgba(16, 185, 129, 0.14);
      color: {dir_color};
      border: 1px solid {dir_color}55;
    }}
    .meta {{
      font-size: 13px;
      color: #94a3b8;
      font-weight: 500;
    }}
    #chart {{
      flex: 1;
      width: 100%;
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid #172133;
      background: #0a101c;
    }}
    .footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 10px;
      font-size: 13px;
      color: #94a3b8;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .pills {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .pill {{
      background: #0f172a;
      border: 1px solid #1e293b;
      padding: 4px 10px;
      border-radius: 6px;
      color: #cbd5e1;
    }}
    .pill b {{ color: #f8fafc; }}
  </style>
</head>
<body>
  <div class="frame">
    <div class="header">
      <div class="title-group">
        <span class="ticker">${base}</span>
        <span class="price">${_fmt_price(cur_price)}</span>
        <span class="badge">{direction} · 1H · {status_label}</span>
      </div>
      <div class="meta">{header_meta}</div>
    </div>
    <div id="chart"></div>
    <div class="footer">
      <div class="pills">
        <span class="pill">Funding <b>{funding}</b></span>
        <span class="pill">4H Taker <b>{taker}</b></span>
        <span class="pill">7D OI <b>{oi7d}</b></span>
        <span class="pill">24H Rel <b>{rel24}</b></span>
      </div>
      <div style="font-size:12px;">{macro_narrative[:95]}</div>
    </div>
  </div>
  <script>
    const DATA = {payload_json};
    const el = document.getElementById("chart");
    const chart = LightweightCharts.createChart(el, {{
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {{
        background: {{ type: "solid", color: "#0a101c" }},
        textColor: "#94a3b8",
        fontSize: 12,
      }},
      grid: {{
        vertLines: {{ color: "rgba(30, 41, 59, 0.45)" }},
        horzLines: {{ color: "rgba(30, 41, 59, 0.45)" }},
      }},
      rightPriceScale: {{
        borderColor: "#1e293b",
        scaleMargins: {{ top: 0.14, bottom: 0.18 }},
      }},
      timeScale: {{
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      }},
      crosshair: {{ mode: 0 }},
    }});

    const series = chart.addCandlestickSeries({{
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
      priceFormat: {{
        type: "price",
        precision: DATA.precision,
        minMove: Math.pow(10, -DATA.precision),
      }},
    }});
    series.setData(DATA.candles);

    const volSeries = chart.addHistogramSeries({{
      priceFormat: {{ type: "volume" }},
      priceScaleId: "",
    }});
    volSeries.priceScale().applyOptions({{ scaleMargins: {{ top: 0.84, bottom: 0 }} }});
    volSeries.setData(
      DATA.candles.map((c) => ({{
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(16, 185, 129, 0.24)" : "rgba(239, 68, 68, 0.24)",
      }}))
    );

    for (const line of DATA.priceLines) {{
      series.createPriceLine({{
        price: line.price,
        color: line.color,
        lineWidth: 2,
        lineStyle: line.style,
        axisLabelVisible: true,
        title: line.title,
      }});
    }}
    chart.timeScale().fitContent();
    window.__ATLAS_CHART_READY__ = true;
  </script>
</body>
</html>
"""


def lint_tweets(tweets: list[str], max_chars: int = 280) -> dict[str, Any]:
    """Validate tweet(s) against Standard-tier length and Anti-AI CT quality rules."""
    errors: list[str] = []
    if not tweets:
        return {"ok": False, "errors": ["No tweets provided."], "tweet_lengths": []}
    if len(tweets) > 4:
        errors.append(f"Thread too long ({len(tweets)} tweets); cap at 4 tweets max.")

    lengths: list[int] = []
    for idx, raw in enumerate(tweets, start=1):
        text = raw.strip()
        lengths.append(len(text))
        prefix = f"Tweet {idx}"
        if not text:
            errors.append(f"{prefix}: empty text.")
            continue
        if len(text) > max_chars:
            errors.append(f"{prefix}: {len(text)} chars exceeds Standard {max_chars}-char cap.")
        if _HASHTAG_RE.search(text):
            errors.append(f"{prefix}: contains hashtag(s); remove all hashtags.")
        cashtags = _CASHTAG_RE.findall(text)
        if len(cashtags) > 2:
            errors.append(f"{prefix}: {len(cashtags)} cashtags ({cashtags}); max 2 per tweet.")
        if idx == 1 and _URL_RE.search(text):
            errors.append(f"{prefix}: contains an external link; never put links in Tweet 1.")
        if _THREAD_NUM_RE.search(text):
            errors.append(f"{prefix}: contains '1/N' thread numbering; keep threads natural.")

        emojis = _EMOJI_RE.findall(text)
        if len(emojis) > 1:
            errors.append(f"{prefix}: {len(emojis)} emojis ({''.join(emojis)}); cap at 0-1.")
        bad_emojis = [e for e in emojis if e in SHILL_EMOJIS]
        if bad_emojis:
            errors.append(f"{prefix}: contains shill/bot emoji(s) {bad_emojis}; remove them.")

        if text.count("—") > 1:
            errors.append(f"{prefix}: multiple em-dashes (—) detected; rewrite naturally.")

        lower = text.lower()
        for phrase in BANNED_AI_PHRASES:
            if phrase in lower:
                errors.append(f"{prefix}: banned AI/shill phrase detected: {phrase!r}.")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "tweet_count": len(tweets),
        "tweet_lengths": lengths,
    }


async def _fetch_from_portal_if_fresh(portal_url: str, max_age_minutes: float) -> dict[str, Any] | None:
    url = f"{portal_url.rstrip('/')}/api/screener"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("data_status") != "current" or data.get("stale") is True or data.get("is_scanning") is True:
        return None
    ts_raw = data.get("timestamp")
    try:
        ts = datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except Exception:
        return None
    age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
    if age_min > max_age_minutes:
        return None
    return data


async def fetch_live_radar_payload(portal_url: str, max_age_minutes: float, force_live: bool = False) -> tuple[dict[str, Any], str]:
    if not force_live:
        cached = await _fetch_from_portal_if_fresh(portal_url, max_age_minutes)
        if cached is not None:
            return cached, "portal_api_fresh"

    from algotrader.api import portal

    portal.warm_screener_cache(REPO_ROOT / "data" / "screener_latest.json")
    try:
        result = await portal._do_crypto_screen(mode="all", min_score=50.0, max_symbols=320)
    finally:
        runtime = getattr(portal, "_shared_runtime", None)
        if runtime is not None and hasattr(runtime, "close"):
            with contextlib.suppress(Exception):
                await runtime.close()
            portal._shared_runtime = None
    return result, "direct_live_scan"


def render_chart_png_via_chrome(html_path: Path, png_path: Path) -> bool:
    """Capture a crisp 1200x675 PNG of the chart HTML using local Google Chrome headless."""
    chrome_candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    chrome_bin: str | None = None
    for cand in chrome_candidates:
        if cand.exists():
            chrome_bin = str(cand)
            break
    if chrome_bin is None:
        chrome_bin = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome_bin is None:
        return False
    cmd = [
        chrome_bin,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1200,675",
        "--virtual-time-budget=4000",
        f"--screenshot={png_path.resolve()}",
        html_path.resolve().as_uri(),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25, check=False)
        return proc.returncode == 0 and png_path.exists() and png_path.stat().st_size > 1024
    except Exception:
        return False


def _cmd_fetch(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload, source = asyncio.run(
        fetch_live_radar_payload(
            portal_url=args.portal_url,
            max_age_minutes=args.max_age_minutes,
            force_live=args.force_live,
        )
    )
    history = load_post_history(Path(args.history_path))
    brief, featured = build_daily_brief(payload, history, events_dir=Path(args.events_dir))
    brief["source"] = source

    chart_html_path: Path | None = None
    if featured is not None and (featured.get("candles") or []):
        chart_html_path = out_dir / "latest_chart.html"
        chart_html_path.write_text(
            render_social_chart_html(featured, macro_narrative=str(brief.get("macro_narrative") or "")),
            encoding="utf-8",
        )
        chart_png_path = out_dir / "latest_chart.png"
        png_ok = render_chart_png_via_chrome(chart_html_path, chart_png_path)
        brief["chart_html_path"] = str(chart_html_path.resolve())
        brief["chart_png_path"] = str(chart_png_path.resolve()) if png_ok else None
    else:
        brief["chart_html_path"] = None
        brief["chart_png_path"] = None

    brief_path = out_dir / "latest_brief.json"
    brief_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    brief["brief_path"] = str(brief_path.resolve())
    print(json.dumps(brief, indent=2, ensure_ascii=False))
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    if args.tweets_file:
        tweets = json.loads(Path(args.tweets_file).read_text(encoding="utf-8"))
    else:
        tweets = json.loads(args.tweets_json)
    if not isinstance(tweets, list) or not all(isinstance(t, str) for t in tweets):
        print(json.dumps({"ok": False, "errors": ["Expected a JSON array of strings."]}))
        return 1
    result = lint_tweets(tweets, max_chars=args.max_chars)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def render_draft_preview_html(
    brief: dict[str, Any],
    tweets: list[str],
    chart_png_path: Path | None = None,
) -> str:
    """Render a self-contained dark-mode Ready-to-Post X Draft Card with 1-click Copy & X Compose Intent."""
    macro = html.escape(str(brief.get("macro_narrative") or "Live Atlas Radar"))
    fmt = html.escape(str(brief.get("recommended_format") or "single_post").replace("_", " ").upper())
    angle = html.escape(str(brief.get("recommended_angle") or "").replace("_", " "))
    gen_at = html.escape(str(brief.get("generated_at") or "")[:19].replace("T", " ") + " UTC")
    total_5 = int(brief.get("total_verified_5_of_5") or 0)

    chart_data_uri = ""
    chart_path_str = ""
    if chart_png_path is not None and chart_png_path.exists():
        b64 = base64.b64encode(chart_png_path.read_bytes()).decode("ascii")
        chart_data_uri = f"data:image/png;base64,{b64}"
        chart_path_str = str(chart_png_path.resolve())

    tweet1_intent = (
        f"https://x.com/intent/post?text={quote(tweets[0].strip(), safe='')}"
        if tweets
        else "https://x.com/compose/post"
    )

    cards_html: list[str] = []
    for idx, raw_t in enumerate(tweets, start=1):
        t = raw_t.strip()
        length = len(t)
        badge_cls = "ok" if length <= 280 else "err"
        escaped_text = html.escape(t)
        intent_btn = (
            f'<a class="btn btn-primary" href="{tweet1_intent}" target="_blank" rel="noopener">Open Tweet 1 in X Compose ↗</a>'
            if idx == 1
            else ""
        )
        cards_html.append(f"""
        <div class="tweet-card">
          <div class="tweet-head">
            <span class="tweet-label">Tweet {idx} of {len(tweets)}</span>
            <span class="char-badge {badge_cls}">{length} / 280 chars</span>
          </div>
          <pre class="tweet-body" id="tweet-{idx}">{escaped_text}</pre>
          <div class="tweet-actions">
            <button class="btn" onclick="copyText('tweet-{idx}', this)">Copy Tweet {idx}</button>
            {intent_btn}
          </div>
        </div>
        """)

    chart_section = ""
    if chart_data_uri:
        escaped_path = html.escape(chart_path_str)
        chart_section = f"""
        <div class="section">
          <div class="section-head">
            <h2>Annotated Chart Attachment (Tweet 1)</h2>
            <div class="tweet-actions">
              <button class="btn btn-primary" onclick="copyChart(this)">Copy Chart Image (Cmd+V into X)</button>
              <a class="btn" href="{chart_data_uri}" download="atlas_radar_chart.png">Download PNG</a>
            </div>
          </div>
          <img id="chart-img" class="chart-preview" src="{chart_data_uri}" alt="Atlas Radar Chart" />
          <div class="path-note">Saved at: <code>{escaped_path}</code></div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Atlas 9AM CET — Ready-to-Post X Draft</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #070b12;
      color: #f1f5f9;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
      padding: 28px 20px 60px;
      line-height: 1.5;
    }}
    .wrap {{ max-width: 860px; margin: 0 auto; }}
    .top {{
      display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
      border-bottom: 1px solid #1e293b; padding-bottom: 16px; margin-bottom: 22px; flex-wrap: wrap;
    }}
    h1 {{ font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }}
    .sub {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
    .badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
    .badge {{
      font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 999px;
      background: #0f172a; border: 1px solid #1e293b; color: #cbd5e1; text-transform: uppercase;
    }}
    .badge.green {{ color: #10b981; border-color: rgba(16,185,129,0.4); background: rgba(16,185,129,0.1); }}
    .section {{
      background: #0d1422; border: 1px solid #1e293b; border-radius: 12px;
      padding: 18px; margin-bottom: 18px;
    }}
    .section-head {{
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
      margin-bottom: 12px; flex-wrap: wrap;
    }}
    h2 {{ font-size: 14px; font-weight: 700; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.5px; }}
    .chart-preview {{ width: 100%; border-radius: 8px; border: 1px solid #1e293b; display: block; }}
    .path-note {{ font-size: 12px; color: #64748b; margin-top: 8px; }}
    .path-note code {{ color: #94a3b8; }}
    .tweet-card {{
      background: #0d1422; border: 1px solid #1e293b; border-radius: 12px;
      padding: 16px 18px; margin-bottom: 14px;
    }}
    .tweet-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
    .tweet-label {{ font-size: 12px; font-weight: 700; color: #94a3b8; text-transform: uppercase; }}
    .char-badge {{ font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; font-family: ui-monospace, monospace; }}
    .char-badge.ok {{ background: rgba(16,185,129,0.14); color: #10b981; }}
    .char-badge.err {{ background: rgba(239,68,68,0.18); color: #ef4444; }}
    .tweet-body {{
      white-space: pre-wrap; font-family: inherit; font-size: 15px; color: #f8fafc;
      background: #070b12; border: 1px solid #172133; border-radius: 8px; padding: 14px; margin-bottom: 12px;
    }}
    .tweet-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .btn {{
      appearance: none; border: 1px solid #334155; background: #1e293b; color: #f8fafc;
      font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 8px;
      cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
    }}
    .btn:hover {{ background: #334155; }}
    .btn-primary {{ background: #1d9bf0; border-color: #1d9bf0; color: #fff; }}
    .btn-primary:hover {{ background: #1a8cd8; }}
    .steps {{ font-size: 13px; color: #94a3b8; padding-left: 18px; }}
    .steps li {{ margin-bottom: 4px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>Atlas 9:00 AM CET — Ready-to-Post X Draft</h1>
        <div class="sub">{macro} · Generated {gen_at}</div>
        <div class="badges">
          <span class="badge green">{total_5} Verified 5/5 Radar Signals</span>
          <span class="badge">{fmt}</span>
          <span class="badge">Angle: {angle}</span>
          <span class="badge">Anti-AI Lint Passed</span>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>15-Second Publish Workflow</h2>
      <ol class="steps" style="margin-top: 8px;">
        <li>Click <b>Open Tweet 1 in X Compose ↗</b> (opens in your logged-in Chrome with Tweet 1 pre-filled).</li>
        <li>Click <b>Copy Chart Image</b> below and press <b>Cmd+V</b> in X Compose to attach the 16:9 chart.</li>
        <li>If posting a mini-thread, click <b>+</b> in X Compose and paste Tweets 2–{len(tweets)} using the Copy buttons below.</li>
      </ol>
    </div>

    {chart_section}

    {"".join(cards_html)}
  </div>

  <script>
    async function copyText(id, btn) {{
      const txt = document.getElementById(id).textContent;
      await navigator.clipboard.writeText(txt);
      const orig = btn.textContent;
      btn.textContent = "Copied ✓";
      setTimeout(() => (btn.textContent = orig), 1600);
    }}
    async function copyChart(btn) {{
      const orig = btn.textContent;
      try {{
        const res = await fetch(document.getElementById("chart-img").src);
        const blob = await res.blob();
        await navigator.clipboard.write([new ClipboardItem({{ "image/png": blob }})]);
        btn.textContent = "Chart Copied ✓ (Cmd+V in X)";
      }} catch (err) {{
        btn.textContent = "Use Download PNG instead";
      }}
      setTimeout(() => (btn.textContent = orig), 2200);
    }}
  </script>
</body>
</html>
"""


def _cmd_render_preview(args: argparse.Namespace) -> int:
    brief = json.loads(Path(args.brief_file).read_text(encoding="utf-8"))
    tweets = json.loads(Path(args.tweets_file).read_text(encoding="utf-8"))
    if not isinstance(tweets, list) or not all(isinstance(t, str) for t in tweets):
        print(json.dumps({"ok": False, "error": "tweets-file must be a JSON array of strings"}))
        return 1
    lint = lint_tweets(tweets, max_chars=280)
    if not lint["ok"]:
        print(json.dumps({"ok": False, "lint_errors": lint["errors"]}))
        return 1
    chart_png = Path(args.chart_png) if args.chart_png else None
    if chart_png is None and brief.get("chart_png_path"):
        chart_png = Path(str(brief["chart_png_path"]))
    out_path = Path(args.out_html)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_draft_preview_html(brief, tweets, chart_png), encoding="utf-8")
    print(json.dumps({"ok": True, "preview_html_path": str(out_path.resolve()), "tweet_count": len(tweets)}, indent=2))
    return 0


def _cmd_log(args: argparse.Namespace) -> int:
    tweets: list[str] = []
    if args.tweets_file:
        raw = json.loads(Path(args.tweets_file).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            tweets = [str(t) for t in raw]
    symbols = [s.strip().upper().lstrip("$") for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "status": args.status,
        "format": args.format,
        "angle": args.angle,
        "symbols": symbols,
        "tweets": tweets,
        "chart_png": args.chart_png,
        "note": args.note,
    }
    append_post_history(entry, Path(args.history_path))
    print(json.dumps({"logged": True, "history_path": str(Path(args.history_path).resolve()), "entry": entry}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live 5/5 Radar -> X.com daily brief, chart & anti-AI linter.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch live 5/5 Radar signals and generate social brief + chart HTML.")
    p_fetch.add_argument("--portal-url", default="http://127.0.0.1:8000")
    p_fetch.add_argument("--max-age-minutes", type=float, default=45.0)
    p_fetch.add_argument("--force-live", action="store_true")
    p_fetch.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p_fetch.add_argument("--history-path", default=str(DEFAULT_HISTORY_PATH))
    p_fetch.add_argument("--events-dir", default=str(DEFAULT_RADAR_EVENTS_DIR))
    p_fetch.set_defaults(func=_cmd_fetch)

    p_lint = sub.add_parser("lint", help="Validate drafted tweet(s) against 280-char and Anti-AI CT rules.")
    g = p_lint.add_mutually_exclusive_group(required=True)
    g.add_argument("--tweets-json", help="JSON array of tweet strings.")
    g.add_argument("--tweets-file", help="Path to JSON file containing an array of tweet strings.")
    p_lint.add_argument("--max-chars", type=int, default=280)
    p_lint.set_defaults(func=_cmd_lint)

    p_preview = sub.add_parser("render-preview", help="Build a local 1-click Ready-to-Post HTML Draft Card.")
    p_preview.add_argument("--brief-file", default=str(DEFAULT_OUT_DIR / "latest_brief.json"))
    p_preview.add_argument("--tweets-file", default=str(DEFAULT_OUT_DIR / "latest_tweets.json"))
    p_preview.add_argument("--chart-png", default="")
    p_preview.add_argument("--out-html", default=str(DEFAULT_OUT_DIR / "draft_preview.html"))
    p_preview.set_defaults(func=_cmd_render_preview)

    p_log = sub.add_parser("log", help="Append drafted/skipped run record to post history.")
    p_log.add_argument("--status", required=True, choices=["drafted", "posted", "skipped", "blocked"])
    p_log.add_argument("--format", required=True)
    p_log.add_argument("--angle", default="")
    p_log.add_argument("--symbols", default="")
    p_log.add_argument("--tweets-file", default="")
    p_log.add_argument("--chart-png", default="")
    p_log.add_argument("--note", default="")
    p_log.add_argument("--history-path", default=str(DEFAULT_HISTORY_PATH))
    p_log.set_defaults(func=_cmd_log)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
