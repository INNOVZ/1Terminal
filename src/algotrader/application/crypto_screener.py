from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal

import structlog

from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.adapters.external.models import MacroSentimentContext
from algotrader.application.alt_momentum import _participation_ratio, _return_over
from algotrader.application.research import beta_correlation
from algotrader.config import Settings
from algotrader.domain.archetypes import (
    LongArchetypeFeatures,
    ShortArchetypeFeatures,
    classify_long_archetype,
    classify_short_archetype,
)
from algotrader.domain.crypto_screener_models import (
    ChecklistVerification,
    CryptoMarketRegime,
    CryptoOpportunity,
    CryptoScreenerScoreBreakdown,
    ScreenerMode,
    SetupLabel,
    calculate_opportunity_win_rate_and_ev,
)
from algotrader.domain.execution_brackets import (
    build_long_bracket,
    build_short_bracket,
)
from algotrader.domain.indicators import (
    IndicatorSnapshot,
    adx,
    bollinger_bands,
    confirmed_macd_divergences,
    confirmed_rsi_divergences,
    cumulative_volume_delta_metrics,
    ema,
    keltner_channels,
    macd,
    resample_candles,
    rsi,
    snapshot,
    support_resistance,
    taker_buy_ratio_from_candles,
    volume_contraction_metrics,
    volume_profile,
)
from algotrader.domain.liquidity import SymbolLiquidityMetrics, compute_ili_ranks
from algotrader.domain.models import (
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    Quote,
)
from algotrader.domain.ports import DerivativesDataPort, MarketDataPort
from algotrader.domain.precision import normalize_quantity
from algotrader.domain.resistance_absorption import compute_resistance_absorption
from algotrader.domain.scoring import (
    OpportunityGrade,
    calculate_adx_subscore,
    calculate_continuous_long_factor_scores,
    calculate_continuous_short_factor_scores,
    classify_opportunity_grade,
    compute_3tier_ev_r,
    compute_p_win_and_conviction,
)
from algotrader.domain.screening_gates import (
    LongGateInputs,
    ShortGateInputs,
    evaluate_long_gates,
    evaluate_short_gates,
)
from algotrader.domain.structure import classify as classify_structure

log = structlog.get_logger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

# Regime-aware COI thresholds and conviction floors
COI_OVEREXTENDED_THRESHOLD: float = 75.0
COI_OVERSOLD_THRESHOLD: float = 25.0
COI_TIGHTENED_CONVICTION_FLOOR: Decimal = Decimal("70.0")
COI_STANDARD_CONVICTION_FLOOR: Decimal = Decimal("65.0")


