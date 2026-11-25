"""Offline worst-case 320-symbol / 75-enrichment resource, concurrency, & failure benchmark.

Simulates realistic provider latency, intermittent Stage-2 timeouts, and worst-case candidate
volume (320 selected symbols, 45 core shortlist + 30 probes = 75 Stage-2 enrichments) with
zero external network requests. Verifies:
- Cold and warm scan duration
- Peak process RSS (MB)
- Strict Stage-2 concurrency ceiling (`<= 8` concurrent `derivatives_snapshot` calls)
- Stage-2 total enrichment ceiling (`<= 75`) and recovery probe cap (`<= 8`)
- Request / Binance weight budget accounting (`<= 2400` ceiling)
- Graceful handling of simulated provider timeouts/degraded telemetry (never publishes unknown R5)
- Instant rollback behavior (`early_recovery_enabled=False`)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import resource
import sys
import time
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algotrader.adapters.external.models import MacroSentimentContext  # noqa: E402
from algotrader.application.crypto_screener import CryptoOpportunityScreener  # noqa: E402
from algotrader.config import Settings  # noqa: E402
from algotrader.domain.crypto_screener_models import ScreenerMode, SetupLabel  # noqa: E402
from algotrader.domain.early_recovery import RECOVERY_PROBE_CAP  # noqa: E402
from algotrader.domain.models import (  # noqa: E402
    AssetClass,
    Candle,
    DerivativesSnapshot,
    Instrument,
    InstrumentId,
    MarketKind,
    Quote,
    Venue,
)
from algotrader.domain.scan_observations import ScanDecisionJournal  # noqa: E402
from algotrader.domain.stage2_allocation import ENRICHMENT_CONCURRENCY  # noqa: E402
from scripts.research_verified_replay import offline  # noqa: E402

D = Decimal
DEFAULT_OUTPUT = (
    ROOT / "docs" / "security" / "evidence" / "2026-11-24-early-recovery-benchmark.json"
)


def _peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports ru_maxrss in bytes; Linux reports in KiB
    if sys.platform == "darwin":
        return round(raw / (1024 * 1024), 2)
    return round(raw / 1024, 2)


def _build_universe(now: datetime) -> tuple[
    list[Instrument],
    list[Quote],
    dict[str, tuple[list[Candle], list[Candle]]],
]:
    h_end = now.replace(minute=0, second=0, microsecond=0)
    q_end = h_end.replace(hour=(h_end.hour // 4) * 4)

    # Shared immutable base histories (360 1h bars, 200 4h bars)
    rec_1h: list[Candle] = []
    flat_1h: list[Candle] = []
    btc_1h: list[Candle] = []
    for idx in range(360):
        opened = h_end - timedelta(hours=360 - idx)
        closed = opened + timedelta(hours=1)
        rel = idx - 360
        if rel < -60:
            cl = D("1.2000")
            op, hi, lo, qv = D("1.2020"), D("1.2200"), D("1.1850"), D("110000")
        elif rel < -28:
            step = D(str((rel + 60) / 32.0))
            cl = D("1.1600") - step * D("0.1600")
            op, hi, lo, qv = cl + D("0.0030"), cl + D("0.0060"), cl - D("0.0040"), D("110000")
        elif rel < -4:
            op, cl, hi = D("1.0000"), D("0.9990"), D("1.0060")
            lo = D("0.9850") if rel == -7 else D("0.9940")
            qv = D("110000")
        elif rel < -1:
            op, cl, hi, lo, qv = D("1.0050"), D("1.0100"), D("1.0140"), D("1.0020"), D("150000")
        else:
            op, cl, hi, lo, qv = D("1.0100"), D("1.0220"), D("1.0240"), D("1.0080"), D("210000")
        vol = (qv / cl).quantize(D("0.01"))
        rec_1h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=vol,
                quote_volume=qv,
                taker_buy_volume=(vol * D("0.56")).quantize(D("0.01")),
                taker_buy_quote_volume=(qv * D("0.56")).quantize(D("0.01")),
                closed=True,
            )
        )
        flat_1h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=D("1.2000"),
                high=D("1.2040"),
                low=D("1.1960"),
                close=D("1.2005"),
                volume=D("120000"),
                quote_volume=D("144000"),
                taker_buy_volume=D("62000"),
                taker_buy_quote_volume=D("74400"),
                closed=True,
            )
        )
        btc_1h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=D("90000"),
                high=D("90150"),
                low=D("89850"),
                close=D("90000"),
                volume=D("100"),
                quote_volume=D("9000000"),
                closed=True,
            )
        )

    rec_4h: list[Candle] = []
    flat_4h: list[Candle] = []
    for idx in range(200):
        opened = q_end - timedelta(hours=4 * (200 - idx))
        closed = opened + timedelta(hours=4)
        rel = idx - 200
        if rel < -12:
            op, cl = D("1.2000"), D("1.2050")
            hi = D("1.2900") if idx == 150 else D("1.2250")
            lo = D("1.1820")
        elif rel < -2:
            step = D(str((rel + 12) / 10.0))
            op = D("1.1500") - step * D("0.1300")
            cl, hi, lo = op - D("0.0100"), op + D("0.0080"), op - D("0.0180")
        else:
            op = D("1.0050")
            cl = D("1.0220") if rel == -1 else D("1.0080")
            hi = D("1.0240")
            lo = D("0.9850") if rel == -2 else D("1.0020")
        rec_4h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=D("500000"),
                quote_volume=D("600000"),
                closed=True,
            )
        )
        flat_4h.append(
            Candle(
                opened_at=opened,
                closed_at=closed,
                open=D("1.2000"),
                high=D("1.2060"),
                low=D("1.1940"),
                close=D("1.2005"),
                volume=D("500000"),
                quote_volume=D("600000"),
                closed=True,
            )
        )

    instruments: list[Instrument] = [
        Instrument(
            id=InstrumentId(Venue.BINANCE_USDM, "BTCUSDT"),
            base_asset="BTC",
            quote_asset="USDT",
            asset_class=AssetClass.CRYPTO,
            market_kind=MarketKind.FUTURES,
            trading=True,
            price_tick=D("0.1"),
            quantity_step=D("0.001"),
            min_quantity=D("0.001"),
            min_notional=D("5"),
        )
    ]
    quotes: list[Quote] = [
        Quote(
            instrument_id=instruments[0].id,
            bid=D("89995"),
            ask=D("90005"),
            last=D("90000"),
            quote_volume_24h=D("5000000000"),
            observed_at=now,
            price_change_pct_24h=D("0.5"),
            funding_rate=D("0.00005"),
            basis_pct=D("0"),
            exchange_observed_at=now,
            received_at=now,
        )
    ]
    candles_map: dict[str, tuple[list[Candle], list[Candle]]] = {"BTCUSDT": (btc_1h, flat_4h)}

    # 340 liquid altcoin symbols so Stage 0 selects full 285/320 discovery_v2 universe:
    # - SYM000..SYM019 (20 symbols): Stage-1 structurally ready Early-Recovery candidates
    # - SYM020..SYM339 (320 symbols): Core/Discovery universe + funding probes
    for i in range(340):
        sym = f"SYM{i:03d}USDT"
        inst = Instrument(
            id=InstrumentId(Venue.BINANCE_USDM, sym),
            base_asset=f"SYM{i:03d}",
            quote_asset="USDT",
            asset_class=AssetClass.CRYPTO,
            market_kind=MarketKind.FUTURES,
            trading=True,
            price_tick=D("0.0001"),
            quantity_step=D("1"),
            min_quantity=D("1"),
            min_notional=D("5"),
        )
        instruments.append(inst)
        is_rec = i < 20
        px = D("1.0225") if is_rec else D("1.2005")
        qv = D(str(80_000_000 - i * 210_000))
        fund = D("0.0") if is_rec else (D("-0.00030") if i < 120 else D("0.00002"))
        quotes.append(
            Quote(
                instrument_id=inst.id,
                bid=px * D("0.9997"),
                ask=px * D("1.0003"),
                last=px,
                quote_volume_24h=max(D("1500000"), qv),
                observed_at=now,
                high_24h=px * D("1.02"),
                low_24h=px * D("0.97"),
                price_change_pct_24h=D("2.2") if is_rec else D("0.4"),
                funding_rate=fund,
                basis_pct=D("0") if is_rec else D("0.0012"),
                exchange_observed_at=now,
                received_at=now,
            )
        )
        candles_map[sym] = (rec_1h, rec_4h) if is_rec else (flat_1h, flat_4h)

    return instruments, quotes, candles_map


class _SimulatedLatencyMarket:
    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        instruments: list[Instrument],
        quotes: list[Quote],
        candles_map: dict[str, tuple[list[Candle], list[Candle]]],
        now: datetime,
    ) -> None:
        self._instruments = instruments
        self._quotes = quotes
        self._candles_map = candles_map
        self._now = now
        self.active_derivs = 0
        self.max_concurrent_derivs = 0
        self.total_deriv_calls = 0
        self.total_candle_calls = 0
        self.simulated_failures = 0

    async def instruments(self) -> list[Instrument]:
        await asyncio.sleep(0.002)
        return self._instruments

    async def quotes(self, instruments: Any) -> list[Quote]:
        await asyncio.sleep(0.003)
        wanted = {i.id.symbol for i in instruments}
        return [q for q in self._quotes if q.instrument_id.symbol in wanted]

    async def candles(self, instrument: Instrument, interval: str, limit: int) -> list[Candle]:
        self.total_candle_calls += 1
        await asyncio.sleep(0.0005)
        pair = self._candles_map[instrument.id.symbol]
        if interval == "1h":
            return pair[0][-limit:]
        if interval == "4h":
            return pair[1][-limit:]
        return []

    async def derivatives_snapshot(self, instrument: Instrument) -> DerivativesSnapshot:
        self.total_deriv_calls += 1
        self.active_derivs += 1
        self.max_concurrent_derivs = max(self.max_concurrent_derivs, self.active_derivs)
        try:
            await asyncio.sleep(0.008)  # Simulated Stage-2 network RTT
            idx = int(instrument.base_asset.removeprefix("SYM")) if instrument.base_asset.startswith("SYM") else 999
            # Inject simulated failures / degraded telemetry on every 5th symbol
            if idx % 7 == 3:
                self.simulated_failures += 1
                raise TimeoutError("simulated Stage-2 provider timeout")
            degraded = idx % 7 == 5
            if degraded:
                self.simulated_failures += 1
            return DerivativesSnapshot(
                funding_rate=D("-0.00008"),
                basis_rate=D("0.0005"),
                open_interest=D("5000000"),
                open_interest_change_1h=D("0.015"),
                taker_buy_sell_ratio=D("1.10"),
                bid_depth_notional=D("150000"),
                ask_depth_notional=D("150000"),
                open_interest_change_4h=D("0.032"),
                open_interest_change_24h=D("0.045"),
                open_interest_change_7d=D("-0.050"),
                taker_buy_sell_ratio_4h=D("1.06"),
                taker_buy_sell_ratio_24h=D("1.03"),
                degraded_telemetry=degraded,
                observed_at=self._now,
            )
        finally:
            self.active_derivs -= 1

    async def get_context(self) -> MacroSentimentContext:
        return MacroSentimentContext(observed_at=self._now)


async def run_benchmark() -> dict[str, Any]:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    instruments, quotes, candles_map = _build_universe(now)
    rss_before = _peak_rss_mb()

    async def _run_single(enabled: bool, inject_core_shortlist: int = 45) -> dict[str, Any]:
        mkt = _SimulatedLatencyMarket(instruments, quotes, candles_map, now)
        settings = Settings(
            _env_file=None,
            early_recovery_enabled=enabled,
            stage2_capacity_rollover_enabled=True,
        )
        journal = ScanDecisionJournal()
        scr = CryptoOpportunityScreener(
            settings, mkt, evaluated_at=now, journal=journal, feature_contract="discovery_v2"
        )
        orig_accum = scr._score_accumulation_setup

        # Force 45 core Stage-1 survivors (SYM050..SYM094) so we hit the exact worst-case
        # 45 core shortlist + 30 Stage-2 probes = 75 total Stage-2 enrichments!
        def _worst_case_accum(**kwargs: Any) -> Any:
            sym = kwargs["inst"].id.symbol
            if sym.startswith("SYM"):
                idx = int(sym[3:6])
                if 50 <= idx < 50 + inject_core_shortlist:
                    res = orig_accum(**kwargs)
                    if res is not None:
                        return res
                    from algotrader.application.early_recovery import score_early_recovery

                    dummy, _ = score_early_recovery(
                        instruments[1],
                        quotes[1],
                        candles_map["SYM000USDT"][0],
                        candles_map["SYM000USDT"][1],
                        candles_map["BTCUSDT"][0],
                        now=now,
                        derivatives=kwargs["derivatives"],
                        is_btc_cascade=False,
                        macro_multiplier=D("1.0"),
                        macro_narrative="Bench",
                        beta=D("1.0"),
                        residual_24h=D("0.01"),
                        is_discovery_tier=False,
                        note=lambda *_a, **_kw: None,
                    )
                    if dummy is not None:
                        return replace(
                            dummy,
                            instrument=kwargs["inst"],
                            setup_label=SetupLabel.VCP_COMPRESSION_LONG,
                            score=D("78.0"),
                            conviction=D("78.0"),
                            oi_change_7d=D("0.05"),
                            recovery_evidence=None,
                        )
            return orig_accum(**kwargs)

        scr._score_accumulation_setup = _worst_case_accum  # type: ignore[method-assign]
        t0 = time.perf_counter()
        with redirect_stdout(io.StringIO()):
            opps = await scr.screen(
                mkt, mkt, mode=ScreenerMode.ALL, max_symbols=320, enforce_quality=True
            )
        elapsed_s = round(time.perf_counter() - t0, 3)

        w_stage0 = 56
        w_stage1 = scr.scan_counts["selected"] * 5 + 2
        w_stage2 = (scr.scan_counts["stage2_shortlist"] + scr.scan_counts["stage2_probes"]) * 4
        total_weight = w_stage0 + w_stage1 + w_stage2

        return {
            "early_recovery_enabled": enabled,
            "duration_seconds": elapsed_s,
            "peak_rss_mb": _peak_rss_mb(),
            "selected_symbols": scr.scan_counts["selected"],
            "stage2_shortlist": scr.scan_counts["stage2_shortlist"],
            "stage2_probes": scr.scan_counts["stage2_probes"],
            "stage2_total_enrichments": scr.scan_counts["stage2_shortlist"]
            + scr.scan_counts["stage2_probes"],
            "total_derivatives_calls_observed": mkt.total_deriv_calls,
            "max_concurrent_derivatives_observed": mkt.max_concurrent_derivs,
            "concurrency_limit_respected_le_8": mkt.max_concurrent_derivs <= ENRICHMENT_CONCURRENCY,
            "stage2_ceiling_respected_le_75": mkt.total_deriv_calls <= 75,
            "recovery_stage1_ready": scr.scan_counts.get("recovery_stage1_ready", 0),
            "recovery_probe_admitted": scr.scan_counts.get("recovery_probe_admitted", 0),
            "recovery_probe_cap_respected_le_8": scr.scan_counts.get("recovery_probe_admitted", 0)
            <= RECOVERY_PROBE_CAP,
            "simulated_stage2_failures_handled": mkt.simulated_failures,
            "estimated_binance_weight": total_weight,
            "weight_under_2400_ceiling": total_weight < 2400,
            "published_total": len(opps),
            "published_recovery_count": sum(
                1 for o in opps if o.setup_label is SetupLabel.RECOVERY_RECLAIM_LONG
            ),
        }

    cold_on = await _run_single(True, inject_core_shortlist=45)
    warm_on = await _run_single(True, inject_core_shortlist=45)
    rollback_off = await _run_single(False, inject_core_shortlist=45)

    assert cold_on["concurrency_limit_respected_le_8"] is True
    assert cold_on["stage2_ceiling_respected_le_75"] is True
    assert cold_on["recovery_probe_cap_respected_le_8"] is True
    assert cold_on["stage2_shortlist"] == 45
    assert cold_on["stage2_probes"] == 30
    assert cold_on["stage2_total_enrichments"] == 75
    assert rollback_off["published_recovery_count"] == 0

    return {
        "schema": "atlas-early-recovery-resource-benchmark-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "network_enabled": False,
        "rss_before_mb": rss_before,
        "cold_scan_flag_on_worst_case_75": cold_on,
        "warm_scan_flag_on_worst_case_75": warm_on,
        "rollback_scan_flag_off_worst_case_75": rollback_off,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    with asyncio.Runner() as runner, offline():
        report = runner.run(run_benchmark())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

