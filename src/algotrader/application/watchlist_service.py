import asyncio
import contextlib
import os
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock

from algotrader.domain.crypto_screener_models import calculate_opportunity_win_rate_and_ev
from algotrader.domain.indicators import rsi, volume_contraction_metrics
from algotrader.domain.models import Candle, DerivativesSnapshot, Instrument
from algotrader.domain.ports import MarketDataPort
from algotrader.domain.watchlist_models import (
    AuditStatus,
    WatchlistItem,
    WatchlistLiveAudit,
    WatchlistPriority,
    WyckoffStage,
)


@dataclass(frozen=True, slots=True)
class _CachedSymbolTelemetry:
    live_price: Decimal | None
    c1h: tuple[Candle, ...]
    c4h: tuple[Candle, ...]
    taker_ratio: Decimal | None
    fetched_at: datetime
    observed_at: datetime | None = None
    flow_observed_at: datetime | None = None


class WatchlistService:
    """Manages persistent watchlists of high-probability crypto setups for coming days."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        raw_path = db_path or os.environ.get("ATLAS_STATE_DB_PATH", "atlas_state.sqlite3")
        self._db_path = Path(raw_path)
        if str(self._db_path.parent) not in (".", ""):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._audit_lock = asyncio.Lock()
        self._audit_cache: dict[str, WatchlistLiveAudit] = {}
        self._audit_cache_time: datetime | None = None
        self._telemetry_cache: dict[str, _CachedSymbolTelemetry] = {}
        self._telemetry_inflight: dict[str, asyncio.Task[_CachedSymbolTelemetry]] = {}
        self._price_map_cache: dict[str, Decimal] = {}
        self._price_map_cache_time: datetime | None = None
        self._ensure_table()
        self._seed_if_empty()

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        create_sql = """
            CREATE TABLE IF NOT EXISTS portal_watchlist (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sector TEXT NOT NULL,
                stage TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                current_price TEXT NOT NULL,
                trigger_price TEXT NOT NULL,
                support_price TEXT NOT NULL,
                invalidation_price TEXT NOT NULL,
                target_1 TEXT NOT NULL,
                target_2 TEXT NOT NULL,
                distance_to_trigger_pct TEXT NOT NULL,
                score TEXT NOT NULL,
                thesis TEXT NOT NULL,
                checklist_passed INTEGER NOT NULL,
                checklist_total INTEGER NOT NULL,
                priority TEXT NOT NULL,
                notes TEXT NOT NULL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """
        # SQLite owns WAL recovery and shared-memory coordination. A failed open
        # must preserve sidecars that another process may still be using.
        with self._lock, self._get_connection() as conn:
            conn.execute(create_sql)
            conn.commit()

    def _seed_if_empty(self) -> None:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) as cnt FROM portal_watchlist")
            count = cur.fetchone()["cnt"]
            if count > 0:
                return

            # Seed institutional pre-breakout watch candidates
            seeds = [
                WatchlistItem(
                    symbol="ONTUSDT",
                    name="Ontology",
                    sector="L1 / Identity",
                    stage=WyckoffStage.PHASE_C_SPRING,
                    timeframe="1–3 Days",
                    current_price=Decimal("0.06172"),
                    trigger_price=Decimal("0.06380"),
                    support_price=Decimal("0.05935"),
                    invalidation_price=Decimal("0.05907"),
                    target_1=Decimal("0.06850"),
                    target_2=Decimal("0.07845"),
                    distance_to_trigger_pct=Decimal("3.37"),
                    score=Decimal("63.3"),
                    thesis="Wyckoff Phase C test holding 4H EMA50 ($0.0592) & 30d POC ($0.0593). Perfect 5/5 Institutional Grade A+ pass.",
                    checklist_passed=5,
                    checklist_total=5,
                    priority=WatchlistPriority.HIGH,
                    notes="Watch 4H close above $0.0638 ceiling for markup ignition. Funding is cheap (+0.44 bps).",
                ),
                WatchlistItem(
                    symbol="COMPUSDT",
                    name="Compound",
                    sector="DeFi Lending",
                    stage=WyckoffStage.VCP_CONTRACTION,
                    timeframe="1–3 Days",
                    current_price=Decimal("23.93"),
                    trigger_price=Decimal("25.80"),
                    support_price=Decimal("23.40"),
                    invalidation_price=Decimal("22.45"),
                    target_1=Decimal("25.80"),
                    target_2=Decimal("28.50"),
                    distance_to_trigger_pct=Decimal("7.81"),
                    score=Decimal("60.5"),
                    thesis="Multi-week base holding above 30d POC ($23.40). Bollinger Bandwidth contracted with positive smart money absorption.",
                    checklist_passed=5,
                    checklist_total=5,
                    priority=WatchlistPriority.HIGH,
                    notes="Orderbook shows absorption at $23.40 support. Target 1 offers 3.5:1 R:R.",
                ),
                WatchlistItem(
                    symbol="JSTUSDT",
                    name="JUST",
                    sector="TRON DeFi",
                    stage=WyckoffStage.COIL_IMMINENT,
                    timeframe="1–2 Days",
                    current_price=Decimal("0.14228"),
                    trigger_price=Decimal("0.14400"),
                    support_price=Decimal("0.13650"),
                    invalidation_price=Decimal("0.14020"),
                    target_1=Decimal("0.16290"),
                    target_2=Decimal("0.19590"),
                    distance_to_trigger_pct=Decimal("1.21"),
                    score=Decimal("65.1"),
                    thesis="Ultra-tight 72h range (4.9% height) riding on top of 30d VAH ($0.1356). Resting right beneath breakout trigger.",
                    checklist_passed=4,
                    checklist_total=5,
                    priority=WatchlistPriority.HIGH,
                    notes="Taker aggression ratio is 2.11x (active spot buyers). Tightly coiled spring.",
                ),
                WatchlistItem(
                    symbol="APTUSDT",
                    name="Aptos",
                    sector="L1 Infra / Move",
                    stage=WyckoffStage.PHASE_B_ABSORPTION,
                    timeframe="3–5 Days",
                    current_price=Decimal("8.45"),
                    trigger_price=Decimal("8.95"),
                    support_price=Decimal("8.15"),
                    invalidation_price=Decimal("7.85"),
                    target_1=Decimal("10.20"),
                    target_2=Decimal("12.40"),
                    distance_to_trigger_pct=Decimal("5.92"),
                    score=Decimal("58.2"),
                    thesis="Phase B absorption of overhead supply. Volume contracted 35% on down days while holding 4H EMA200 support.",
                    checklist_passed=4,
                    checklist_total=5,
                    priority=WatchlistPriority.MEDIUM,
                    notes="Allow base to complete one more contraction before placing limit trigger at $8.95.",
                ),
                WatchlistItem(
                    symbol="SUIUSDT",
                    name="Sui Network",
                    sector="Move L1 Ecosystem",
                    stage=WyckoffStage.POC_RETEST,
                    timeframe="3–5 Days",
                    current_price=Decimal("2.14"),
                    trigger_price=Decimal("2.28"),
                    support_price=Decimal("2.02"),
                    invalidation_price=Decimal("1.95"),
                    target_1=Decimal("2.58"),
                    target_2=Decimal("3.10"),
                    distance_to_trigger_pct=Decimal("6.54"),
                    score=Decimal("57.5"),
                    thesis="Retesting 30-day POC level with rising open interest and neutral funding rate. Forming higher low on 4H.",
                    checklist_passed=4,
                    checklist_total=5,
                    priority=WatchlistPriority.WATCH,
                    notes="Watch for volume ignition spike on 1H chart to confirm springboard off POC.",
                ),
            ]

            for s in seeds:
                conn.execute(
                    """
                    INSERT INTO portal_watchlist (
                        symbol, name, sector, stage, timeframe, current_price, trigger_price,
                        support_price, invalidation_price, target_1, target_2, distance_to_trigger_pct,
                        score, thesis, checklist_passed, checklist_total, priority, notes, added_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s.symbol,
                        s.name,
                        s.sector,
                        str(s.stage),
                        s.timeframe,
                        str(s.current_price),
                        str(s.trigger_price),
                        str(s.support_price),
                        str(s.invalidation_price),
                        str(s.target_1),
                        str(s.target_2),
                        str(s.distance_to_trigger_pct),
                        str(s.score),
                        s.thesis,
                        s.checklist_passed,
                        s.checklist_total,
                        s.priority.value,
                        s.notes,
                        s.added_at.isoformat(),
                        s.updated_at.isoformat(),
                    ),
                )
            conn.commit()

    def list_items(self) -> list[WatchlistItem]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM portal_watchlist ORDER BY score DESC, added_at DESC")
            items = []
            for row in cur.fetchall():
                score_d = Decimal(row["score"])
                win_rate, _ = calculate_opportunity_win_rate_and_ev(
                    setup_label=row["stage"],
                    score=score_d,
                    checklist_passed=row["checklist_passed"],
                    checklist_total=row["checklist_total"],
                    risk_reward_ratio=Decimal("3.2"),
                )
                items.append(
                    WatchlistItem(
                        symbol=row["symbol"],
                        name=row["name"],
                        sector=row["sector"],
                        stage=row["stage"],
                        timeframe=row["timeframe"],
                        current_price=Decimal(row["current_price"]),
                        trigger_price=Decimal(row["trigger_price"]),
                        support_price=Decimal(row["support_price"]),
                        invalidation_price=Decimal(row["invalidation_price"]),
                        target_1=Decimal(row["target_1"]),
                        target_2=Decimal(row["target_2"]),
                        distance_to_trigger_pct=Decimal(row["distance_to_trigger_pct"]),
                        score=score_d,
                        thesis=row["thesis"],
                        checklist_passed=row["checklist_passed"],
                        checklist_total=row["checklist_total"],
                        priority=WatchlistPriority(row["priority"]) if row["priority"] in WatchlistPriority.__members__ else WatchlistPriority.HIGH,
                        notes=row["notes"],
                        added_at=datetime.fromisoformat(row["added_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                        estimated_win_rate_pct=win_rate,
                    )
                )
            return items

    def get_item(self, symbol: str) -> WatchlistItem | None:
        sym_clean = symbol.upper()
        items = self.list_items()
        return next((i for i in items if i.symbol.upper() == sym_clean), None)

    def add_or_update(self, item: WatchlistItem) -> WatchlistItem:
        with self._lock, self._get_connection() as conn:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                """
                INSERT INTO portal_watchlist (
                    symbol, name, sector, stage, timeframe, current_price, trigger_price,
                    support_price, invalidation_price, target_1, target_2, distance_to_trigger_pct,
                    score, thesis, checklist_passed, checklist_total, priority, notes, added_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    current_price=excluded.current_price,
                    trigger_price=excluded.trigger_price,
                    support_price=excluded.support_price,
                    invalidation_price=excluded.invalidation_price,
                    target_1=excluded.target_1,
                    target_2=excluded.target_2,
                    distance_to_trigger_pct=excluded.distance_to_trigger_pct,
                    score=excluded.score,
                    thesis=excluded.thesis,
                    checklist_passed=excluded.checklist_passed,
                    checklist_total=excluded.checklist_total,
                    priority=excluded.priority,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    item.symbol,
                    item.name,
                    item.sector,
                    str(item.stage),
                    item.timeframe,
                    str(item.current_price),
                    str(item.trigger_price),
                    str(item.support_price),
                    str(item.invalidation_price),
                    str(item.target_1),
                    str(item.target_2),
                    str(item.distance_to_trigger_pct),
                    str(item.score),
                    item.thesis,
                    item.checklist_passed,
                    item.checklist_total,
                    item.priority.value if isinstance(item.priority, WatchlistPriority) else str(item.priority),
                    item.notes,
                    item.added_at.isoformat(),
                    now,
                ),
            )
            conn.commit()
            return item

    def delete_item(self, symbol: str) -> bool:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("DELETE FROM portal_watchlist WHERE symbol = ?", (symbol.upper(),))
            conn.commit()
            return cur.rowcount > 0

    def update_notes(self, symbol: str, notes: str) -> bool:
        with self._lock, self._get_connection() as conn:
            now = datetime.now(UTC).isoformat()
            cur = conn.execute(
                "UPDATE portal_watchlist SET notes = ?, updated_at = ? WHERE symbol = ?",
                (notes, now, symbol.upper()),
            )
            conn.commit()
            return cur.rowcount > 0

    def audit_item(
        self,
        item: WatchlistItem,
        live_price: Decimal | None,
        closed_1h: Sequence[Candle],
        closed_4h: Sequence[Candle],
        derivatives: DerivativesSnapshot | None,
        *,
        observed_at: datetime | None = None,
    ) -> WatchlistLiveAudit:
        """Runs a real-time institutional gate diagnosis against verified Binance data.

        Security invariant (W1): Missing market telemetry must NEVER fabricate
        hardcoded prices/indicators or stamp a fresh observation timestamp.
        """
        now = datetime.now(UTC)
        def recent(at: datetime | None, seconds: float) -> bool:
            return at is not None and at.tzinfo is not None and -5 <= (now - at).total_seconds() <= seconds

        def valid_history(bars: Sequence[Candle], minimum: int, hours: int) -> bool:
            expected_sec = hours * 3600
            return len(bars) >= minimum and all(
                b.closed and b.opened_at.tzinfo is not None and b.closed_at.tzinfo is not None
                and b.closed_at > b.opened_at and b.closed_at <= now
                and abs((b.closed_at - b.opened_at).total_seconds() - expected_sec) <= 2.0
                and all(v.is_finite() and v > 0 for v in (b.open, b.high, b.low, b.close))
                and b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
                for b in bars
            ) and recent(bars[-1].closed_at, expected_sec + 300) and all(
                timedelta(0) <= right.opened_at - left.closed_at <= timedelta(milliseconds=1)
                for left, right in zip(bars, bars[1:], strict=False)
            )

        valid_live = live_price is not None and live_price.is_finite() and live_price > 0 and recent(observed_at, 60)
        complete = valid_history(closed_1h, 48, 1) and valid_history(closed_4h, 15, 4) and (
            derivatives is not None and recent(derivatives.observed_at, 7200)
            and derivatives.taker_buy_sell_ratio is not None and derivatives.taker_buy_sell_ratio.is_finite()
            and derivatives.taker_buy_sell_ratio > 0
        )
        if not valid_live or not complete:
            return WatchlistLiveAudit(
                symbol=item.symbol,
                status=AuditStatus.DATA_UNAVAILABLE,
                status_title="Market Telemetry Unavailable",
                live_price=live_price if valid_live else None,
                price_change_pct=(
                    ((live_price - item.current_price) / item.current_price) * Decimal("100")
                    if (valid_live and live_price is not None and item.current_price > 0)
                    else None
                ),
                current_rsi_1h=None,
                current_rsi_4h=None,
                taker_ratio=None,
                vol_contraction_ratio=None,
                gate_tripped="Required price/history/flow evidence missing or stale; gates unverified.",
                live_reading=(
                    f"Live Price: ${live_price:.4f} | Candles unavailable"
                    if valid_live and live_price is not None
                    else "Live Price & Candles unavailable"
                ),
                what_happened=(
                    f"Real-time Binance USD-M telemetry for {item.symbol} could not be verified. "
                    "Saved reference levels are preserved without fabricating an audit verdict."
                ),
                institutional_logic=(
                    "Audit status is explicitly marked unavailable rather than substituting fallback "
                    "constants or user reference prices."
                ),
                audited_at=observed_at if valid_live else None,
                telemetry_available=False,
            )

        assert live_price is not None
        base_price = item.current_price if item.current_price > 0 else live_price
        price_change_pct = ((live_price - base_price) / base_price) * Decimal("100")

        # 24h return from candles if available
        ret_24h_pct = price_change_pct
        if len(closed_1h) >= 24 and closed_1h[-24].close > 0:
            ret_24h_pct = ((live_price - closed_1h[-24].close) / closed_1h[-24].close) * Decimal("100")

        # Technical indicators (never fabricate hardcoded defaults)
        closes_1h = [c.close for c in closed_1h]
        closes_4h = [c.close for c in closed_4h]
        rsi_1h = rsi(closes_1h, 14)[-1]
        rsi_4h = rsi(closes_4h, 14)[-1]

        # Volume contraction
        vol_contraction_ratio: Decimal | None = None
        if len(closed_1h) >= 48:
            _, _, vc = volume_contraction_metrics(
                closed_1h,
                min(7, max(1, len(closed_1h) // 24)),
                min(14, max(2, len(closed_1h) // 24)),
            )
            vol_contraction_ratio = vc

        # Taker ratio
        taker_ratio = derivatives.taker_buy_sell_ratio if derivatives else None

        taker_str = f"{taker_ratio:.3f}" if taker_ratio is not None else "N/A"
        vol_str = f"{vol_contraction_ratio:.3f}" if vol_contraction_ratio is not None else "N/A"

        # Check setup direction
        is_short = (
            "short" in str(item.stage).lower()
            or "utad" in str(item.stage).lower()
            or "breakdown" in str(item.stage).lower()
            or "short" in item.thesis.lower()
            or item.target_1 < item.current_price
        )

        if not is_short:
            # --- LONG EVALUATION ---
            # 1. Target 1 reached
            if live_price >= item.target_1:
                status = AuditStatus.TARGET_REACHED
                status_title = "Target 1 Reached: Active Profit Expansion"
                what_happened = (
                    f"Setup expanded to Target 1 (${item.target_1:.4f}). "
                    f"Price advanced {price_change_pct:+.2f}% since initially watched."
                )
                gate_tripped = "Profit Target 1 Hit (Exited Pre-Breakout Coil)"
                live_reading = f"Live Price: ${live_price:.4f} >= Target 1: ${item.target_1:.4f} ({price_change_pct:+.2f}%)"
                institutional_logic = (
                    "Institutional Target 1 hit. Standard Execution Playbook: Take 50% profit off the table, "
                    f"trail protective stop to breakeven (${item.support_price:.4f}), and let remaining position ride to Target 2 (${item.target_2:.4f})."
                )

            # 2. Invalidation breached
            elif live_price <= item.invalidation_price:
                status = AuditStatus.STOP_BREACHED
                status_title = "Invalidation Hit: Setup Nullified"
                what_happened = (
                    f"Price broke below institutional invalidation level (${item.invalidation_price:.4f}). "
                    f"Base accumulation failed."
                )
                gate_tripped = "Structural Stop-Loss Breached (Floor Failure)"
                live_reading = f"Live Price: ${live_price:.4f} <= Invalidation: ${item.invalidation_price:.4f} ({price_change_pct:+.2f}%)"
                institutional_logic = (
                    "Hard stop triggered. When base structure violates invalidation support, the thesis is proven false. "
                    "Do not hold or average down. Cut exposure according to strict capital preservation rules."
                )

            # 3. Aggressive Seller Influx
            elif taker_ratio is not None and taker_ratio < Decimal("0.60"):
                status = AuditStatus.SELLER_INFLUX
                status_title = "Aggressive Seller Influx"
                dump_ratio = float(Decimal("1") / taker_ratio) if taker_ratio > 0 else 2.0
                what_happened = (
                    f"Significant market dump aggression on the Binance order book. "
                    f"Aggressive market sellers outnumber buyers ~{dump_ratio:.1f}:1."
                )
                gate_tripped = (
                    "# Gate 6: Institutional Order Flow Collapse\n"
                    "if taker_ratio < Decimal(\"0.85\"):\n"
                    "    return None  # Severe market sell dominance"
                )
                live_reading = f"Taker Buy/Sell Ratio: {taker_str} (< 0.60 severe dump threshold)"
                institutional_logic = (
                    "An asset cannot be classified as an institutional Wyckoff Spring when aggressive market sells "
                    "account for over 60% of all market orders. Smart money is not absorbing resting supply; stand aside."
                )

            # 4. Taker Flow Divergence or Volume Expansion
            elif (taker_ratio is not None and taker_ratio < Decimal("0.85")) or (
                vol_contraction_ratio is not None and vol_contraction_ratio > Decimal("1.25")
            ):
                status = AuditStatus.FLOW_DIVERGENCE
                status_title = "Taker Flow Divergence"
                what_happened = "Aggressive order flow on Binance shifted from absorption to selling, or down-volume expanded."
                gate_tripped = (
                    "# Gate 6 & Gate 5: Flow Divergence & Volume Expansion\n"
                    + (f"Taker Buy/Sell Ratio: {taker_str} (Institutional gate mandates >= 0.85)\n" if taker_ratio is not None and taker_ratio < Decimal("0.85") else "")
                    + (f"Volume Contraction Ratio: {vol_str} (Gate limit <= 1.25)" if vol_contraction_ratio is not None and vol_contraction_ratio > Decimal("1.25") else "")
                ).strip()
                live_reading = f"Taker Buy/Sell Ratio: {taker_str} | Volume Contraction: {vol_str}"
                institutional_logic = (
                    "Smart money absorption requires passive accumulation with taker buyer dominance. "
                    "When taker sell aggression takes over, the coil risks breaking downward. Wait for absorption to resume."
                )

            # 5. Graduated from Pre-Breakout to Active Expansion
            elif rsi_1h > Decimal("65.0") or rsi_4h > Decimal("65.0") or price_change_pct > Decimal("5.0") or ret_24h_pct > Decimal("7.0"):
                status = AuditStatus.ACTIVE_EXPANSION
                status_title = 'Graduated: "Pre-Breakout" to "Active Expansion"'
                what_happened = (
                    f"When initially screened, {item.symbol} was quietly coiling at ${item.current_price:.4f} inside its base. "
                    f"Live on Binance right now, {item.symbol} has advanced to ${live_price:.4f} ({ret_24h_pct:+.2f}% in 24h)."
                )
                gate_tripped = (
                    "# Gate 2: Excursion & Anti-Chasing (Pre-Breakout Rule)\n"
                    "if snap_1h.rsi14 > Decimal(\"65\") or rsi_4h > Decimal(\"65\"):\n"
                    "    return None  # Token is already expanding / no longer a pre-breakout entry!"
                )
                live_reading = f"{item.symbol}'s 1H RSI rose to {rsi_1h:.2f} (> 65.0) | 4H RSI: {rsi_4h:.2f} | Change: {price_change_pct:+.2f}%"
                institutional_logic = (
                    "The screener is strictly a Pre-Breakout Accumulation engine (identifying low-risk entries before momentum ignites). "
                    "Once price pushes and RSI breaks above 65 into overbought territory, buying there violates the Anti-Chasing rule. "
                    "It has transitioned from a pre-breakout coil into an active expansion phase. Trail stop to breakeven or lock partial gains."
                )

            # 6. Coil Holding
            else:
                status = AuditStatus.COIL_HOLDING
                status_title = "Coil Intact: Accumulation Holding"
                what_happened = (
                    f"{item.symbol} remains quietly coiling between ${item.support_price:.4f} support and ${item.trigger_price:.4f} trigger. "
                    f"Live price at ${live_price:.4f} ({price_change_pct:+.2f}%)."
                )
                gate_tripped = "Watchlist price/history/flow checks holding; not a radar qualification"
                live_reading = (
                    f"1H RSI: {rsi_1h:.2f} | 4H RSI: {rsi_4h:.2f} | "
                    f"Taker: {taker_str} | Vol Ratio: {vol_str}"
                )
                institutional_logic = (
                    "Setup remains prime and compressed. Supply is drying up and institutions continue absorbing float. "
                    f"Maintain resting trigger order at ${item.trigger_price:.4f}."
                )

        else:
            # --- SHORT EVALUATION ---
            if live_price <= item.target_1:
                status = AuditStatus.TARGET_REACHED
                status_title = "Short Target 1 Reached: Profit Expansion"
                what_happened = f"Short setup reached Target 1 (${item.target_1:.4f}). Price dropped {price_change_pct:+.2f}%."
                gate_tripped = "Short Target 1 Reached"
                live_reading = f"Live Price: ${live_price:.4f} <= Target 1: ${item.target_1:.4f}"
                institutional_logic = "Cover 50% short exposure, move stop to breakeven, and let remainder run."
            elif live_price >= item.invalidation_price:
                status = AuditStatus.STOP_BREACHED
                status_title = "Short Invalidation Hit: Stop Triggered"
                what_happened = f"Price rallied above invalidation ceiling (${item.invalidation_price:.4f})."
                gate_tripped = "Structural Stop-Loss Breached"
                live_reading = f"Live Price: ${live_price:.4f} >= Invalidation: ${item.invalidation_price:.4f}"
                institutional_logic = "Short thesis invalidated. Cover immediately to prevent short squeeze."
            elif taker_ratio is not None and taker_ratio > Decimal("1.40"):
                status = AuditStatus.FLOW_DIVERGENCE
                status_title = "Aggressive Buyer Influx Against Short"
                what_happened = "Aggressive market buyers stepped in, opposing short breakdown thesis."
                gate_tripped = "Gate 6: Aggressive Buyer Dominance on Short"
                live_reading = f"Taker Buy/Sell Ratio: {taker_str} (> 1.40 buyer influx)"
                institutional_logic = "Smart money is buying liquidity; risk of short squeeze. Reduce position size."
            elif rsi_1h < Decimal("35.0") or rsi_4h < Decimal("35.0") or price_change_pct < Decimal("-5.0"):
                status = AuditStatus.ACTIVE_EXPANSION
                status_title = "Graduated: Active Breakdown Expansion"
                what_happened = f"{item.symbol} broke down from distribution base to ${live_price:.4f} ({price_change_pct:+.2f}%)."
                gate_tripped = "Gate 2: Active Breakdown Excursion (RSI < 35)"
                live_reading = f"1H RSI: {rsi_1h:.2f} (< 35.0) | Price: ${live_price:.4f}"
                institutional_logic = "Breakdown has expanded into oversold territory. Anti-chasing rules apply: do not enter late shorts. Trail stops."
            else:
                status = AuditStatus.COIL_HOLDING
                status_title = "Distribution Breakdown Coil Intact"
                what_happened = f"{item.symbol} is coiling beneath resistance (${item.support_price:.4f})."
                gate_tripped = "Watchlist short price/history/flow checks holding; not a radar qualification"
                live_reading = f"1H RSI: {rsi_1h:.2f} | 4H RSI: {rsi_4h:.2f} | Taker: {taker_str}"
                institutional_logic = "Distribution breakdown coil is intact. Maintain resting trigger order."

        return WatchlistLiveAudit(
            symbol=item.symbol,
            status=status,
            status_title=status_title,
            live_price=live_price,
            price_change_pct=price_change_pct,
            current_rsi_1h=rsi_1h,
            current_rsi_4h=rsi_4h,
            taker_ratio=taker_ratio,
            vol_contraction_ratio=vol_contraction_ratio,
            gate_tripped=gate_tripped,
            live_reading=live_reading,
            what_happened=what_happened,
            institutional_logic=institutional_logic,
            audited_at=observed_at or (closed_1h[-1].closed_at if closed_1h else None),
            telemetry_available=True,
        )

    def audit_refresh_due(self, now: datetime | None = None, *, cooldown_seconds: float = 30.0) -> bool:
        at = now or datetime.now(UTC)
        return self._audit_cache_time is None or (at - self._audit_cache_time).total_seconds() >= cooldown_seconds

    def get_fresh_audit_cache(
        self, now: datetime | None = None, *, max_age_seconds: float = 60.0
    ) -> dict[str, WatchlistLiveAudit]:
        at = now or datetime.now(UTC)
        if (
            self._audit_cache
            and self._audit_cache_time is not None
            and (at - self._audit_cache_time).total_seconds() <= max_age_seconds
        ):
            return self._audit_cache
        return {}

    async def run_live_audit(
        self,
        market_data: MarketDataPort,
        instruments: Sequence[Instrument],
        force_refresh: bool = False,
    ) -> dict[str, WatchlistLiveAudit]:
        """Runs concurrent audits for all watchlist items with bounded server-side cooldown."""
        now = datetime.now(UTC)
        # V4: Enforce bounded server-side cooldown even for authorized force_refresh requests.
        if self._audit_cache and not self.audit_refresh_due(now):
            return self._audit_cache

        async with self._audit_lock:
            now = datetime.now(UTC)
            if self._audit_cache and not self.audit_refresh_due(now):
                return self._audit_cache
            # Reserve cooldown before I/O so failures cannot trigger retry storms.
            self._audit_cache_time = now

            items = self.list_items()
            if not items:
                return {}

            audits: dict[str, WatchlistLiveAudit] = {}
            for offset in range(0, len(items), 25):
                audits.update(await self.run_live_audit_for_items(
                    items[offset:offset + 25], market_data, instruments, force_refresh=False,
                ))

            self._audit_cache = audits
            self._audit_cache_time = datetime.now(UTC)
            return audits

    async def run_live_audit_for_items(
        self,
        items: Sequence[WatchlistItem],
        market_data: MarketDataPort,
        instruments: Sequence[Instrument],
        force_refresh: bool = False,
    ) -> dict[str, WatchlistLiveAudit]:
        """Runs concurrent audits for an arbitrary list of user watchlist items with shared 30s symbol telemetry caching."""
        if not items:
            return {}

        # Deduplicate items by symbol (last item wins) and cap batch to 25 unique symbols
        deduped_by_sym: dict[str, WatchlistItem] = {}
        for item in items:
            sym = item.symbol.upper().strip()
            if sym and len(deduped_by_sym) < 25:
                deduped_by_sym[sym] = item

        if not deduped_by_sym:
            return {}

        now = datetime.now(UTC)
        inst_map = {i.id.symbol: i for i in instruments}
        sem = asyncio.Semaphore(8)

        # Only validated exchange-timestamped futures midpoints may enter shared telemetry.
        live_price_map: dict[str, Decimal] = {}
        quote_times: dict[str, datetime] = {}
        requested = [inst_map[s] for s in deduped_by_sym if s in inst_map]
        if requested:
            with contextlib.suppress(Exception):
                quotes = await asyncio.wait_for(market_data.quotes(requested), timeout=3.0)
                for quote in quotes:
                    sym = quote.instrument_id.symbol
                    if sym in deduped_by_sym and quote.is_valid_book and quote.is_fresh_at(now, max_age_seconds=60):
                        live_price_map[sym] = quote.mid
                        quote_times[sym] = quote.observed_at

        async def _fetch_telemetry_inner(sym: str, inst: Instrument) -> _CachedSymbolTelemetry:
            """Fetch verified exchange telemetry only; NEVER read or cache user-supplied reference prices."""
            cached = self._telemetry_cache.get(sym)
            live_price: Decimal | None = live_price_map.get(sym)
            c1h: Sequence[Candle] = ()
            c4h: Sequence[Candle] = ()
            taker_ratio: Decimal | None = None
            observed_at = quote_times.get(sym)
            flow_observed_at: datetime | None = None

            async with sem:
                with contextlib.suppress(Exception):
                    fetched_1h = await asyncio.wait_for(market_data.candles(inst, "1h", 60), timeout=2.5)
                    closed_1h = [c for c in fetched_1h if c.closed and c.close > 0]
                    if closed_1h:
                        c1h = closed_1h

                with contextlib.suppress(Exception):
                    fetched_4h = await asyncio.wait_for(market_data.candles(inst, "4h", 25), timeout=2.5)
                    c4h = [c for c in fetched_4h if c.closed and c.close > 0]

                if hasattr(market_data, "_client"):
                    try:
                        t_res = await asyncio.wait_for(
                            market_data._client.request(
                                "GET",
                                "/futures/data/takerlongshortRatio",
                                params={"symbol": inst.id.symbol, "period": "1h", "limit": 1},
                            ),
                            timeout=2.0,
                        )
                        if isinstance(t_res, list) and len(t_res) > 0:
                            tr = Decimal(str(t_res[0].get("buySellRatio", "")))
                            flow_time = datetime.fromtimestamp(float(t_res[0]["timestamp"]) / 1000, UTC)
                            if tr.is_finite() and tr > 0 and -5 <= (now - flow_time).total_seconds() <= 7200:
                                taker_ratio = tr
                                flow_observed_at = flow_time
                    except Exception:
                        pass

            # Only persist into the shared cache when actual verified exchange observations were obtained.
            if live_price is not None and c1h:
                telem = _CachedSymbolTelemetry(
                    live_price=live_price,
                    c1h=tuple(c1h),
                    c4h=tuple(c4h),
                    taker_ratio=taker_ratio,
                    fetched_at=datetime.now(UTC),
                    observed_at=observed_at,
                    flow_observed_at=flow_observed_at,
                )
                if len(self._telemetry_cache) >= 512 and sym not in self._telemetry_cache:
                    oldest_key = min(
                        self._telemetry_cache, key=lambda k: self._telemetry_cache[k].fetched_at
                    )
                    self._telemetry_cache.pop(oldest_key, None)
                self._telemetry_cache[sym] = telem
                return telem

            # If live fetch failed, return unavailable (never poison shared cache with user reference prices)
            if cached is not None and (datetime.now(UTC) - cached.fetched_at).total_seconds() < 120.0:
                return cached
            return _CachedSymbolTelemetry(
                live_price=live_price,
                c1h=tuple(c1h),
                c4h=tuple(c4h),
                taker_ratio=taker_ratio,
                fetched_at=now,
                observed_at=observed_at,
                flow_observed_at=flow_observed_at,
            )

        async def _get_or_fetch_telemetry(item: WatchlistItem) -> _CachedSymbolTelemetry:
            sym = item.symbol.upper()
            inst = inst_map.get(sym)
            # Security invariant: unverified / fake symbols not in active exchange instruments
            # never allocate cache entries or trigger external network I/O.
            if inst is None:
                return _CachedSymbolTelemetry(
                    live_price=live_price_map.get(sym),
                    c1h=(),
                    c4h=(),
                    taker_ratio=None,
                    fetched_at=now,
                    observed_at=None,
                )

            cached = self._telemetry_cache.get(sym)
            if (
                cached is not None
                and (datetime.now(UTC) - cached.fetched_at).total_seconds() < 30.0
            ):
                if sym in live_price_map:
                    return replace(cached, live_price=live_price_map[sym], observed_at=quote_times[sym])
                return cached

            # Singleflight coalescing: concurrent callers for the same cold symbol share 1 in-flight task
            existing_task = self._telemetry_inflight.get(sym)
            if existing_task is not None and not existing_task.done():
                return await existing_task

            task = asyncio.create_task(_fetch_telemetry_inner(sym, inst))
            self._telemetry_inflight[sym] = task
            try:
                return await task
            finally:
                if self._telemetry_inflight.get(sym) is task:
                    self._telemetry_inflight.pop(sym, None)

        async def _audit_single_item(item: WatchlistItem) -> tuple[str, WatchlistLiveAudit]:
            telem = await _get_or_fetch_telemetry(item)
            deriv = None
            if telem.taker_ratio is not None:
                deriv = DerivativesSnapshot(
                    funding_rate=Decimal("0.0001"),
                    basis_rate=Decimal("0"),
                    open_interest=Decimal("0"),
                    open_interest_change_1h=None,
                    taker_buy_sell_ratio=telem.taker_ratio,
                    bid_depth_notional=Decimal("100000"),
                    ask_depth_notional=Decimal("100000"),
                    open_interest_change_7d=None,
                    observed_at=telem.flow_observed_at or datetime.min.replace(tzinfo=UTC),
                )

            audit = self.audit_item(
                item,
                telem.live_price,
                telem.c1h,
                telem.c4h,
                deriv,
                observed_at=telem.observed_at,
            )
            return item.symbol, audit

        results = await asyncio.gather(
            *[_audit_single_item(item) for item in deduped_by_sym.values()]
        )
        return {sym: aud for sym, aud in results}

    async def list_items_with_audit(
        self,
        market_data: MarketDataPort,
        instruments: Sequence[Instrument],
        force_refresh: bool = False,
    ) -> list[WatchlistItem]:
        """Returns watchlist items enriched with real-time audit diagnostics and updated prices."""
        audits = await self.run_live_audit(market_data, instruments, force_refresh)
        items = self.list_items()
        enriched: list[WatchlistItem] = []
        for item in items:
            audit = audits.get(item.symbol) or self.audit_item(item, None, [], [], None)
            eff_price = audit.live_price if (audit.live_price is not None and audit.live_price > 0) else item.current_price
            enriched.append(
                replace(
                    item,
                    current_price=eff_price,
                    distance_to_trigger_pct=(
                        abs(item.trigger_price - eff_price) / eff_price * Decimal("100")
                        if eff_price > 0
                        else item.distance_to_trigger_pct
                    ),
                    audit=audit,
                )
            )
        return enriched

    async def enrich_items_with_audit(
        self,
        items: Sequence[WatchlistItem],
        market_data: MarketDataPort,
        instruments: Sequence[Instrument],
        force_refresh: bool = False,
    ) -> list[WatchlistItem]:
        """Enriches user-specific watchlist items with real-time Binance gate audits."""
        audits = await self.run_live_audit_for_items(items, market_data, instruments, force_refresh=force_refresh)
        enriched: list[WatchlistItem] = []
        for item in items:
            audit = audits.get(item.symbol) or self.audit_item(item, None, [], [], None)
            eff_price = audit.live_price if (audit.live_price is not None and audit.live_price > 0) else item.current_price
            enriched.append(
                replace(
                    item,
                    current_price=eff_price,
                    distance_to_trigger_pct=(
                        abs(item.trigger_price - eff_price) / eff_price * Decimal("100")
                        if eff_price > 0
                        else item.distance_to_trigger_pct
                    ),
                    audit=audit,
                )
            )
        return enriched