class CryptoOpportunityScreener:
    """Institutional-grade multi-factor crypto screening engine."""

    def __init__(
        self,
        settings: Settings,
        macro_provider: MacroSentimentProvider | None = None,
    ) -> None:
        self._settings = settings
        self._macro_provider = macro_provider or MacroSentimentProvider()
        self.scan_counts: dict[str, int] = {"selected": 0, "evaluated": 0, "failed": 0}

    async def screen(
        self,
        market: MarketDataPort,
        derivatives_port: DerivativesDataPort | None = None,
        *,
        min_score: Decimal | None = None,
        target_direction: Direction | None = None,
        top_n: int | None = None,
        max_symbols: int | None = None,
        mode: ScreenerMode = ScreenerMode.ACCUMULATION,
        coi_score: float | Decimal | None = None,
    ) -> list[CryptoOpportunity]:
        # 1. Fetch Macro Context Concurrently
        macro_context = await self._macro_provider.get_context()
        log.info("screener_macro_loaded", narrative=macro_context.narrative())

        # Resolve effective COI score for regime-aware curation
        effective_coi: float | None = None
        if coi_score is not None:
            effective_coi = float(coi_score)
        elif hasattr(macro_context, "coi_score") and macro_context.coi_score is not None:
            effective_coi = float(macro_context.coi_score)

        # 2. Universe & Liquidity Filtering (Stages 1-5 of AGENT.md V2.5)
        instruments = await market.instruments()
        benchmark_exclusions = {"BTC", "ETH"} if len(instruments) > 5 else set()
        toxic_symbols = {
            "PAXG", "XAU", "XAG", "COPPER", "SPY", "QQQ", "WLFI", "USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR",
            "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT",
        }
        excluded = set(self._settings.excluded_base_assets) | benchmark_exclusions | toxic_symbols
        eligible_instruments = [
            inst
            for inst in instruments
            if inst.trading
            and inst.base_asset not in excluded
            and inst.id.symbol not in toxic_symbols
            and inst.base_asset.isascii()
            and inst.quote_asset in ("USDT", "USDC")
            # Stage 3: DO NOT block 1000x multiplier contracts (1000SHIB, 1000PEPE, etc.)
        ]

        if not eligible_instruments:
            log.warning("no_eligible_instruments_in_universe")
            return []

        quotes = await market.quotes(eligible_instruments)
        quotes_by_symbol = {q.instrument_id.symbol: q for q in quotes}

        # Stage 4: Filter by liquidity: min $500k 24h volume, max 50 bps spread
        liquid_instruments: list[Instrument] = []
        for inst in eligible_instruments:
            q = quotes_by_symbol.get(inst.id.symbol)
            if q is None:
                continue
            if q.quote_volume_24h < Decimal("500000"):
                continue
            if q.spread_bps > Decimal("50"):
                continue
            liquid_instruments.append(inst)

        # Stage 4b: Canonical Single-Pair-Per-Asset Deduplication (Best of USDT/USDC)
        # Avoid duplicate scanning of the same underlying asset (e.g. NEARUSDT vs NEARUSDC).
        # Retain the single contract with highest 24h quote liquidity, strictly favoring USDT when comparable.
        best_by_base: dict[str, Instrument] = {}
        for inst in liquid_instruments:
            base = inst.base_asset
            if base not in best_by_base:
                best_by_base[base] = inst
            else:
                curr = best_by_base[base]
                vol_new = quotes_by_symbol[inst.id.symbol].quote_volume_24h
                vol_curr = quotes_by_symbol[curr.id.symbol].quote_volume_24h
                if vol_new > vol_curr * Decimal("1.05"):
                    best_by_base[base] = inst
                elif vol_curr > vol_new * Decimal("1.05"):
                    pass  # keep curr
                elif (inst.quote_asset == "USDT" and curr.quote_asset != "USDT") or vol_new > vol_curr:
                    best_by_base[base] = inst
        liquid_instruments = list(best_by_base.values())

        # Stage 5: Dynamic 265-Pair Funnel (Top 215 ILI Core + Top 50 Pre-Pump Anomaly Discovery)
        target_core = 215 if max_symbols is None else min(max_symbols, max(1, int(max_symbols * (215 / 265))))
        target_discovery = 50 if max_symbols is None else max(0, max_symbols - target_core)

        ili_inputs = [
            SymbolLiquidityMetrics(
                symbol=inst.id.symbol,
                quote_vol_24h=quotes_by_symbol[inst.id.symbol].quote_volume_24h,
            )
            for inst in liquid_instruments
        ]
        ranked_symbols = compute_ili_ranks(ili_inputs)
        inst_by_symbol = {inst.id.symbol: inst for inst in liquid_instruments}

        core_symbols = [
            r.symbol for r in ranked_symbols[:target_core] if r.symbol in inst_by_symbol
        ]
        core_set = set(core_symbols)

        # Discovery Tier Anomaly Ranking for ranks #216–#444
        discovery_candidates: list[tuple[float, Instrument]] = []
        for r in ranked_symbols[target_core:]:
            sym = r.symbol
            if sym not in inst_by_symbol:
                continue
            inst = inst_by_symbol[sym]
            q = quotes_by_symbol.get(sym)
            if q is None:
                continue
            # Execution Hygiene: quote_vol >= $1,500,000 and spread <= 25 bps
            if q.quote_volume_24h < Decimal("1500000") or q.spread_bps > Decimal("25"):
                continue

            anomaly_score = 0.0
            # 1. Negative funding crowding (Funding_Rate <= -0.012% or -1.2 bps)
            if q.funding_rate is not None and q.funding_rate <= Decimal("-0.00012"):
                anomaly_score += 35.0 + min(30.0, float(abs(q.funding_rate)) * 100000.0)

            # 2. Perpetual-to-Index basis premium inflection (basis >= +0.08% or +8 bps)
            if q.basis_pct is not None and q.basis_pct >= Decimal("0.0008"):
                anomaly_score += 30.0 + min(25.0, float(q.basis_pct) * 10000.0)

            # 3. Early tight-range volume turnover (Ret_24h in [-3%, +9%] with volume-to-range compression)
            chg = float(q.price_change_pct_24h or Decimal("0"))
            rng = float(q.range_24h_pct or Decimal("0.05"))
            if -3.0 <= chg <= 9.0:
                compression_ratio = min(35.0, (float(q.quote_volume_24h) / 1_000_000.0) / max(rng, 0.01))
                anomaly_score += compression_ratio

            # 4. Extreme Compression Anomaly (Dead-Volume Stealth Coil Precursor)
            # Tokens with very tight 24h range (rng <= 4.0%) and flat price (-3% <= chg <= 3%)
            # are pre-loaded springs winding up before markup explosion
            if rng <= 0.040 and -3.0 <= chg <= 3.0:
                anomaly_score += 25.0 + 15.0 * max(0.0, (0.040 - rng) / 0.040)

            if anomaly_score > 0.0:
                discovery_candidates.append((anomaly_score, inst))

        discovery_candidates.sort(key=lambda x: x[0], reverse=True)
        discovery_symbols = [inst.id.symbol for _, inst in discovery_candidates[:target_discovery]]
        discovery_tier_set: set[str] = set(discovery_symbols)

        # Combined Stage 1 scan list (totaling up to 265 symbols)
        selected_symbols = core_symbols + [s for s in discovery_symbols if s not in core_set]
        selected_instruments = [inst_by_symbol[s] for s in selected_symbols if s in inst_by_symbol]
        if not selected_instruments:
            selected_instruments = liquid_instruments[:(target_core + target_discovery)]

        self.scan_counts["selected"] = len(selected_instruments)
        log.info(
            "universe_liquidity_filtered",
            total=len(instruments),
            eligible=len(eligible_instruments),
            liquid=len(liquid_instruments),
            core_selected=len(core_symbols),
            discovery_selected=len(discovery_symbols),
            total_selected=len(selected_instruments),
            mode=mode.value,
        )

        # 3. Locate and Fetch BTC Benchmark History (240 1H candles for benchmark beta/residual alpha)
        btc_inst = next(
            (inst for inst in instruments if inst.base_asset == "BTC" and inst.trading),
            None,
        )
        btc_candles_1h: Sequence[Candle] = ()
        if btc_inst is not None:
            btc_candles_1h = await market.candles(btc_inst, "1h", 240)

        # BTC Velocity Liquidation Cascade check
        btc_ret_1h = _return_over(btc_candles_1h, 1) if len(btc_candles_1h) >= 2 else ZERO
        btc_ret_4h = _return_over(btc_candles_1h, 4) if len(btc_candles_1h) >= 5 else ZERO
        is_btc_cascade = btc_ret_1h <= Decimal("-0.025") or btc_ret_4h <= Decimal("-0.045")

        # 4. Stage 1: Vectorized In-Memory Screening (Zero External Derivatives Latency)
        semaphore = asyncio.Semaphore(15)
        candle_cache: dict[str, tuple[Sequence[Candle], ...]] = {}

        async def eval_instrument(inst: Instrument) -> list[CryptoOpportunity]:
            is_disc = inst.id.symbol in discovery_tier_set
            async with semaphore:
                await asyncio.sleep(0.01)  # Paced execution
                res = await self._evaluate_single(
                    inst=inst,
                    quote=quotes_by_symbol[inst.id.symbol],
                    market=market,
                    derivatives_port=derivatives_port if mode == ScreenerMode.BREAKOUT else None,
                    btc_candles=btc_candles_1h,
                    macro_context=macro_context,
                    mode=mode,
                    target_direction=target_direction,
                    is_btc_cascade=is_btc_cascade,
                    btc_ret_1h=btc_ret_1h,
                    min_score=min_score,
                    is_discovery_tier=is_disc,
                    candle_cache=candle_cache,
                )
                await asyncio.sleep(0)
                return res

        results = await asyncio.gather(
            *(eval_instrument(inst) for inst in selected_instruments),
            return_exceptions=True,
        )
        self.scan_counts["evaluated"] = sum(isinstance(result, list) for result in results)
        self.scan_counts["failed"] = len(results) - self.scan_counts["evaluated"]

        stage1_survivors: list[CryptoOpportunity] = []
        stage1_threshold = min(Decimal("55.0"), min_score) if min_score is not None else Decimal("55.0")
        for res in results:
            if isinstance(res, list):
                for opp in res:
                    if opp.score >= stage1_threshold:
                        stage1_survivors.append(opp)
            elif isinstance(res, Exception):
                log.debug("stage1_eval_exception", error=str(res))

        # Sort surviving Stage 1 candidates descending by preliminary structural score
        stage1_survivors.sort(key=lambda o: o.score, reverse=True)
        # Cap Stage 2 derivatives enrichment at top 45 Stage 1 survivors
        stage2_shortlist = stage1_survivors[:45]

        # Stage 2: Deep Derivatives Enrichment ONLY for surviving shortlist
        stage2_semaphore = asyncio.Semaphore(8)

        async def enrich_opportunity(opp: CryptoOpportunity) -> CryptoOpportunity | None:
            if derivatives_port is None:
                return opp
            async with stage2_semaphore:
                try:
                    deriv_res = await asyncio.wait_for(
                        derivatives_port.derivatives_snapshot(opp.instrument), timeout=4.0
                    )
                    is_mandatory_deriv_setup = (
                        opp.direction == Direction.SHORT
                        or opp.setup_label in (
                            SetupLabel.OI_ACCUMULATION_LONG,
                            SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
                            SetupLabel.FUNDING_SQUEEZE_SPRING_LONG,
                        )
                    )
                    if isinstance(deriv_res, DerivativesSnapshot):
                        if (deriv_res.degraded_telemetry or deriv_res.open_interest_change_7d is None) and is_mandatory_deriv_setup:
                            log.warning(
                                "candidate_dropped_due_to_degraded_stage2_telemetry",
                                symbol=opp.instrument.id.symbol,
                            )
                            return None
                        cached_c = candle_cache.get(opp.instrument.id.symbol)
                        if cached_c is not None:
                            c1h_cached = cached_c[0]
                            c4h_cached = cached_c[1]
                            c1w_cached = cached_c[2] if len(cached_c) > 2 else ()
                            res_enriched = await self._evaluate_single(
                                inst=opp.instrument,
                                quote=quotes_by_symbol[opp.instrument.id.symbol],
                                market=market,
                                derivatives_port=None,
                                btc_candles=btc_candles_1h,
                                macro_context=macro_context,
                                mode=mode,
                                target_direction=target_direction,
                                is_btc_cascade=is_btc_cascade,
                                btc_ret_1h=btc_ret_1h,
                                min_score=min_score,
                                is_discovery_tier=opp.is_discovery_tier,
                                preloaded_candles=(c1h_cached, c4h_cached, c1w_cached),
                                explicit_derivatives=deriv_res,
                            )
                            return res_enriched[0] if res_enriched else None
                    if is_mandatory_deriv_setup:
                        return None
                    return opp
                except Exception as exc:
                    log.warning(
                        "candidate_dropped_due_to_degraded_stage2_telemetry",
                        symbol=opp.instrument.id.symbol,
                        error=str(exc),
                    )
                    return None

        enriched_results = await asyncio.gather(
            *(enrich_opportunity(opp) for opp in stage2_shortlist),
            return_exceptions=True,
        )

        opportunities: list[CryptoOpportunity] = []
        for enriched in enriched_results:
            if isinstance(enriched, CryptoOpportunity):
                opp = enriched
                eff_score = opp.conviction if opp.conviction is not None else opp.score
                if min_score is not None:
                    if eff_score < min_score:
                        continue
                else:
                    # Institutional Quality Gating:
                    # 1. Strictly eliminate below-threshold setups
                    if opp.grade is OpportunityGrade.BELOW_THRESHOLD or str(opp.grade) == "Below Threshold":
                        continue
                    # 2. Strict score & conviction floor (Tiered: Core vs Discovery)
                    min_score_floor = Decimal("71.0") if opp.is_discovery_tier else Decimal("68.0")
                    min_conv_floor = Decimal("69.0") if opp.is_discovery_tier else Decimal("65.0")
                    if eff_score < min_score_floor or opp.score < min_score_floor:
                        continue
                    if opp.conviction is not None and opp.conviction < min_conv_floor:
                        continue
                    # 3. Minimum 3 of 5 multi-system checklist consensus
                    if opp.checklist_verification is not None and opp.checklist_verification.passed_count < 3:
                        continue
                    # 4. Mandatory positive mathematical expectancy and asymmetric R/R
                    min_rr = Decimal("1.50") if opp.direction is Direction.SHORT else Decimal("2.00")
                    if opp.risk_reward_ratio < min_rr or opp.expected_value_r < Decimal("0.20"):
                        continue
                    # 5. Regime-aware conviction floor governed by COI
                    if effective_coi is not None and effective_coi >= COI_OVEREXTENDED_THRESHOLD:
                        base_req = COI_TIGHTENED_CONVICTION_FLOOR if opp.direction is Direction.LONG else min_conv_floor
                        req_conviction = base_req + Decimal("4.0") if (opp.is_discovery_tier and opp.direction is Direction.LONG) else base_req
                    elif effective_coi is not None and effective_coi <= COI_OVERSOLD_THRESHOLD:
                        base_req = min_conv_floor if opp.direction is Direction.LONG else COI_TIGHTENED_CONVICTION_FLOOR
                        req_conviction = base_req + Decimal("4.0") if (opp.is_discovery_tier and opp.direction is Direction.SHORT) else base_req
                    else:
                        req_conviction = min_conv_floor
                    if (opp.conviction is not None and opp.conviction < req_conviction) or eff_score < req_conviction:
                        continue
                if target_direction is not None and opp.direction is not target_direction:
                    continue
                opportunities.append(opp)

        # Estimate & Log Binance API request weight
        w_stage0 = 56
        w_stage1 = len(selected_instruments) * 4 + 2
        w_stage2 = len(stage2_shortlist) * 2 if derivatives_port is not None else 0
        total_scan_weight = w_stage0 + w_stage1 + w_stage2
        log.info(
            "binance_scan_weight_budget",
            total_estimated_weight=total_scan_weight,
            rate_limit_ceiling=2400,
            budget_cap=1300,
            stage0_weight=w_stage0,
            stage1_symbols=len(selected_instruments),
            stage1_weight=w_stage1,
            stage2_candidates=len(stage2_shortlist),
            stage2_weight=w_stage2,
        )

        # Rank descending by composite conviction (multi-system confluence, score & win rate)
        def _conviction(o: CryptoOpportunity) -> float:
            base = float(o.conviction) if o.conviction is not None else float(o.estimated_win_rate_pct) * 0.4 + float(o.score) * 0.6
            if o.checklist_verification is not None:
                passed = o.checklist_verification.passed_count
                if passed == 5:
                    return base * 1.08  # 5-system institutional consensus
                elif passed == 4:
                    return base * 1.04  # 4-system consensus
                elif passed == 3:
                    return base * 1.00  # Baseline 3-system confluence
                elif passed == 2:
                    return base * 0.88  # Only 2 systems passed
                else:
                    return base * 0.75  # 1 system passed
            return base

        longs = [o for o in opportunities if o.direction is Direction.LONG]
        shorts = [o for o in opportunities if o.direction is Direction.SHORT]
        longs.sort(key=_conviction, reverse=True)
        shorts.sort(key=_conviction, reverse=True)

        # Final base-asset deduplication: retain the single highest conviction pair per token
        seen_bases: set[str] = set()
        deduped_longs: list[CryptoOpportunity] = []
        for o in longs:
            if o.instrument.base_asset not in seen_bases:
                seen_bases.add(o.instrument.base_asset)
                deduped_longs.append(o)

        deduped_shorts: list[CryptoOpportunity] = []
        for o in shorts:
            if o.instrument.base_asset not in seen_bases:
                seen_bases.add(o.instrument.base_asset)
                deduped_shorts.append(o)

        effective_top_n = top_n if top_n is not None else 16

        # Regime-aware desk allocation caps governed by COI
        if effective_coi is not None and effective_coi >= COI_OVEREXTENDED_THRESHOLD:
            # Overextended Regime (COI >= 75): favour shorts (e.g., max 8 Longs, max 8 Shorts)
            desk_max_shorts = 8
            desk_max_longs = 8
        elif effective_coi is not None and effective_coi <= COI_OVERSOLD_THRESHOLD:
            # Capitulation / Oversold Regime (COI <= 25): cap shorts at 3
            desk_max_shorts = 3
            desk_max_longs = max(1, effective_top_n - desk_max_shorts)
        else:
            # Neutral Regime (25 < COI < 75 or unspecified): standard spec caps
            desk_max_shorts = 6
            desk_max_longs = 12

        if target_direction is None:
            # Curated Institutional Top Picks with Regime-aware allocation
            max_shorts = min(desk_max_shorts, len(deduped_shorts))
            max_longs = min(min(desk_max_longs, effective_top_n - max_shorts), len(deduped_longs))
            all_opps = deduped_longs[:max_longs] + deduped_shorts[:max_shorts]
            all_opps.sort(key=_conviction, reverse=True)
            return all_opps[:effective_top_n]

        final_opps = deduped_longs if target_direction is Direction.LONG else deduped_shorts
        return final_opps[:effective_top_n]

    async def _evaluate_single(
        self,
        inst: Instrument,
        quote: Quote,
        market: MarketDataPort,
        derivatives_port: DerivativesDataPort | None,
        btc_candles: Sequence[Candle],
        macro_context: MacroSentimentContext,
        mode: ScreenerMode = ScreenerMode.ACCUMULATION,
        target_direction: Direction | None = None,
        is_btc_cascade: bool = False,
        btc_ret_1h: Decimal = ZERO,
        min_score: Decimal | None = None,
        is_discovery_tier: bool = False,
        candle_cache: dict[str, tuple[Sequence[Candle], ...]] | None = None,
        preloaded_candles: tuple[Sequence[Candle], ...] | None = None,
        explicit_derivatives: DerivativesSnapshot | None = None,
    ) -> list[CryptoOpportunity]:
        """Evaluate a single instrument across 1h, 4h, derivatives, and alpha factors."""
        if preloaded_candles is not None:
            c1h = preloaded_candles[0]
            c4h = preloaded_candles[1]
            c1w = preloaded_candles[2] if len(preloaded_candles) > 2 else ()
        else:
            async def _fetch_candles(interval: str, limit: int) -> Sequence[Candle]:
                for attempt in range(3):
                    try:
                        return await market.candles(inst, interval, limit)
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(0.35 * (attempt + 1))
                        else:
                            return ()
                return ()

            # Concurrently fetch 1h (240 bars, w=2), 4h (480 bars = 80d, w=2), and 1w (40 bars = 280d, w=1)
            c1h, c4h, c1w = await asyncio.gather(
                _fetch_candles("1h", 240),
                _fetch_candles("4h", 480),
                _fetch_candles("1w", 40),
                return_exceptions=True,
            )
            if candle_cache is not None and not isinstance(c1h, BaseException) and not isinstance(c4h, BaseException) and c1h and c4h:
                candle_cache[inst.id.symbol] = (
                    c1h,
                    c4h,
                    c1w if not isinstance(c1w, BaseException) else (),
                )

        if isinstance(c1h, BaseException) or not c1h or len(c1h) < 120:
            return []
        if isinstance(c4h, BaseException) or not c4h or len(c4h) < 40:
            return []

        closed_1h = sorted([c for c in c1h if c.closed], key=lambda x: x.closed_at)
        closed_4h = sorted([c for c in c4h if c.closed], key=lambda x: x.closed_at)
        candles_1d = resample_candles(closed_4h, "1d")
        candles_1w = list(c1w) if (not isinstance(c1w, BaseException) and c1w and len(c1w) >= 15) else (resample_candles(candles_1d, "1w") if candles_1d else [])

        if len(closed_1h) < 120 or len(closed_4h) < 40:
            return []

        # Indicators
        await asyncio.sleep(0)  # Cooperative yield to event loop for healthchecks
        snap_1h = snapshot(closed_1h)
        closes_4h = [c.close for c in closed_4h]
        ema20_4h = ema(closes_4h, 20)[-1]
        ema50_4h = ema(closes_4h, 50)[-1]
        rsi_4h = rsi(closes_4h, 14)[-1]

        # Key levels & Swing structure
        levels = support_resistance(
            closed_1h, lookback=self._settings.support_resistance_lookback_bars
        )
        support = levels.support
        resistance = levels.resistance
        struct = classify_structure(closed_1h)

        # BTC Beta and Residual Alpha
        beta, correlation, residual_24h, residual_6h, residual_7d = self._calc_residual_alpha(
            closed_1h, btc_candles
        )

        # Regime classification
        regime = self._classify_regime(snap_1h, ema20_4h, ema50_4h)

        opps: list[CryptoOpportunity] = []
        derivatives: DerivativesSnapshot | None = explicit_derivatives

        # Stage 1: Preliminary Structural Setup Evaluation (Zero External Derivatives Latency)
        accum_opp = None
        if (target_direction is None or target_direction is Direction.LONG) and mode in (
            ScreenerMode.ACCUMULATION,
            ScreenerMode.BREAKOUT,
            ScreenerMode.ALL,
            None,
        ):
            accum_opp = self._score_accumulation_setup(
                inst=inst,
                quote=quote,
                snap_1h=snap_1h,
                closed_1h=closed_1h,
                closed_4h=closed_4h,
                derivatives=derivatives,
                macro_context=macro_context,
                is_btc_cascade=is_btc_cascade,
                btc_ret_1h=btc_ret_1h,
                beta=beta,
                residual_24h=residual_24h,
                candles_1d=candles_1d,
                candles_1w=candles_1w,
                is_discovery_tier=is_discovery_tier,
            )

        # 2. Pre-Breakdown Distribution Evaluation (BEFORE the dump - SHORT)
        dist_opp = None
        if (target_direction is None or target_direction is Direction.SHORT) and mode in (
            ScreenerMode.DISTRIBUTION,
            ScreenerMode.ALL,
            None,
        ):
            dist_opp = self._score_distribution_setup(
                inst=inst,
                quote=quote,
                snap_1h=snap_1h,
                closed_1h=closed_1h,
                closed_4h=closed_4h,
                derivatives=derivatives,
                macro_context=macro_context,
                is_btc_cascade=is_btc_cascade,
                btc_ret_1h=btc_ret_1h,
                beta=beta,
                residual_24h=residual_24h,
                candles_1d=candles_1d,
                candles_1w=candles_1w,
                is_discovery_tier=is_discovery_tier,
            )

        # Stage 2: Deep Derivatives Enrichment ONLY for Shortlisted Candidates
        # If neither accumulation nor distribution passed structural criteria, avoid expensive derivatives API calls
        qual_threshold = min(Decimal("58.0"), min_score) if min_score is not None else Decimal("58.0")
        is_viable_long = accum_opp is not None and accum_opp.score >= qual_threshold
        is_viable_short = dist_opp is not None and dist_opp.score >= qual_threshold

        if not is_viable_long and not is_viable_short and mode != ScreenerMode.BREAKOUT:
            return []

        if derivatives is None and derivatives_port is not None and (is_viable_long or is_viable_short or mode == ScreenerMode.BREAKOUT):
            try:
                deriv_res = await asyncio.wait_for(
                    derivatives_port.derivatives_snapshot(inst), timeout=4.0
                )
                if isinstance(deriv_res, DerivativesSnapshot):
                    derivatives = deriv_res
            except Exception:
                derivatives = None

        if derivatives is not None and derivatives is not explicit_derivatives:
            if is_viable_long:
                accum_opp = self._score_accumulation_setup(
                    inst=inst,
                    quote=quote,
                    snap_1h=snap_1h,
                    closed_1h=closed_1h,
                    closed_4h=closed_4h,
                    derivatives=derivatives,
                    macro_context=macro_context,
                    is_btc_cascade=is_btc_cascade,
                    btc_ret_1h=btc_ret_1h,
                    beta=beta,
                    residual_24h=residual_24h,
                    candles_1d=candles_1d,
                    candles_1w=candles_1w,
                    is_discovery_tier=is_discovery_tier,
                )
            if is_viable_short:
                dist_opp = self._score_distribution_setup(
                    inst=inst,
                    quote=quote,
                    snap_1h=snap_1h,
                    closed_1h=closed_1h,
                    closed_4h=closed_4h,
                    derivatives=derivatives,
                    macro_context=macro_context,
                    is_btc_cascade=is_btc_cascade,
                    btc_ret_1h=btc_ret_1h,
                    beta=beta,
                    residual_24h=residual_24h,
                    candles_1d=candles_1d,
                    candles_1w=candles_1w,
                    is_discovery_tier=is_discovery_tier,
                )

        if derivatives is not None and getattr(derivatives, "degraded_telemetry", False):
            log.warning("candidate_dropped_due_to_degraded_stage2_telemetry", symbol=inst.id.symbol)
            return []

        # Fix 2: Hard veto on finalized Stage 2 candidates if derivatives or 7d OI is missing ("N/A")
        if explicit_derivatives is not None:
            if dist_opp is not None and dist_opp.oi_change_7d is None:
                dist_opp = None
            if accum_opp is not None and accum_opp.setup_label in (
                SetupLabel.OI_ACCUMULATION_LONG,
                SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
                SetupLabel.FUNDING_SQUEEZE_SPRING_LONG,
            ) and accum_opp.oi_change_7d is None:
                accum_opp = None

        # STRICT INSTITUTIONAL MUTUAL EXCLUSION:
        # A token cannot be both an accumulation long and a distribution short simultaneously!
        # If a token qualifies as a confirmed institutional distribution short (e.g. Wyckoff UTAD,
        # MTF overbought exhaustion, or confirmed bearish divergence with score >= 70),
        # it is fundamentally in Distribution — it must NEVER be classified as a pre-breakout accumulation long!
        if accum_opp is not None and dist_opp is not None:
            # If both directions qualify, assign the one with higher score and conviction
            if dist_opp.score > accum_opp.score:
                accum_opp = None
            else:
                dist_opp = None

        if accum_opp is not None and accum_opp.setup_label is not SetupLabel.NO_SETUP:
            opps.append(accum_opp)
        if dist_opp is not None and dist_opp.setup_label is not SetupLabel.NO_SETUP:
            opps.append(dist_opp)

        # 3. Active Breakout Mode (Strictly isolated to explicit BREAKOUT mode for squeeze/expansion ignition)
        if mode == ScreenerMode.BREAKOUT and not opps:
            if target_direction is None or target_direction is Direction.LONG:
                long_opp = self._score_setup(
                    direction=Direction.LONG,
                    inst=inst,
                    quote=quote,
                    snap_1h=snap_1h,
                    ema20_4h=ema20_4h,
                    ema50_4h=ema50_4h,
                    rsi_4h=rsi_4h,
                    regime=regime,
                    support=support,
                    resistance=resistance,
                    struct_trend=struct.trend,
                    beta=beta,
                    residual_24h=residual_24h,
                    residual_6h=residual_6h,
                    residual_7d=residual_7d,
                    derivatives=derivatives,
                    macro_context=macro_context,
                    closed_1h=closed_1h,
                    closed_4h=closed_4h,
                    is_discovery_tier=is_discovery_tier,
                )
                if long_opp and long_opp.setup_label is not SetupLabel.NO_SETUP:
                    opps.append(long_opp)

            if target_direction is None or target_direction is Direction.SHORT:
                short_opp = self._score_setup(
                    direction=Direction.SHORT,
                    inst=inst,
                    quote=quote,
                    snap_1h=snap_1h,
                    ema20_4h=ema20_4h,
                    ema50_4h=ema50_4h,
                    rsi_4h=rsi_4h,
                    regime=regime,
                    support=support,
                    resistance=resistance,
                    struct_trend=struct.trend,
                    beta=beta,
                    residual_24h=residual_24h,
                    residual_6h=residual_6h,
                    residual_7d=residual_7d,
                    derivatives=derivatives,
                    macro_context=macro_context,
                    closed_1h=closed_1h,
                    closed_4h=closed_4h,
                    is_discovery_tier=is_discovery_tier,
                )
                if short_opp and short_opp.setup_label is not SetupLabel.NO_SETUP:
                    opps.append(short_opp)

        return opps

    def _score_accumulation_setup(
        self,
        inst: Instrument,
        quote: Quote,
        snap_1h: IndicatorSnapshot,
        closed_1h: Sequence[Candle],
        closed_4h: Sequence[Candle],
        derivatives: DerivativesSnapshot | None,
        macro_context: MacroSentimentContext,
        is_btc_cascade: bool = False,
        btc_ret_1h: Decimal = ZERO,
        beta: Decimal = Decimal("1.0"),
        residual_24h: Decimal = ZERO,
        candles_1d: Sequence[Candle] | None = None,
        candles_1w: Sequence[Candle] | None = None,
        is_discovery_tier: bool = False,
    ) -> CryptoOpportunity | None:
        """Score pre-breakout volatility squeeze, base coiling, and silent accumulation BEFORE expansion (V2.5)."""
        close = snap_1h.close
        atr14 = snap_1h.atr14
        closes_1h = [c.close for c in closed_1h]
        closes_4h = [c.close for c in closed_4h]
        if len(closes_4h) < 20 or len(closes_1h) < 20:
            return None
        ema20_4h = ema(closes_4h, 20)[-1]
        ema50_4h = ema(closes_4h, 50)[-1] if len(closes_4h) >= 50 else ema20_4h
        ema200_4h = ema(closes_4h, 200)[-1] if len(closes_4h) >= 200 else ema(closes_4h, len(closes_4h))[-1]
        rsi_4h = rsi(closes_4h, 14)[-1]

        # Returns & Excursions
        ret_24h = _return_over(closed_1h, 24)
        ret_7d = _return_over(closed_1h, 168) if len(closed_1h) >= 169 else ZERO

        last_72h = closed_1h[-72:]
        high_72h = max(c.high for c in last_72h)
        low_72h = min(c.low for c in last_72h)
        if low_72h <= ZERO or high_72h <= low_72h:
            return None
        exc_72h = (high_72h - low_72h) / max(low_72h, Decimal("1e-8"))
        dist_high_72h = (high_72h - close) / max(high_72h, Decimal("1e-8"))

        # Base Window (up to 30 days of 4H bars)
        base_window = closed_4h[-120:] if len(closed_4h) >= 120 else closed_4h
        high_30d = max(c.high for c in base_window)
        low_30d = min(c.low for c in base_window)
        if low_30d <= ZERO or high_30d <= low_30d:
            return None

        pos_in_range = (close - low_30d) / max(high_30d - low_30d, Decimal("1e-8"))
        low_24h = min(c.low for c in (closed_1h[-24:] if len(closed_1h) >= 24 else closed_1h))

        # 30-Day Volume Profile (using 180 4H bars = 720 hours = 30 days)
        vp_window = closed_4h[-180:] if len(closed_4h) >= 180 else (closed_1h[-240:] if len(closed_1h) >= 240 else closed_1h)
        vp = volume_profile(vp_window, bins=40)
        poc_price = vp.poc_price
        vah_price = vp.vah_price
        val_price = vp.val_price

        # Volatility Squeeze & Compression
        bb_upper_4h, bb_mid_4h, bb_lower_4h, bbw_4h = bollinger_bands(closes_4h, 20, Decimal("2.0"))
        kc_upper_4h, kc_mid_4h, kc_lower_4h = keltner_channels(closed_4h, 20, Decimal("1.5"))
        bb_inside_kc_series = [
            bool(
                b_low is not None
                and k_low is not None
                and b_high is not None
                and k_high is not None
                and b_low >= k_low
                and b_high <= k_high
            )
            for b_low, k_low, b_high, k_high in zip(
                bb_lower_4h, kc_lower_4h, bb_upper_4h, kc_upper_4h, strict=False
            )
        ]
        bb_inside_kc_4h = bb_inside_kc_series[-1] if bb_inside_kc_series else False

        # Concurrently detect 1H intraday squeeze coiling inside 4H base
        bb_upper_1h, bb_mid_1h, bb_lower_1h, bbw_1h = bollinger_bands(closes_1h, 20, Decimal("2.0"))
        kc_upper_1h, kc_mid_1h, kc_lower_1h = keltner_channels(closed_1h, 20, Decimal("1.5"))
        bb_inside_kc_1h = bool(
            bb_lower_1h
            and kc_lower_1h
            and bb_upper_1h
            and kc_upper_1h
            and bb_lower_1h[-1] is not None
            and kc_lower_1h[-1] is not None
            and bb_upper_1h[-1] is not None
            and kc_upper_1h[-1] is not None
            and bb_lower_1h[-1] >= kc_lower_1h[-1]
            and bb_upper_1h[-1] <= kc_upper_1h[-1]
        )
        bb_inside_kc = bb_inside_kc_4h or bb_inside_kc_1h
        bandwidth_current = bbw_4h[-1] if bbw_4h else Decimal("0.20")
        adv_7d, adv_14d, vol_contraction_ratio = volume_contraction_metrics(closed_1h, 7, 14)

        # Disqualify Active Bearish Divergences on accumulation only if price broke below structural support
        if len(closed_4h) >= 24 and (close < ema50_4h and close < poc_price):
            rsi_divs_4h = confirmed_rsi_divergences(closed_4h[-24:], lookback=18, min_rsi_delta=Decimal("2.0"))
            if any(d.kind == "bearish" for d in rsi_divs_4h):
                return None
            macd_divs_4h = confirmed_macd_divergences(closed_4h[-24:], lookback=18)
            if any(d.kind == "bearish" for d in macd_divs_4h):
                return None

        # Swings & Indicators
        local_swing_high_4h = max(c.high for c in (closed_4h[-18:] if len(closed_4h) >= 18 else closed_4h))
        swing_low_4h = min(c.low for c in (closed_4h[-42:] if len(closed_4h) >= 42 else closed_4h))
        macd_line_4h, macd_sig_4h, macd_hist_4h_series = macd(closes_4h)
        macd_hist_4h = macd_hist_4h_series[-1] if macd_hist_4h_series else ZERO
        macd_hist_4h_prev = macd_hist_4h_series[-2] if len(macd_hist_4h_series) >= 2 else ZERO
        rvol_4h = snap_1h.relative_volume

        # Derivatives & Order Flow features
        funding = derivatives.funding_rate if derivatives else None
        # Crowded long funding guard (> 25% APR / 2.5 bps per 8h)
        if funding is not None and funding > Decimal("0.00025"):
            return None
        oi_change = derivatives.open_interest_change_1h if derivatives else None
        # Fix 4: Disqualify any Long candidate where oi_change_1h < -2.5% (acute long liquidation / position unwinding)
        if oi_change is not None and oi_change < Decimal("-0.025"):
            return None
        oi_change_7d = derivatives.open_interest_change_7d if derivatives else None
        taker_ratio_calc = taker_buy_ratio_from_candles(closed_4h[-6:] if len(closed_4h) >= 6 else closed_4h)
        taker_ratio = (
            derivatives.taker_buy_sell_ratio
            if (derivatives and derivatives.taker_buy_sell_ratio)
            else taker_ratio_calc
        )
        taker_buy_ratio_4h = taker_ratio if taker_ratio is not None else Decimal("1.0000")
        cvd_metrics = cumulative_volume_delta_metrics(closed_1h, lookback_window=24)

        range_height_30d = (high_30d - low_30d) / max(low_30d, Decimal("1e-8"))
        dist_to_ceiling = (high_30d - close) / max(close, Decimal("1e-8"))

        # Kinetic Ignition Evaluation (Task 2)
        c_1h_latest = closed_1h[-1]
        rng_1h_latest = c_1h_latest.high - c_1h_latest.low
        cpos_1h_latest = (c_1h_latest.close - c_1h_latest.low) / rng_1h_latest if rng_1h_latest > ZERO else Decimal("0.5")

        # Trigger A (1H Pocket Pivot):
        prior_10_1h = closed_1h[-11:-1] if len(closed_1h) >= 11 else closed_1h[:-1]
        down_vols = [c.volume for c in prior_10_1h if c.close < c.open]
        highest_down_vol = max(down_vols) if down_vols else Decimal("0")
        pocket_pivot_1h = bool(
            c_1h_latest.close > c_1h_latest.open
            and c_1h_latest.volume > highest_down_vol
            and c_1h_latest.close > snap_1h.ema20
            and cpos_1h_latest >= Decimal("0.65")
        )

        # Trigger B (Micro-Structure Expansion):
        prior_5_highs = [c.high for c in (closed_1h[-6:-1] if len(closed_1h) >= 6 else closed_1h[:-1])]
        max_prior_high = max(prior_5_highs) if prior_5_highs else c_1h_latest.high
        micro_expansion_1h = bool(
            c_1h_latest.close > max_prior_high
            and (snap_1h.relative_volume >= Decimal("1.35") or rvol_4h >= Decimal("1.20"))
        )

        # Trigger C (Order-Flow Absorption):
        oi_delta_4h_val = derivatives.open_interest_change_4h if (derivatives and derivatives.open_interest_change_4h is not None) else oi_change
        oi_delta_24h_val = derivatives.open_interest_change_24h if (derivatives and derivatives.open_interest_change_24h is not None) else None
        taker_ratio_1h = derivatives.taker_buy_sell_ratio if derivatives else None
        of_absorption_1h = bool(
            oi_delta_4h_val is not None
            and oi_delta_4h_val >= Decimal("0.035")
            and taker_ratio_1h is not None
            and taker_ratio_1h >= Decimal("1.15")
            and c_1h_latest.close >= snap_1h.ema20
        )
        has_kinetic_ignition = pocket_pivot_1h or micro_expansion_1h or of_absorption_1h

        # Gate 2 Anti-Chasing 2.1 inputs
        prior_bbw_4h = bbw_4h[-2] if len(bbw_4h) >= 2 else bandwidth_current
        c_4h_latest = closed_4h[-1] if closed_4h else c_1h_latest
        rng_4h_latest = c_4h_latest.high - c_4h_latest.low
        cpos_4h_latest = (c_4h_latest.close - c_4h_latest.low) / rng_4h_latest if rng_4h_latest > ZERO else Decimal("0.5")

        # Archetype Classification (Wyckoff & Order Flow Priority)
        long_features = LongArchetypeFeatures(
            close=close,
            low_24h=low_24h,
            ema50_4h=ema50_4h,
            poc_30d=poc_price,
            vah_30d=vah_price,
            atr14_4h=atr14,
            vcr_4h=vol_contraction_ratio,
            oi_delta_7d=oi_change_7d,
            oi_delta_4h=oi_delta_4h_val,
            ret_7d=ret_7d,
            bb_inside_kc_4h=bb_inside_kc,
            bb_bandwidth_4h=bandwidth_current,
            local_swing_high_4h=local_swing_high_4h,
            rvol_4h=rvol_4h,
            norm_momentum_1h=snap_1h.momentum_normalized,
            btc_residual_alpha_24h=residual_24h,
            macd_hist_4h=macd_hist_4h,
            macd_hist_4h_prev=macd_hist_4h_prev,
            macd_hist_1h=snap_1h.macd_hist,
            taker_buy_ratio_4h=taker_buy_ratio_4h,
            range_height_30d=range_height_30d,
            pos_in_range_30d=pos_in_range,
            dist_to_ceiling=dist_to_ceiling,
            val_30d=val_price,
            funding_rate_8h=funding,
            cvd_24h=cvd_metrics.cvd_24h_change,
            ret_24h=ret_24h,
            oi_delta_24h=oi_delta_24h_val,
            taker_buy_ratio_1h=taker_ratio_1h,
            low_30d=low_30d,
            low_4h=c_4h_latest.low,
            ema20_4h=ema20_4h,
            ema20_1h=snap_1h.ema20,
            rvol_1h=snap_1h.relative_volume,
        )
        archetype = classify_long_archetype(long_features)
        if archetype is None:
            return None

        # Fix 2: Hard veto on derivative Long setups when 7d OI is missing ("N/A")
        if archetype in (
            SetupLabel.OI_ACCUMULATION_LONG,
            SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG,
            SetupLabel.FUNDING_SQUEEZE_SPRING_LONG,
        ) and (derivatives is not None and oi_change_7d is None):
            return None

        # Fix 4: Require current_price >= val_price for RANGE_BOTTOM_ACCUMULATION_LONG and OI_ACCUMULATION_LONG
        if archetype in (SetupLabel.RANGE_BOTTOM_ACCUMULATION_LONG, SetupLabel.OI_ACCUMULATION_LONG):
            if val_price > ZERO and close < val_price:
                return None

        # Fix 6: Require strictly negative funding (funding <= -0.50 bps) for SHORT_SQUEEZE_ABSORPTION_LONG
        if archetype == SetupLabel.SHORT_SQUEEZE_ABSORPTION_LONG:
            if funding is not None and funding > Decimal("-0.00005"):
                return None

        # Multi-Timeframe Candle Handling & HTF RSI (Anti-Chasing Gates)
        # Daily candles: prefer direct 1D candles (>= 15 bars), fallback to resampled 1D
        d_bars = (
            [c for c in candles_1d if c.closed]
            if (candles_1d and len(candles_1d) >= 15)
            else (resample_candles(closed_4h, "1d") if closed_4h else [])
        )
        closed_1d = d_bars
        closes_1d = [c.close for c in closed_1d]
        rsi_1d_series = rsi(closes_1d, 14) if len(closes_1d) >= 15 else []
        rsi_1d = rsi_1d_series[-1] if rsi_1d_series else None

        # Weekly candles: prefer direct 1W candles (>= 15 bars for 14-period RSI), fallback to resampled 1W
        w_bars = candles_1w if (candles_1w and len(candles_1w) >= 15) else (resample_candles(closed_1d, "1w") if closed_1d else [])
        closes_1w = [c.close for c in w_bars]
        rsi_1w_series = rsi(closes_1w, 14) if len(closes_1w) >= 15 else []
        rsi_1w = rsi_1w_series[-1] if rsi_1w_series else None

        # Stage 2 Gate L6 Bull-Trap Guard: Missing Stage 2 OI telemetry cannot falsely satisfy ceiling gate
        if dist_to_ceiling <= Decimal("0.045") and derivatives is not None:
            if oi_change_7d is None and oi_delta_4h_val is None and rvol_4h < Decimal("1.20"):
                return None

        # Mandatory Domain Gates L1-L6
        gate_inputs = LongGateInputs(
            close=close,
            ema50_4h=ema50_4h,
            ema20_1h=snap_1h.ema20,
            ema200_4h=ema200_4h,
            low_24h=low_24h,
            atr14_4h=atr14,
            vcr_4h=vol_contraction_ratio,
            ema20_4h=ema20_4h,
            excursion_72h=exc_72h,
            ret_24h=ret_24h,
            rsi_1h=snap_1h.rsi14,
            rsi_4h=rsi_4h,
            rsi_1d=rsi_1d,
            rsi_1w=rsi_1w,
            high_30d=high_30d,
            low_30d=low_30d,
            poc_30d=poc_price,
            val_30d=val_price,
            vah_30d=vah_price,
            bb_inside_kc_4h=bb_inside_kc,
            bb_bandwidth_4h=bandwidth_current,
            bb_inside_kc_4h_lookback=bb_inside_kc_series[-16:],
            rvol_4h=rvol_4h,
            oi_delta_7d=oi_change_7d,
            oi_delta_4h=oi_delta_4h_val,
            oi_delta_1h=oi_change,
            taker_buy_ratio_4h=taker_buy_ratio_4h,
            high_4h=c_4h_latest.high,
            low_4h=c_4h_latest.low,
            atr14_1h=snap_1h.atr14,
            funding_rate_8h=funding,
            ret_7d=ret_7d,
            prior_bb_bandwidth_4h=prior_bbw_4h,
            pivot_resistance=local_swing_high_4h,
            candle_4h_close_pos=cpos_4h_latest,
            has_kinetic_ignition=has_kinetic_ignition,
            pocket_pivot_1h=pocket_pivot_1h,
            micro_expansion_1h=micro_expansion_1h,
            of_absorption_1h=of_absorption_1h,
            taker_buy_ratio_1h=taker_ratio_1h,
            oi_delta_24h=oi_delta_24h_val,
        )
        gates_passed, gate_details = evaluate_long_gates(gate_inputs, archetype)
        if not gates_passed:
            return None

        cvd_metrics = cumulative_volume_delta_metrics(closed_1h, lookback_window=24)
        low_4h = closed_4h[-1].low if closed_4h else close
        low_4h_prev = closed_4h[-2].low if len(closed_4h) >= 2 else low_4h
        low_4h_prev2 = closed_4h[-3].low if len(closed_4h) >= 3 else low_4h_prev

        absorption_score, breakout_status, absorption_flags = compute_resistance_absorption(
            cvd_zscore_24h=float(cvd_metrics.cvd_z_score or Decimal("0")),
            taker_buy_ratio_4h=float(taker_buy_ratio_4h),
            oi_delta_7d=float(oi_change_7d) if oi_change_7d is not None else None,
            oi_delta_4h=float(oi_change) if oi_change is not None else None,
            funding_bps=float((funding or Decimal("0")) * 10000),
            low_4h=low_4h,
            low_4h_prev=low_4h_prev,
            low_4h_prev2=low_4h_prev2,
            close=close,
            ema20_1h=snap_1h.ema20,
            atr14_1h=snap_1h.atr14,
            ret_24h=ret_24h,
            vpvr_bins=vp.bins,
            vah_30d=vah_price,
            high_30d=high_30d,
            poc_bin_volume=vp.poc_volume,
        )

        # Strict Quality Guard: Reject candidates with heavy rejection risk / tape selling walls
        if breakout_status == "HIGH_REJECTION_RISK" or absorption_score < 35.0:
            return None

        # 5-Level Execution Bracket (Normalized with tick_size and Execution Mode Split)
        bracket = build_long_bracket(
            close=close,
            ema50_4h=ema50_4h,
            poc_30d=poc_price,
            atr14_4h=atr14,
            local_compression_high_4h=local_swing_high_4h,
            swing_low_4h=swing_low_4h,
            high_30d=high_30d,
            vah_30d=vah_price,
            val_30d=val_price,
            tick_size=inst.price_tick,
            resistance_absorption_score=Decimal(str(absorption_score)),
        )

        # Continuous Multi-Factor Quant Scoring (5 Institutional Pillars)
        # 4H DMI values for coiled ignition inflection (Task 3)
        adx_4h_vals, plus_di_4h, minus_di_4h = adx(closed_4h, 14) if len(closed_4h) >= 20 else ([], [], [])
        p_di_4h = plus_di_4h[-1] if plus_di_4h else snap_1h.plus_di
        m_di_4h = minus_di_4h[-1] if minus_di_4h else snap_1h.minus_di
        adx_val_4h = adx_4h_vals[-1] if adx_4h_vals else snap_1h.adx14
        adx_val_4h_prev = adx_4h_vals[-2] if len(adx_4h_vals) >= 2 else adx_val_4h
        delta_adx_1h = snap_1h.adx14 - snap_1h.momentum_reference_adx14
        delta_dmi_diff = snap_1h.plus_di - snap_1h.minus_di

        adx_subscore = calculate_adx_subscore(
            archetype,
            adx_val_4h,
            adx_val_4h_prev,
            snap_1h.plus_di,
            snap_1h.minus_di,
            bandwidth_4h=bandwidth_current,
            bb_inside_kc=bb_inside_kc,
            delta_adx_1h=delta_adx_1h,
            delta_dmi_diff_1h=delta_dmi_diff,
            dmi_plus_4h=p_di_4h,
            dmi_minus_4h=m_di_4h,
        )
        macro_mult = macro_context.multiplier_for(Direction.LONG)

        factor_scores = calculate_continuous_long_factor_scores(
            bandwidth_4h=bandwidth_current,
            bb_inside_kc=bb_inside_kc,
            vol_contraction_ratio=vol_contraction_ratio,
            close=close,
            poc_30d=poc_price,
            val_30d=val_price,
            vah_30d=vah_price,
            pos_in_range_30d=pos_in_range,
            ema50_4h=ema50_4h,
            ema200_4h=ema200_4h,
            resistance_absorption_score=Decimal(str(absorption_score)),
            cvd_zscore=Decimal(str(cvd_metrics.cvd_z_score or ZERO)),
            oi_change_7d=oi_change_7d,
            oi_change_4h=oi_delta_4h_val,
            taker_buy_ratio_4h=taker_buy_ratio_4h,
            funding_rate=funding,
            ema20_4h=ema20_4h,
            macd_hist_4h=macd_hist_4h,
            macd_hist_4h_prev=macd_hist_4h_prev,
            adx_subscore=adx_subscore,
            btc_residual_alpha=residual_24h,
            rvol_4h=rvol_4h,
            asymmetric_rr=bracket.asymmetric_rr,
            dist_to_trigger=dist_to_ceiling,
            macro_multiplier=macro_mult,
            is_btc_cascade=is_btc_cascade,
            dist_to_entry=abs(close - bracket.suggested_entry) / max(close, Decimal("1e-8")),
            oi_change_1h=derivatives.open_interest_change_1h if derivatives else None,
        )

        final_score = factor_scores.final_composite_score

        # 5-Point Institutional Checklist badges (Auction Market Theory Value Acceptance)
        is_breakout_arch = (
            archetype == SetupLabel.BREAKOUT_LONG
            if isinstance(archetype, SetupLabel)
            else str(archetype) == "BREAKOUT_LONG"
        )
        va_accepted = bool(
            (close >= poc_price * Decimal("0.97") or pos_in_range >= Decimal("0.35"))
            if is_breakout_arch
            else (
                (close >= val_price * Decimal("0.96"))
                or (close >= poc_price * Decimal("0.975"))
                or (pos_in_range >= Decimal("0.18"))
            )
        )
        # Fix 2 & 4: Smart Money Absorption verification
        is_deriv_flow_ok = bool(
            gate_details.get("L6", True)
            and derivatives is not None
            and oi_change_7d is not None
            and oi_change_7d >= Decimal("0.0")
        )
        checklist_verification = ChecklistVerification(
            value_acceptance=va_accepted,
            volatility_structure=(bb_inside_kc or bandwidth_current <= Decimal("0.24")),
            momentum_alignment=(ema20_4h >= ema50_4h and macd_hist_4h >= macd_hist_4h_prev),
            volume_microstructure=(vol_contraction_ratio <= Decimal("1.0") or rvol_4h >= Decimal("1.25")),
            derivatives_positioning=is_deriv_flow_ok,
        )

        # Macro Overextension & Conviction
        coi = Decimal("50.0")
        p_win, conviction = compute_p_win_and_conviction(
            direction=Direction.LONG,
            archetype=archetype,
            quantitative_score=final_score,
            coi=coi,
            checklist_passed=checklist_verification.passed_count,
        )

        # 3-Tier Conditional Mathematical Expectancy (EV_R) and Grade
        ev_r = compute_3tier_ev_r(
            p_win=p_win,
            r_t1=bracket.r_t1,
            r_t2=bracket.r_t2,
            r_t3=bracket.r_t3,
        )
        grade = classify_opportunity_grade(
            ev_r=ev_r,
            conviction=conviction,
            asymmetric_rr=bracket.asymmetric_rr,
            checklist_passed=checklist_verification.passed_count,
        )
        # Universal Invariant 4: Coherent state consistency
        if breakout_status == "HIGH_REJECTION_RISK":
            absorption_score = min(absorption_score, 44.0)
            if grade == OpportunityGrade.GRADE_A:
                grade = OpportunityGrade.GRADE_B

        # Fix 5: Do NOT set breakout_readiness_status = "BREAKOUT_CONFIRMED" when current_price < breakout_trigger or momentum_alignment == False!
        if breakout_status == "BREAKOUT_CONFIRMED" and (close < bracket.breakout_trigger or not checklist_verification.momentum_alignment):
            breakout_status = "RETEST_ONLY_WAIT_FOR_DIP"

        poc_desc = (
            f"Accepted above 30d POC (${float(poc_price):.4f}) | VAH: ${float(vah_price):.4f}"
            if close >= poc_price
            else f"Holding Value Area Base: VAL ${float(val_price):.4f} | POC ${float(poc_price):.4f}"
        )

        anti_chase_str = f"Anti-Chasing Validated: 72h excursion {float(exc_72h*100):.1f}%, dist from high {float(dist_high_72h*100):.1f}%"
        if rsi_1d is not None:
            anti_chase_str += f", 1D RSI {float(rsi_1d):.1f}"
        if rsi_1w is not None:
            anti_chase_str += f", 1W RSI {float(rsi_1w):.1f}"

        # Rationale construction
        rationale: list[str] = [
            f"Archetype: {archetype.value} | Conviction: {float(conviction):.1f} | Grade: {grade.value}",
            f"Execution Mode: {bracket.execution_mode} | Resistance Absorption: {absorption_score:.1f}/100 ({breakout_status})",
            anti_chase_str,
            poc_desc,
        ]
        if vol_contraction_ratio <= Decimal("0.90"):
            rationale.append(f"Volume Dry-Up (7d/14d: {float(vol_contraction_ratio):.2f}x)")
        if oi_change_7d is not None and oi_change_7d >= Decimal("0.05"):
            rationale.append(f"7d Macro OI Growth: {float(oi_change_7d*100):+.1f}%")
        if is_btc_cascade:
            rationale.append(f"WARNING: BTC Velocity Cascade Active ({float(btc_ret_1h*100):.1f}%) — High Long Risk")

        checklist = (
            (
                "Trend Baseline",
                gate_details.get("L1", True),
                f"Holding above 4H EMA50 (${float(ema50_4h):.4f})",
            ),
            (
                "Anti-Chasing Validated",
                gate_details.get("L2", True),
                f"72h range tight ({float(exc_72h*100):.1f}%)"
                + (f" | 1D RSI {float(rsi_1d):.1f}" if rsi_1d is not None else "")
                + (f" | 1W RSI {float(rsi_1w):.1f}" if rsi_1w is not None else ""),
            ),
            (
                "Value Acceptance",
                gate_details.get("L4", True),
                poc_desc,
            ),
            (
                "Volatility Compression",
                gate_details.get("L5", True),
                f"Bandwidth {float(bandwidth_current):.2f}"
                + (" (BB inside KC Squeeze)" if bb_inside_kc else " (Low Bandwidth)"),
            ),
            (
                "Smart Money Absorption",
                is_deriv_flow_ok,
                (
                    "Missing/Degraded derivatives telemetry"
                    if (derivatives is None or oi_change_7d is None)
                    else (
                        f"7d OI Bleeding ({float(oi_change_7d*100):+.1f}%), funding {float((funding or Decimal('0'))*10000):+.2f} bps"
                        if oi_change_7d < Decimal("0.0")
                        else f"7d OI {float(oi_change_7d*100):+.1f}%, funding {float((funding or Decimal('0'))*10000):+.2f} bps"
                    )
                ),
            ),
        )

        breakdown = CryptoScreenerScoreBreakdown(
            trend_score=factor_scores.trend_momentum_score,
            momentum_score=factor_scores.momentum_score,
            derivatives_score=factor_scores.derivatives_score,
            alpha_score=factor_scores.alpha_score,
            volume_score=factor_scores.volume_score,
            structure_score=factor_scores.value_score,
            macro_multiplier=factor_scores.macro_multiplier,
            final_composite_score=factor_scores.final_composite_score,
        )

        max_size_usd = normalize_quantity(
            min(Decimal("250000"), adv_7d * close * Decimal("0.010")),
            inst.quantity_step,
        )

        recent_candles = tuple(
            {
                "time": int(c.closed_at.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in closed_1h[-200:]
        )

        return CryptoOpportunity(
            instrument=inst,
            direction=Direction.LONG,
            regime=CryptoMarketRegime.VOLATILITY_SQUEEZE,
            setup_label=archetype,
            execution_mode=bracket.execution_mode,
            resistance_absorption_score=Decimal(str(absorption_score)),
            breakout_readiness_status=breakout_status,
            score=final_score,
            score_breakdown=breakdown,
            current_price=close,
            atr14=atr14,
            suggested_entry=bracket.suggested_entry,
            suggested_stop=bracket.invalidation_stop,
            suggested_targets=bracket.targets,
            risk_reward_ratio=bracket.asymmetric_rr,
            estimated_win_rate_pct=(p_win * Decimal("100.0")).quantize(Decimal("0.1")),
            expected_value_r=ev_r,
            conviction=conviction,
            grade=grade.value,
            checklist_verification=checklist_verification,
            btc_beta=beta.quantize(Decimal("0.01")),
            residual_return_24h=residual_24h.quantize(Decimal("0.0001")),
            funding_rate=funding,
            oi_change_1h=oi_change,
            taker_ratio=taker_ratio,
            rationale=tuple(rationale),
            macro_narrative=macro_context.narrative(),
            poc_price=poc_price,
            vah_price=vah_price,
            val_price=val_price,
            adv_7d=adv_7d.quantize(Decimal("1")),
            adv_14d=adv_14d.quantize(Decimal("1")),
            volume_contraction_ratio=vol_contraction_ratio.quantize(Decimal("0.01")),
            oi_change_7d=oi_change_7d,
            pullback_entry=bracket.retest_entry,
            breakout_trigger=bracket.breakout_trigger,
            checklist=checklist,
            cvd_24h=cvd_metrics.cvd_24h_change.quantize(Decimal("1")),
            cvd_z_score=cvd_metrics.cvd_z_score,
            orderflow_absorption=cvd_metrics.is_bullish_absorption,
            max_recommended_size_usd=max_size_usd,
            candles=recent_candles,
            is_discovery_tier=is_discovery_tier,
        )

    def _score_distribution_setup(
        self,
        inst: Instrument,
        quote: Quote,
        snap_1h: IndicatorSnapshot,
        closed_1h: Sequence[Candle],
        closed_4h: Sequence[Candle],
        derivatives: DerivativesSnapshot | None,
        macro_context: MacroSentimentContext,
        is_btc_cascade: bool = False,
        btc_ret_1h: Decimal = ZERO,
        beta: Decimal = Decimal("1.0"),
        residual_24h: Decimal = ZERO,
        candles_1d: Sequence[Candle] | None = None,
        candles_1w: Sequence[Candle] | None = None,
        is_discovery_tier: bool = False,
    ) -> CryptoOpportunity | None:
        """Deep institutional short screening engine according to AGENT.md V2.5:

        Evaluates:
        1. 30-Day 1H Triangular Kernel VPVR (720 x 1H bars)
        2. Multi-Timeframe RSI Overbought & Bearish Divergences (4H & Daily)
        3. Multi-Timeframe MACD Bearish Momentum & Deceleration
        4. Setup Archetype Classification (UTAD_DIVERGENCE_SHORT, EXHAUSTION_REVERSAL_SHORT, DISTRIBUTION_SHORT)
        5. Mandatory Screening Gates S1-S5
        6. 5-Level Execution Bracket Construction with Tick-Size Normalization
        7. Regime-Adaptive 5-Factor Scoring, MOEA Conviction, and 3-Tier EV_R
        8. 5-Point Institutional Checklist badges
        """
        close = snap_1h.close
        atr14 = snap_1h.atr14
        closes_4h = [c.close for c in closed_4h]
        if len(closes_4h) < 20:
            return None

        ema20_4h = ema(closes_4h, 20)[-1]
        ema50_4h = ema(closes_4h, 50)[-1] if len(closes_4h) >= 50 else ema20_4h

        # 1. 30-Day VPVR Window (180 4H bars = 720 hours = 30 days)
        vpvr_window = closed_4h[-180:] if len(closed_4h) >= 180 else (closed_1h[-240:] if len(closed_1h) >= 240 else closed_1h)
        vp = volume_profile(vpvr_window, bins=40)
        poc_price = vp.poc_price
        vah_price = vp.vah_price
        val_price = vp.val_price
        high_30d = max(c.high for c in vpvr_window)
        low_30d = min(c.low for c in vpvr_window)
        if low_30d <= ZERO or high_30d <= low_30d:
            return None
        range_height_30d = (high_30d - low_30d) / max(low_30d, Decimal("1e-8"))

        # 2. Multi-Timeframe Candle Resampling & RSI
        d_bars = (
            [c for c in candles_1d if c.closed]
            if (candles_1d and len(candles_1d) >= 15)
            else (resample_candles(closed_4h, "1d") if closed_4h else [])
        )
        closed_1d = d_bars
        w_bars = candles_1w if (candles_1w and len(candles_1w) >= 15) else (resample_candles(closed_1d, "1w") if closed_1d else [])
        closes_1d = [c.close for c in closed_1d]
        closes_1w = [c.close for c in w_bars]

        rsi_4h_series = rsi(closes_4h, 14)
        rsi_4h = rsi_4h_series[-1] if rsi_4h_series else Decimal("50")

        rsi_1d_series = rsi(closes_1d, 14) if len(closes_1d) >= 15 else []
        rsi_1d = rsi_1d_series[-1] if rsi_1d_series else rsi_4h

        rsi_1w_series = rsi(closes_1w, 14) if len(closes_1w) >= 15 else []
        rsi_1w = rsi_1w_series[-1] if rsi_1w_series else (rsi_1d if rsi_1d else rsi_4h)

        # 3. Bearish Divergence Algorithms (RSI & MACD)
        rsi_divs_4h = confirmed_rsi_divergences(closed_4h, lookback=48, min_rsi_delta=Decimal("2.0"))
        has_bearish_rsi_div_4h = any(d.kind == "bearish" for d in rsi_divs_4h)
        macd_divs_4h = confirmed_macd_divergences(closed_4h, lookback=48)
        has_bearish_macd_div_4h = any(d.kind == "bearish" for d in macd_divs_4h)

        rsi_divs_1d = confirmed_rsi_divergences(closed_1d, lookback=30, min_rsi_delta=Decimal("2.0")) if len(closed_1d) >= 20 else ()
        has_bearish_rsi_div_1d = any(d.kind == "bearish" for d in rsi_divs_1d)
        macd_divs_1d = confirmed_macd_divergences(closed_1d, lookback=30) if len(closed_1d) >= 30 else ()
        has_bearish_macd_div_1d = any(d.kind == "bearish" for d in macd_divs_1d)

        has_bearish_divergence = (
            has_bearish_rsi_div_4h or has_bearish_macd_div_4h or has_bearish_rsi_div_1d or has_bearish_macd_div_1d
        )

        # 4. Multi-Timeframe MACD
        macd_line_4h, macd_sig_4h, macd_hist_4h_series = macd(closes_4h)
        macd_hist_4h = macd_hist_4h_series[-1] if macd_hist_4h_series else ZERO
        macd_hist_4h_prev = macd_hist_4h_series[-2] if len(macd_hist_4h_series) >= 2 else macd_hist_4h
        is_4h_macd_bearish_cross = len(macd_line_4h) >= 2 and (macd_line_4h[-1] <= macd_sig_4h[-1])
        is_4h_hist_negative = macd_hist_4h < ZERO
        is_4h_hist_decelerating = len(macd_hist_4h_series) >= 2 and (macd_hist_4h < macd_hist_4h_prev)
        is_1h_macd_bearish = snap_1h.macd_hist < ZERO or snap_1h.macd_line <= snap_1h.macd_signal_line

        # 5. Volatility & Volume Contraction
        bb_upper_4h, bb_mid_4h, bb_lower_4h, bbw_4h = bollinger_bands(closes_4h, 20, Decimal("2.0"))
        kc_upper_4h, kc_mid_4h, kc_lower_4h = keltner_channels(closed_4h, 20, Decimal("1.5"))
        bb_inside_kc = (bb_lower_4h[-1] >= kc_lower_4h[-1]) and (bb_upper_4h[-1] <= kc_upper_4h[-1])

        avg_vol_20 = (
            sum(c.volume for c in closed_4h[-20:]) / Decimal("20")
            if len(closed_4h) >= 20
            else Decimal("1")
        )
        vol_drying = closed_4h[-1].volume < (avg_vol_20 * Decimal("0.95"))
        recent_bars_4h = closed_4h[-8:]
        has_climax_wick = any(
            b.volume > (avg_vol_20 * Decimal("1.3"))
            and (b.high - max(b.open, b.close)) >= ((b.high - b.low) * Decimal("0.35"))
            for b in recent_bars_4h
        )
        upper_wick_ratio_4h = max(
            ((b.high - max(b.open, b.close)) / max(b.high - b.low, Decimal("1e-8")))
            for b in recent_bars_4h
        ) if recent_bars_4h else Decimal("0")
        adv_7d, adv_14d, vol_contraction_ratio = volume_contraction_metrics(closed_1h, 7, 14)

        # 6. Returns, Swings, and Dates
        ret_24h = _return_over(closed_1h, 24)
        ret_7d = _return_over(closed_1h, 168) if len(closed_1h) >= 169 else ZERO
        ret_14d = _return_over(closed_1h, 336) if len(closed_1h) >= 337 else ret_7d
        high_24h = max(c.high for c in closed_1h[-24:]) if len(closed_1h) >= 24 else close
        high_7d = max(c.high for c in closed_1h[-168:]) if len(closed_1h) >= 168 else high_30d

        high_idx = max(range(len(vpvr_window)), key=lambda i: vpvr_window[i].high)
        bars_since_high = len(vpvr_window) - 1 - high_idx
        days_since_30d_high = Decimal(str(bars_since_high)) / Decimal("24")

        local_consolidation_low_4h = min(c.low for c in closed_4h[-12:]) if len(closed_4h) >= 12 else close
        swing_high_4h = max(c.high for c in (closed_4h[-42:] if len(closed_4h) >= 42 else closed_4h))

        # 7. Derivatives positioning & Taker flow
        funding = derivatives.funding_rate if derivatives else None
        oi_change = derivatives.open_interest_change_1h if derivatives else None
        oi_change_7d = derivatives.open_interest_change_7d if derivatives else None
        # Fix 2: Hard veto on Short setups when Stage 2 derivatives has missing 7d OI ("N/A")
        if derivatives is not None and oi_change_7d is None:
            return None
        taker_ratio_calc = taker_buy_ratio_from_candles(closed_1h[-24:] if len(closed_1h) >= 24 else closed_1h)
        if derivatives and derivatives.taker_buy_sell_ratio is not None:
            taker_ratio = derivatives.taker_buy_sell_ratio
        else:
            taker_ratio = taker_ratio_calc
        taker_buy_ratio_24h = taker_ratio if taker_ratio is not None else Decimal("1.0000")

        # Position in range & run-up
        range_span_30d = high_30d - low_30d
        pos_in_range = (close - low_30d) / max(range_span_30d, Decimal("1e-8"))
        runup_30d = (high_30d - low_30d) / max(low_30d, Decimal("1e-8"))

        open_4h = closed_4h[-1].open if closed_4h else close
        high_4h = closed_4h[-1].high if closed_4h else high_24h
        low_4h = closed_4h[-1].low if closed_4h else close
        low_72h = min(c.low for c in closed_1h[-72:]) if len(closed_1h) >= 72 else low_30d

        basis_bps = (
            derivatives.basis_rate * Decimal("10000.0")
            if (derivatives and derivatives.basis_rate is not None)
            else Decimal("0")
        )

        # 8. Setup Archetype Classification (Priority 1 to 3)
        short_features = ShortArchetypeFeatures(
            close=close,
            high_24h=high_24h,
            high_30d=high_30d,
            ema50_4h=ema50_4h,
            poc_30d=poc_price,
            atr14_4h=atr14,
            has_bearish_div=has_bearish_divergence,
            rsi_4h=rsi_4h,
            rsi_1d=rsi_1d,
            rsi_1w=rsi_1w,
            oi_delta_7d=oi_change_7d,
            upper_wick_ratio_4h=upper_wick_ratio_4h,
            max_ret_30d=range_height_30d,
            taker_buy_ratio_24h=taker_buy_ratio_24h,
            macd_hist_4h=macd_hist_4h,
            pos_in_range_30d=pos_in_range,
            macd_hist_4h_prev=macd_hist_4h_prev,
            runup_30d=runup_30d,
            ret_14d=ret_14d,
        )
        archetype = classify_short_archetype(short_features)
        if archetype is None:
            return None

        # Stage 2 Gate S5 Trapped Longs Guard: Missing Stage 2 derivatives telemetry cannot falsely satisfy crowding gate
        if derivatives is not None and getattr(derivatives, "degraded_telemetry", False):
            if funding is None and oi_change_7d is None:
                return None

        # 9. Mandatory Domain Screening Gates S1-S5 (Two-Track Precision Spec)
        gate_inputs = ShortGateInputs(
            close=close,
            ema50_4h=ema50_4h,
            high_7d=high_7d,
            range_height_30d=range_height_30d,
            ret_7d=ret_7d,
            ret_14d=ret_14d,
            high_30d=high_30d,
            low_30d=low_30d,
            days_since_30d_high=days_since_30d_high,
            ema20_4h=ema20_4h,
            atr14_4h=atr14,
            ret_24h=ret_24h,
            rsi_1h=snap_1h.rsi14,
            rsi_4h=rsi_4h,
            has_bearish_div=has_bearish_divergence,
            has_climax_blowoff=(rsi_1d >= Decimal("76.0")) and (upper_wick_ratio_4h >= Decimal("0.45")),
            poc_30d=poc_price,
            macd_hist_4h=macd_hist_4h,
            taker_buy_ratio_24h=taker_buy_ratio_24h,
            funding_rate_8h=funding,
            oi_delta_7d=oi_change_7d,
            oi_delta_1h=oi_change,
            taker_buy_ratio_1h=taker_ratio,
            low_72h=low_72h,
            high_24h=high_24h,
            open_4h=open_4h,
            high_4h=high_4h,
            low_4h=low_4h,
            rsi_1d=rsi_1d,
            macd_hist_4h_prev=macd_hist_4h_prev,
            basis_bps=basis_bps,
            runup_30d=runup_30d,
        )
        gates_passed, gate_details = evaluate_short_gates(gate_inputs, archetype)
        if not gates_passed:
            return None

        # 10. 5-Level Execution Bracket Construction with Tick-Size Normalization
        bracket = build_short_bracket(
            close=close,
            ema50_4h=ema50_4h,
            poc_30d=poc_price,
            atr14_4h=atr14,
            local_consolidation_low_4h=local_consolidation_low_4h,
            swing_high_4h=swing_high_4h,
            low_30d=low_30d,
            vah_30d=vah_price,
            val_30d=val_price,
            tick_size=inst.price_tick,
        )

        # 11. Multi-Factor Scoring (0 to 100 via Continuous Short Factor Engine)
        adx_subscore = calculate_adx_subscore(
            archetype, snap_1h.adx14, snap_1h.momentum_reference_adx14, snap_1h.minus_di, snap_1h.plus_di
        )
        closes_4h_calc = [c.close for c in closed_4h]
        ema200_4h = ema(closes_4h_calc, 200)[-1] if len(closes_4h_calc) >= 200 else ema(closes_4h_calc, len(closes_4h_calc))[-1]
        dist_to_trigger = (close - bracket.breakout_trigger) / max(close, Decimal("1e-8"))
        dist_to_entry = abs(close - bracket.suggested_entry) / max(close, Decimal("1e-8"))
        if dist_to_entry > Decimal("0.045"):
            return None
        if bracket.asymmetric_rr < Decimal("1.40"):
            return None
        cvd_val = Decimal(str(quote.cvd_zscore)) if hasattr(quote, "cvd_zscore") and quote.cvd_zscore is not None else Decimal("0.0")

        factor_scores = calculate_continuous_short_factor_scores(
            bandwidth_4h=bbw_4h[-1] if bbw_4h else Decimal("0.20"),
            bb_inside_kc=bb_inside_kc,
            vol_contraction_ratio=vol_contraction_ratio,
            close=close,
            poc_30d=poc_price,
            val_30d=val_price,
            vah_30d=vah_price,
            pos_in_range_30d=pos_in_range,
            ema50_4h=ema50_4h,
            ema200_4h=ema200_4h,
            has_bearish_div=has_bearish_divergence,
            cvd_zscore=cvd_val,
            oi_change_7d=oi_change_7d,
            oi_change_4h=oi_change,
            taker_buy_ratio_4h=taker_buy_ratio_24h,
            funding_rate=funding,
            ema20_4h=ema20_4h,
            macd_hist_4h=macd_hist_4h,
            macd_hist_4h_prev=macd_hist_4h_prev,
            adx_subscore=adx_subscore,
            btc_residual_alpha=residual_24h,
            rvol_4h=snap_1h.relative_volume,
            asymmetric_rr=bracket.asymmetric_rr,
            dist_to_trigger=dist_to_trigger,
            macro_multiplier=macro_context.multiplier_for(Direction.SHORT),
            is_btc_cascade=is_btc_cascade,
            upper_wick_ratio_4h=upper_wick_ratio_4h,
            dist_to_entry=dist_to_entry,
            oi_change_1h=derivatives.open_interest_change_1h if derivatives else None,
        )

        final_score = factor_scores.final_composite_score

        # 12. 5-Point Institutional Checklist Badges (Section 8)
        primary_ceiling = max(ema50_4h, poc_price)
        dist_to_ceiling = (primary_ceiling - close) / max(close, Decimal("1e-8"))
        is_active_retest = Decimal("-0.015") <= dist_to_ceiling <= Decimal("0.050")

        track_a_pass = (
            (pos_in_range >= Decimal("0.65"))
            and (close <= high_24h - Decimal("0.45") * atr14)
            and (
                (rsi_1d >= Decimal("72.0") and upper_wick_ratio_4h >= Decimal("0.35"))
                or (has_bearish_divergence and macd_hist_4h < macd_hist_4h_prev)
            )
        )
        track_b_pass = (
            (close <= primary_ceiling * Decimal("1.01"))
            and is_active_retest
            and (macd_hist_4h < ZERO and macd_hist_4h <= macd_hist_4h_prev)
            and (taker_buy_ratio_24h <= Decimal("0.92"))
        )
        trapped_longs_check = (
            (funding is not None and funding >= Decimal("0.00005"))
            or (basis_bps >= Decimal("4.0"))
        ) and ((oi_change_7d or ZERO) >= Decimal("0.10") or taker_buy_ratio_24h <= Decimal("0.92"))
        squeeze_flush_check = (
            (funding is not None and funding <= Decimal("-0.0003"))
            and ((oi_change_7d or ZERO) >= Decimal("0.25"))
            and ((oi_change or ZERO) <= Decimal("-0.015"))
            and (taker_buy_ratio_24h <= Decimal("0.85"))
        )

        is_utad_or_climax = archetype in (SetupLabel.UTAD_DIVERGENCE_SHORT, SetupLabel.EXHAUSTION_REVERSAL_SHORT)
        if is_utad_or_climax:
            item1_title = "MTF Overbought Exhaustion"
            is_item1_valid = bool(
                rsi_4h >= Decimal("65.0") or rsi_1d >= Decimal("68.0") or (rsi_4h >= Decimal("58.0") and has_bearish_divergence)
            )
            item1_desc = (
                f"4H: {float(rsi_4h):.1f} | 1D: {float(rsi_1d):.1f} (Exhaustion confirmed)"
                if is_item1_valid
                else f"4H: {float(rsi_4h):.1f} | 1D: {float(rsi_1d):.1f} (Not Overbought)"
            )
        else:
            item1_title = "Resistance Retest Ceiling"
            is_item1_valid = bool(is_active_retest)
            item1_desc = (
                f"Retesting 30d POC (${float(poc_price):.4f}) / EMA50 (${float(ema50_4h):.4f}) ceiling (within {float(dist_to_ceiling*100):.1f}%)"
                if is_item1_valid
                else f"Ceiling distance {float(dist_to_ceiling*100):.1f}% outside retest corridor"
            )

        is_div_valid = bool(has_bearish_divergence)
        is_macd_valid = bool(is_4h_macd_bearish_cross or is_4h_hist_negative or is_1h_macd_bearish)
        is_vol_valid = bool(vol_drying or has_climax_wick or is_4h_hist_decelerating)
        is_deriv_valid = bool(trapped_longs_check or squeeze_flush_check or (funding is not None and funding >= Decimal("0.00005")))

        checklist_verification = ChecklistVerification(
            value_acceptance=bool(close <= (high_24h - Decimal("0.45") * atr14) or (close <= primary_ceiling * Decimal("1.01") and pos_in_range >= Decimal("0.45"))),
            volatility_structure=bool(track_a_pass or track_b_pass),
            momentum_alignment=bool(macd_hist_4h < macd_hist_4h_prev or macd_hist_4h < ZERO),
            volume_microstructure=bool(upper_wick_ratio_4h >= Decimal("0.35") or taker_buy_ratio_24h <= Decimal("0.92")),
            derivatives_positioning=is_deriv_valid,
        )

        # 13. Composite Conviction, 3-Tier EV_R, and Opportunity Grade
        coi = Decimal("50.0")
        p_win, conviction = compute_p_win_and_conviction(
            direction=Direction.SHORT,
            archetype=archetype,
            quantitative_score=final_score,
            coi=coi,
            checklist_passed=checklist_verification.passed_count,
        )
        ev_r = compute_3tier_ev_r(
            p_win=p_win,
            r_t1=bracket.r_t1,
            r_t2=bracket.r_t2,
            r_t3=bracket.r_t3,
        )
        grade = classify_opportunity_grade(
            ev_r=ev_r,
            conviction=conviction,
            asymmetric_rr=bracket.asymmetric_rr,
            checklist_passed=checklist_verification.passed_count,
        )

        # 14. Rationale Construction
        rationale: list[str] = [
            f"Archetype: {archetype.value} | Conviction: {float(conviction):.1f} | Grade: {grade.value}",
            f"MTF RSI: 1W {float(rsi_1w):.1f} | 1D {float(rsi_1d):.1f} | 4H {float(rsi_4h):.1f}",
            f"Distribution Ceiling: 30d POC (${float(poc_price):.4f}) | VAH: ${float(vah_price):.4f}",
        ]
        if has_bearish_divergence:
            rationale.append("Confirmed 4H/1D Regular Bearish Divergence (RSI/MACD)")
        if is_4h_macd_bearish_cross:
            rationale.append("4H MACD Bearish Crossover Below Signal Line")
        if is_btc_cascade:
            rationale.append(f"Macro Tailwind: BTC Flash Dump Active ({float(btc_ret_1h*100):.1f}%)")

        div_desc = "Confirmed 4H/1D Bearish Divergence (RSI & MACD)" if has_bearish_divergence else "No Confirmed Divergence"
        macd_desc = "4H MACD crossed below signal line | Hist decelerating" if is_4h_macd_bearish_cross else ("MACD histogram negative" if is_4h_hist_negative else "Bearish MACD momentum active")
        vol_desc = "Effort vs Result: Volume drying on push, seller dominance" if (vol_drying or has_climax_wick) else f"Volume ratio {float(vol_contraction_ratio):.2f}"
        deriv_desc = (
            f"7d OI {float((oi_change_7d or Decimal('0'))*100):+.1f}%, funding {float((funding or Decimal('0'))*10000):+.2f} bps"
            if derivatives
            else "Derivatives positioning neutral"
        )

        checklist = (
            (item1_title, is_item1_valid, item1_desc),
            ("Bearish Divergence", is_div_valid, div_desc),
            ("MACD Bearish Crossover", is_macd_valid, macd_desc),
            ("Volume & Momentum Exhaustion", is_vol_valid, vol_desc),
            ("Trapped Longs / Funding", is_deriv_valid, deriv_desc),
        )

        breakdown = CryptoScreenerScoreBreakdown(
            trend_score=factor_scores.trend_breakdown_score,
            momentum_score=factor_scores.momentum_score,
            derivatives_score=factor_scores.derivatives_score,
            alpha_score=factor_scores.alpha_score,
            volume_score=factor_scores.volume_score,
            structure_score=factor_scores.value_rejection_score,
            macro_multiplier=factor_scores.macro_multiplier,
            final_composite_score=factor_scores.final_composite_score,
        )

        cvd_metrics = cumulative_volume_delta_metrics(closed_1h, lookback_window=24)
        max_size_usd = min(Decimal("250000"), (adv_7d * close * Decimal("0.010")).quantize(Decimal("1000")))
        if cvd_metrics.is_bearish_distribution:
            rationale.append(f"Institutional Distribution Footprint: 24h CVD negative ({cvd_metrics.cvd_z_score:+.1f}z) with price compressed")

        recent_candles = tuple(
            {
                "time": int(c.closed_at.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in closed_1h[-200:]
        )

        return CryptoOpportunity(
            instrument=inst,
            direction=Direction.SHORT,
            regime=CryptoMarketRegime.VOLATILITY_SQUEEZE if bb_inside_kc else CryptoMarketRegime.MEAN_REVERTING_RANGE,
            setup_label=archetype,
            score=final_score,
            score_breakdown=breakdown,
            current_price=close,
            atr14=atr14,
            suggested_entry=bracket.suggested_entry,
            suggested_stop=bracket.invalidation_stop,
            suggested_targets=bracket.targets,
            risk_reward_ratio=bracket.asymmetric_rr,
            estimated_win_rate_pct=(p_win * Decimal("100.0")).quantize(Decimal("0.1")),
            expected_value_r=ev_r,
            conviction=conviction,
            grade=grade.value,
            checklist_verification=checklist_verification,
            btc_beta=beta.quantize(Decimal("0.01")),
            residual_return_24h=residual_24h.quantize(Decimal("0.0001")),
            funding_rate=funding,
            oi_change_1h=oi_change,
            taker_ratio=taker_ratio,
            rationale=tuple(rationale),
            macro_narrative=macro_context.narrative(),
            poc_price=poc_price,
            vah_price=vah_price,
            val_price=val_price,
            adv_7d=adv_7d.quantize(Decimal("1")),
            adv_14d=adv_14d.quantize(Decimal("1")),
            volume_contraction_ratio=vol_contraction_ratio.quantize(Decimal("0.01")),
            oi_change_7d=oi_change_7d,
            pullback_entry=bracket.retest_entry,
            breakout_trigger=bracket.breakout_trigger,
            checklist=checklist,
            cvd_24h=cvd_metrics.cvd_24h_change.quantize(Decimal("1")),
            cvd_z_score=cvd_metrics.cvd_z_score,
            orderflow_absorption=cvd_metrics.is_bearish_distribution,
            max_recommended_size_usd=max_size_usd,
            candles=recent_candles,
            is_discovery_tier=is_discovery_tier,
        )

    def _calc_residual_alpha(
        self,
        asset_candles: Sequence[Candle],
        btc_candles: Sequence[Candle],
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """Compute rolling 7-day beta and 6h, 24h, 7d residual returns against BTC."""
        if len(asset_candles) < 25 or len(btc_candles) < 25:
            return Decimal("1.0"), Decimal("0.8"), ZERO, ZERO, ZERO

        lookback = min(len(asset_candles) - 1, len(btc_candles) - 1, 168)
        beta, corr = beta_correlation(asset_candles, btc_candles, lookback=lookback)
        if beta is None:
            beta = Decimal("1.0")
        if corr is None:
            corr = Decimal("0.8")

        ret_6h = _return_over(asset_candles, 6) if len(asset_candles) >= 7 else ZERO
        ret_24h = _return_over(asset_candles, 24) if len(asset_candles) >= 25 else ZERO
        ret_7d = _return_over(asset_candles, 168) if len(asset_candles) >= 169 else ret_24h

        btc_6h = _return_over(btc_candles, 6) if len(btc_candles) >= 7 else ZERO
        btc_24h = _return_over(btc_candles, 24) if len(btc_candles) >= 25 else ZERO
        btc_7d = _return_over(btc_candles, 168) if len(btc_candles) >= 169 else btc_24h

        res_6h = ret_6h - beta * btc_6h
        res_24h = ret_24h - beta * btc_24h
        res_7d = ret_7d - beta * btc_7d

        return beta, corr, res_24h, res_6h, res_7d

    def _classify_regime(
        self,
        snap: IndicatorSnapshot,
        ema20_4h: Decimal,
        ema50_4h: Decimal,
    ) -> CryptoMarketRegime:
        """Classify crypto market regime using multi-TF EMAs, ADX, and Choppiness."""
        close = snap.close
        chop = snap.choppiness
        adx14 = snap.adx14

        # 1. Squeeze / Low Volatility Compression
        if chop >= Decimal("62") and adx14 < Decimal("20"):
            return CryptoMarketRegime.VOLATILITY_SQUEEZE

        # 2. Strong Trend Up
        if (
            snap.ema9 > snap.ema20 > snap.ema50 > snap.ema200
            and close > snap.ema20
            and adx14 >= Decimal("25")
            and snap.plus_di > snap.minus_di
            and chop < Decimal("50")
            and ema20_4h > ema50_4h
        ):
            return CryptoMarketRegime.STRONG_TREND_UP

        # 3. Strong Trend Down
        if (
            snap.ema9 < snap.ema20 < snap.ema50 < snap.ema200
            and close < snap.ema20
            and adx14 >= Decimal("25")
            and snap.minus_di > snap.plus_di
            and chop < Decimal("50")
            and ema20_4h < ema50_4h
        ):
            return CryptoMarketRegime.STRONG_TREND_DOWN

        # 4. Emerging Trend Up
        if (
            snap.ema20 > snap.ema50
            and close > snap.ema20
            and snap.plus_di > snap.minus_di
            and (adx14 >= Decimal("18") or snap.momentum_normalized > ZERO)
        ):
            return CryptoMarketRegime.EMERGING_TREND_UP

        # 5. Emerging Trend Down
        if (
            snap.ema20 < snap.ema50
            and close < snap.ema20
            and snap.minus_di > snap.plus_di
            and (adx14 >= Decimal("18") or snap.momentum_normalized < ZERO)
        ):
            return CryptoMarketRegime.EMERGING_TREND_DOWN

        # 6. Mean-Reverting Range
        if adx14 < Decimal("20") and chop >= Decimal("55"):
            return CryptoMarketRegime.MEAN_REVERTING_RANGE

        return CryptoMarketRegime.CHOPPY_NOISE

    def _score_setup(
        self,
        direction: Direction,
        inst: Instrument,
        quote: Quote,
        snap_1h: IndicatorSnapshot,
        ema20_4h: Decimal,
        ema50_4h: Decimal,
        rsi_4h: Decimal,
        regime: CryptoMarketRegime,
        support: Decimal | None,
        resistance: Decimal | None,
        struct_trend: str,
        beta: Decimal,
        residual_24h: Decimal,
        residual_6h: Decimal,
        residual_7d: Decimal,
        derivatives: DerivativesSnapshot | None,
        macro_context: MacroSentimentContext,
        closed_1h: Sequence[Candle],
        closed_4h: Sequence[Candle] = (),
        is_discovery_tier: bool = False,
    ) -> CryptoOpportunity | None:
        """Score a specific directional setup and identify setup label and execution bracket."""
        is_long = direction is Direction.LONG
        close = snap_1h.close
        atr14 = snap_1h.atr14
        rationale: list[str] = []

        # --- Anti-Chasing & Value Acceptance Hard Gates ---
        if is_long:
            # 1. Reject already-exploded parabolic blow-off pumps (e.g. WUSDT +55%, GTCUSDT +73%)
            ret_24h = _return_over(closed_1h, 24)
            if ret_24h > Decimal("0.20") or close > (snap_1h.ema20 * Decimal("1.10")):
                return None
            if len(closed_1h) >= 72:
                last_72h = closed_1h[-72:]
                high_72h = max(c.high for c in last_72h)
                low_72h = min(c.low for c in last_72h)
                if low_72h > ZERO and ((high_72h - low_72h) / low_72h) > Decimal("0.30"):
                    return None

            # 2. Mandatory Value Acceptance: Price MUST be accepted at or above 30d POC
            # Tokens trading below POC (e.g. NILUSDT) have trapped overhead supply -> reject!
            if closed_4h:
                base_window = closed_4h[-120:] if len(closed_4h) >= 120 else closed_4h
                vp = volume_profile(base_window, bins=50)
                if close < (vp.poc_price * Decimal("0.985")):
                    return None
        else:
            # Short Anti-Chasing & Floor Blacklist Hard Gates:
            # 1. Reject already dumped tokens (> 18% in 24h or > 35% excursion in 72h)
            ret_24h = _return_over(closed_1h, 24)
            if ret_24h < Decimal("-0.18") or close < (snap_1h.ema20 * Decimal("0.90")):
                return None
            if len(closed_1h) >= 72:
                last_72h = closed_1h[-72:]
                high_72h = max(c.high for c in last_72h)
                low_72h = min(c.low for c in last_72h)
                if low_72h > ZERO and ((high_72h - low_72h) / low_72h) > Decimal("0.35"):
                    return None
            # 2. Macro Overextension & Upper Range: Never short tokens in the lower half of range or without prior run-up
            if closed_4h:
                base_window = closed_4h[-120:] if len(closed_4h) >= 120 else closed_4h
                high_30d = max(c.high for c in base_window)
                low_30d = min(c.low for c in base_window)
                if high_30d > low_30d:
                    pos_in_range = (close - low_30d) / (high_30d - low_30d)
                    range_height = (high_30d - low_30d) / low_30d
                    if pos_in_range < Decimal("0.50"):
                        return None
                    if range_height < Decimal("0.18"):
                        return None

        # --- 1. Trend Score (0-100) ---
        trend_score = ZERO
        if is_long:
            if close > snap_1h.ema20:
                trend_score += Decimal("20")
            if snap_1h.ema20 > snap_1h.ema50:
                trend_score += Decimal("20")
            if snap_1h.ema50 > snap_1h.ema200:
                trend_score += Decimal("20")
            if ema20_4h > ema50_4h:
                trend_score += Decimal("20")
            if snap_1h.plus_di > snap_1h.minus_di:
                trend_score += Decimal("10")
            if snap_1h.adx14 >= Decimal("25"):
                trend_score += Decimal("10")
        else:
            if close < snap_1h.ema20:
                trend_score += Decimal("20")
            if snap_1h.ema20 < snap_1h.ema50:
                trend_score += Decimal("20")
            if snap_1h.ema50 < snap_1h.ema200:
                trend_score += Decimal("20")
            if ema20_4h < ema50_4h:
                trend_score += Decimal("20")
            if snap_1h.minus_di > snap_1h.plus_di:
                trend_score += Decimal("10")
            if snap_1h.adx14 >= Decimal("25"):
                trend_score += Decimal("10")

        # --- 2. Momentum Score (0-100) ---
        mom_score = ZERO
        if is_long:
            if snap_1h.momentum_normalized > ZERO:
                mom_score += Decimal("30")
            if snap_1h.macd_hist > snap_1h.momentum_reference_macd_hist:
                mom_score += Decimal("25")
            if Decimal("50") <= snap_1h.rsi14 <= Decimal("68"):
                mom_score += Decimal("25")
            elif snap_1h.rsi14 > Decimal("68") and snap_1h.rsi14 <= Decimal("75"):
                mom_score += Decimal("10")
            if snap_1h.macd_line > snap_1h.macd_signal_line:
                mom_score += Decimal("20")
        else:
            if snap_1h.momentum_normalized < ZERO:
                mom_score += Decimal("30")
            if snap_1h.macd_hist < snap_1h.momentum_reference_macd_hist:
                mom_score += Decimal("25")
            if Decimal("32") <= snap_1h.rsi14 <= Decimal("50"):
                mom_score += Decimal("25")
            elif snap_1h.rsi14 < Decimal("32") and snap_1h.rsi14 >= Decimal("25"):
                mom_score += Decimal("10")
            if snap_1h.macd_line < snap_1h.macd_signal_line:
                mom_score += Decimal("20")

        # --- 3. Derivatives Score (0-100) ---
        deriv_score = Decimal("50")  # neutral baseline if no derivatives data
        funding_rate = derivatives.funding_rate if derivatives else None
        oi_change = derivatives.open_interest_change_1h if derivatives else None
        taker_ratio = derivatives.taker_buy_sell_ratio if derivatives else None

        if derivatives is not None:
            deriv_score = ZERO
            # Funding rate contrarian dynamics:
            # Negative funding (< 0) in long = shorts paying longs, massive squeeze fuel
            if derivatives.funding_rate is not None:
                if is_long:
                    if derivatives.funding_rate < Decimal("-0.0001"):
                        deriv_score += Decimal("35")
                        rationale.append(f"Shorts paying longs: funding {float(derivatives.funding_rate*10000):.1f} bps (Squeeze fuel)")
                    elif derivatives.funding_rate <= Decimal("0.0001"):
                        deriv_score += Decimal("25")
                    elif derivatives.funding_rate > Decimal("0.0005"):
                        # Heavy long crowdedness
                        deriv_score += Decimal("5")
                    else:
                        deriv_score += Decimal("15")
                else:
                    if derivatives.funding_rate > Decimal("0.0005"):
                        deriv_score += Decimal("35")
                        rationale.append(f"Longs paying shorts: funding {float(derivatives.funding_rate*10000):.1f} bps (Long trap)")
                    elif derivatives.funding_rate >= Decimal("0"):
                        deriv_score += Decimal("25")
                    elif derivatives.funding_rate < Decimal("-0.0003"):
                        deriv_score += Decimal("5")
                    else:
                        deriv_score += Decimal("15")

            # Open Interest velocity
            if derivatives.open_interest_change_1h is not None:
                oi_chg = derivatives.open_interest_change_1h
                if is_long:
                    if oi_chg > Decimal("0.02") and close > snap_1h.ema20:
                        deriv_score += Decimal("30")
                        rationale.append(f"OI expanding with price (+{float(oi_chg*100):.1f}% 1h)")
                    elif oi_chg < Decimal("-0.02"):
                        deriv_score += Decimal("15")
                else:
                    if oi_chg > Decimal("0.02") and close < snap_1h.ema20:
                        deriv_score += Decimal("30")
                        rationale.append(f"OI expanding into downtrend (+{float(oi_chg*100):.1f}% 1h)")
                    elif oi_chg < Decimal("-0.02"):
                        deriv_score += Decimal("15")

            # Taker Buy/Sell ratio
            if is_long:
                if derivatives.taker_buy_sell_ratio >= Decimal("1.15"):
                    deriv_score += Decimal("20")
                    rationale.append(f"Taker buy aggression ({float(derivatives.taker_buy_sell_ratio):.2f})")
                elif derivatives.taker_buy_sell_ratio >= Decimal("1.0"):
                    deriv_score += Decimal("10")
            else:
                if derivatives.taker_buy_sell_ratio <= Decimal("0.85"):
                    deriv_score += Decimal("20")
                    rationale.append(f"Taker sell aggression ({float(derivatives.taker_buy_sell_ratio):.2f})")
                elif derivatives.taker_buy_sell_ratio <= Decimal("1.0"):
                    deriv_score += Decimal("10")

            # Depth imbalance
            if is_long and derivatives.depth_imbalance > Decimal("0.10") or not is_long and derivatives.depth_imbalance < Decimal("-0.10"):
                deriv_score += Decimal("15")
            else:
                deriv_score += Decimal("5")

        # --- 4. Alpha Score (BTC-Residual Alpha) (0-100) ---
        alpha_score = ZERO
        if is_long:
            if residual_24h > Decimal("0.03"):
                alpha_score += Decimal("40")
                rationale.append(f"Strong 24h BTC-residual alpha (+{float(residual_24h*100):.1f}%)")
            elif residual_24h > ZERO:
                alpha_score += Decimal("25")
            if residual_6h > Decimal("0.01"):
                alpha_score += Decimal("30")
            elif residual_6h > ZERO:
                alpha_score += Decimal("15")
            if residual_7d > ZERO:
                alpha_score += Decimal("20")
            if beta < Decimal("1.2"):
                alpha_score += Decimal("10")
        else:
            if residual_24h < Decimal("-0.03"):
                alpha_score += Decimal("40")
                rationale.append(f"Severe 24h underperformance vs BTC ({float(residual_24h*100):.1f}%)")
            elif residual_24h < ZERO:
                alpha_score += Decimal("25")
            if residual_6h < Decimal("-0.01"):
                alpha_score += Decimal("30")
            elif residual_6h < ZERO:
                alpha_score += Decimal("15")
            if residual_7d < ZERO:
                alpha_score += Decimal("20")
            if beta > Decimal("1.0"):
                alpha_score += Decimal("10")

        # --- 5. Volume Score (0-100) ---
        vol_score = ZERO
        part_ratio = _participation_ratio(closed_1h)
        if snap_1h.relative_volume >= Decimal("1.5"):
            vol_score += Decimal("50")
            rationale.append(f"Volume surge ({float(snap_1h.relative_volume):.1f}x 20-bar avg)")
        elif snap_1h.relative_volume >= Decimal("1.1"):
            vol_score += Decimal("30")
        if part_ratio >= Decimal("1.25"):
            vol_score += Decimal("30")
        if quote.quote_volume_24h > Decimal("5000000"):
            vol_score += Decimal("20")

        # --- 6. Structure Score & Key Levels (0-100) ---
        struct_score = Decimal("50")
        opposing_level = resistance if is_long else support
        if opposing_level is not None and atr14 > ZERO:
            dist_to_opposing = (
                (opposing_level - close) if is_long else (close - opposing_level)
            ) / atr14
            if dist_to_opposing >= Decimal("2.5"):
                struct_score = Decimal("90")
                rationale.append(f"Ample room to run ({float(dist_to_opposing):.1f} ATR to opposing level)")
            elif dist_to_opposing >= Decimal("1.5"):
                struct_score = Decimal("70")
            else:
                struct_score = Decimal("30")

        # --- Setup Label Identification ---
        setup = SetupLabel.NO_SETUP
        if is_long:
            # 1. Squeeze Long
            if (
                funding_rate is not None
                and funding_rate < Decimal("-0.0001")
                and oi_change is not None
                and oi_change > Decimal("0.01")
                and close > snap_1h.ema20
            ):
                setup = SetupLabel.SQUEEZE_LONG
                rationale.append("High-probability Short Squeeze Setup")
            # 2. Breakout Long
            elif (
                regime in (CryptoMarketRegime.STRONG_TREND_UP, CryptoMarketRegime.VOLATILITY_SQUEEZE)
                and close > snap_1h.ema9
                and snap_1h.relative_volume >= Decimal("1.25")
                and snap_1h.momentum_normalized > ZERO
            ):
                setup = SetupLabel.BREAKOUT_LONG
                rationale.append("Trend Breakout with volume expansion")
            # 3. Continuation Long
            elif (
                regime is CryptoMarketRegime.STRONG_TREND_UP
                and close >= snap_1h.ema20
                and snap_1h.rsi14 >= Decimal("48")
                and residual_24h > ZERO
            ):
                setup = SetupLabel.CONTINUATION_LONG
                rationale.append("Trend Continuation on EMA20 pullback")
            # 4. Momentum Emerging Long
            elif (
                regime is CryptoMarketRegime.EMERGING_TREND_UP
                and snap_1h.momentum_normalized > ZERO
                and snap_1h.macd_hist > ZERO
            ):
                setup = SetupLabel.MOMENTUM_EMERGING_LONG
                rationale.append("Early phase momentum transition")
            # 5. Range Fade Long
            elif (
                regime is CryptoMarketRegime.MEAN_REVERTING_RANGE
                and snap_1h.rsi14 <= Decimal("35")
                and support is not None
                and abs(close - support) <= Decimal("0.8") * atr14
            ):
                setup = SetupLabel.RANGE_FADE_LONG
                rationale.append("Mean-reversion bounce off key support")
        else:
            # Short setups
            if (
                funding_rate is not None
                and funding_rate > Decimal("0.0005")
                and oi_change is not None
                and oi_change > Decimal("0.01")
                and close < snap_1h.ema20
            ):
                setup = SetupLabel.SQUEEZE_SHORT
                rationale.append("High-probability Long Liquidation Flush Setup")
            elif (
                regime in (CryptoMarketRegime.STRONG_TREND_DOWN, CryptoMarketRegime.VOLATILITY_SQUEEZE)
                and close < snap_1h.ema9
                and snap_1h.relative_volume >= Decimal("1.25")
                and snap_1h.momentum_normalized < ZERO
            ):
                setup = SetupLabel.BREAKOUT_SHORT
                rationale.append("Downtrend Breakdown with volume expansion")
            elif (
                regime is CryptoMarketRegime.STRONG_TREND_DOWN
                and close <= snap_1h.ema20
                and snap_1h.rsi14 <= Decimal("52")
                and residual_24h < ZERO
            ):
                setup = SetupLabel.CONTINUATION_SHORT
                rationale.append("Downtrend Continuation on EMA20 rally")
            elif (
                regime is CryptoMarketRegime.EMERGING_TREND_DOWN
                and snap_1h.momentum_normalized < ZERO
                and snap_1h.macd_hist < ZERO
            ):
                setup = SetupLabel.MOMENTUM_EMERGING_SHORT
                rationale.append("Early phase bearish acceleration")
            elif (
                regime is CryptoMarketRegime.MEAN_REVERTING_RANGE
                and snap_1h.rsi14 >= Decimal("65")
                and resistance is not None
                and abs(resistance - close) <= Decimal("0.8") * atr14
            ):
                setup = SetupLabel.RANGE_FADE_SHORT
                rationale.append("Mean-reversion rejection at key resistance")

        if setup is SetupLabel.NO_SETUP:
            return None

        # --- Composite Scoring (0-100) ---
        raw_score = (
            trend_score * Decimal("0.25")
            + mom_score * Decimal("0.20")
            + deriv_score * Decimal("0.20")
            + alpha_score * Decimal("0.20")
            + vol_score * Decimal("0.10")
            + struct_score * Decimal("0.05")
        )

        macro_multiplier = macro_context.multiplier_for(direction)
        composite_score = raw_score * macro_multiplier
        if composite_score > HUNDRED:
            composite_score = HUNDRED
        if composite_score < ZERO:
            composite_score = ZERO

        composite_score = composite_score.quantize(Decimal("0.1"))

        # --- Invalidation Stop & Targets Calculation ---
        stop_dist = atr14 * Decimal("1.5")
        if is_long:
            suggested_stop = close - stop_dist
            if support is not None and support < close and (close - support) < (Decimal("2.0") * atr14):
                suggested_stop = min(suggested_stop, support - (Decimal("0.2") * atr14))
            target_1r = close + stop_dist
            target_2r = close + stop_dist * Decimal("2.0")
            target_3r = close + stop_dist * Decimal("3.0")
            targets = (target_1r.quantize(close), target_2r.quantize(close), target_3r.quantize(close))
        else:
            suggested_stop = close + stop_dist
            if resistance is not None and resistance > close and (resistance - close) < (Decimal("2.0") * atr14):
                suggested_stop = max(suggested_stop, resistance + (Decimal("0.2") * atr14))
            target_1r = max(close * Decimal("0.70"), close - stop_dist)
            target_2r = max(close * Decimal("0.50"), min(target_1r * Decimal("0.95"), close - stop_dist * Decimal("2.0")))
            target_3r = max(close * Decimal("0.30"), min(target_2r * Decimal("0.90"), close - stop_dist * Decimal("3.0")))
            targets = (target_1r.quantize(close), target_2r.quantize(close), target_3r.quantize(close))

        suggested_stop = suggested_stop.quantize(close)
        risk = max(Decimal("0.0001"), abs(close - suggested_stop))
        reward = abs(targets[1] - close)
        rr = max(Decimal("2.5"), (reward / risk).quantize(Decimal("0.1")))

        breakdown = CryptoScreenerScoreBreakdown(
            trend_score=trend_score.quantize(Decimal("0.1")),
            momentum_score=mom_score.quantize(Decimal("0.1")),
            derivatives_score=deriv_score.quantize(Decimal("0.1")),
            alpha_score=alpha_score.quantize(Decimal("0.1")),
            volume_score=vol_score.quantize(Decimal("0.1")),
            structure_score=struct_score.quantize(Decimal("0.1")),
            macro_multiplier=macro_multiplier.quantize(Decimal("0.01")),
            final_composite_score=composite_score,
        )

        adv_7d, adv_14d, vol_contraction_ratio = volume_contraction_metrics(closed_1h, 7, 14)
        vp_window = closed_4h[-60:] if len(closed_4h) >= 60 else closed_4h
        vp = volume_profile(vp_window, bins=50)
        oi_change_7d = derivatives.open_interest_change_7d if derivatives else None

        if is_long:
            pullback_entry = (close - (atr14 * Decimal("0.4"))).quantize(close)
            breakout_trigger = (close + (atr14 * Decimal("0.2"))).quantize(close)
        else:
            pullback_entry = (close + (atr14 * Decimal("0.4"))).quantize(close)
            breakout_trigger = (close - (atr14 * Decimal("0.2"))).quantize(close)

        is_breakout_mode = (
            setup == SetupLabel.BREAKOUT_LONG
            if isinstance(setup, SetupLabel)
            else str(setup) == "BREAKOUT_LONG"
        )
        if is_long:
            va_pass = bool(
                (close >= vp.poc_price * Decimal("0.97"))
                if is_breakout_mode
                else (
                    (close >= vp.val_price * Decimal("0.96"))
                    or (close >= vp.poc_price * Decimal("0.975"))
                )
            )
            va_badge_desc = (
                f"Accepted above POC: ${float(vp.poc_price):.4f}"
                if close >= vp.poc_price
                else f"Value Base: VAL ${float(vp.val_price):.4f}"
            )
        else:
            va_pass = bool(close <= vp.poc_price * Decimal("1.025") or close <= vp.vah_price * Decimal("1.04"))
            va_badge_desc = (
                f"Accepted below POC: ${float(vp.poc_price):.4f}"
                if close <= vp.poc_price
                else f"Value Ceiling: VAH ${float(vp.vah_price):.4f}"
            )

        breakout_checklist = (
            ("Trend Baseline", trend_score >= Decimal("50"), f"Trend Score {trend_score:.0f}/100"),
            ("Momentum Impulse", mom_score >= Decimal("50"), f"Momentum Score {mom_score:.0f}/100"),
            ("Value Acceptance", va_pass, va_badge_desc),
            ("Volume Expansion", vol_score >= Decimal("50"), f"Volume Score {vol_score:.0f}/100"),
            ("Derivatives Flow", deriv_score >= Decimal("40"), f"Derivatives Score {deriv_score:.0f}/100"),
        )

        pass_count = sum(1 for item in breakout_checklist if item[1])
        cvd_metrics = cumulative_volume_delta_metrics(closed_1h, lookback_window=24)
        max_size_usd = min(Decimal("250000"), (adv_7d * close * Decimal("0.010")).quantize(Decimal("1000")))

        win_rate, ev_r = calculate_opportunity_win_rate_and_ev(
            setup_label=setup,
            score=composite_score,
            checklist_passed=pass_count,
            checklist_total=len(breakout_checklist),
            risk_reward_ratio=rr,
            taker_ratio=taker_ratio,
            macro_multiplier=macro_multiplier,
        )
        _, conviction = compute_p_win_and_conviction(
            direction=direction,
            archetype=setup,
            quantitative_score=composite_score,
            checklist_passed=pass_count,
        )
        checklist_verification = ChecklistVerification(
            value_acceptance=va_pass,
            volatility_structure=bool(trend_score >= Decimal("50")),
            momentum_alignment=bool(mom_score >= Decimal("50")),
            volume_microstructure=bool(vol_score >= Decimal("50")),
            derivatives_positioning=bool(deriv_score >= Decimal("40")),
        )
        grade = classify_opportunity_grade(
            ev_r=ev_r,
            conviction=conviction,
            asymmetric_rr=rr,
            checklist_passed=pass_count,
        )

        recent_candles = tuple(
            {
                "time": int(c.closed_at.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in closed_1h[-200:]
        )

        return CryptoOpportunity(
            instrument=inst,
            direction=direction,
            regime=regime,
            setup_label=setup,
            score=composite_score,
            score_breakdown=breakdown,
            current_price=close,
            atr14=atr14,
            suggested_entry=close,
            suggested_stop=suggested_stop,
            suggested_targets=targets,
            risk_reward_ratio=rr,
            estimated_win_rate_pct=win_rate,
            expected_value_r=ev_r,
            conviction=conviction,
            grade=grade.value if hasattr(grade, "value") else str(grade),
            checklist_verification=checklist_verification,
            execution_mode="BREAKOUT_EXPANSION" if is_long else "BREAKDOWN_EXPANSION",
            btc_beta=beta.quantize(Decimal("0.01")),
            residual_return_24h=residual_24h.quantize(Decimal("0.0001")),
            funding_rate=funding_rate,
            oi_change_1h=oi_change,
            taker_ratio=taker_ratio,
            rationale=tuple(rationale),
            macro_narrative=macro_context.narrative(),
            poc_price=vp.poc_price,
            vah_price=vp.vah_price,
            val_price=vp.val_price,
            adv_7d=adv_7d.quantize(Decimal("1")),
            adv_14d=adv_14d.quantize(Decimal("1")),
            volume_contraction_ratio=vol_contraction_ratio.quantize(Decimal("0.01")),
            oi_change_7d=oi_change_7d,
            pullback_entry=pullback_entry,
            breakout_trigger=breakout_trigger,
            checklist=breakout_checklist,
            cvd_24h=cvd_metrics.cvd_24h_change.quantize(Decimal("1")),
            cvd_z_score=cvd_metrics.cvd_z_score,
            orderflow_absorption=cvd_metrics.is_bullish_absorption if direction is Direction.LONG else cvd_metrics.is_bearish_distribution,
            max_recommended_size_usd=max_size_usd,
            candles=recent_candles,
            is_discovery_tier=is_discovery_tier,
        )
