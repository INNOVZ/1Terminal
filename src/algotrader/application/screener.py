from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import structlog

from algotrader.application.alpha import (
    AlphaDecision,
    EarlyMomentumAlpha,
    EmpiricalTrendAlpha,
    LayeredMomentumAlpha,
    RegimeBreakoutAlpha,
    SlowTimeSeriesMomentumAlpha,
    TrendQualityAlpha,
)
from algotrader.application.alt_momentum import (
    AltMomentumRow,
    alt_momentum_row,
    rank_alt_momentum,
)
from algotrader.application.research import beta_correlation
from algotrader.config import (
    CROSS_SECTIONAL_RANKING_PROFILES,
    DAILY_REGIME_PROFILES,
    EMPIRICAL_ALPHA_PROFILES,
    FOUR_HOUR_DECISION_PROFILES,
    SOFT_STRUCTURE_PROFILES,
    Settings,
    StrategyProfile,
)
from algotrader.domain.errors import InsufficientHistory
from algotrader.domain.indicators import (
    ContinuationTrigger,
    IndicatorSnapshot,
    PriceLevels,
    continuation_trigger,
    snapshot,
    support_resistance,
)
from algotrader.domain.models import (
    Candidate,
    Candle,
    DerivativesSnapshot,
    Direction,
    Instrument,
    MarketKind,
    MomentumState,
    Quote,
)
from algotrader.domain.ports import MarketDataPort
from algotrader.domain.structure import MarketStructure, classify

log = structlog.get_logger(__name__)

ZERO = Decimal("0")


class MultiFactorScreener:
    """Ranks liquid instruments; it does not make profitability claims."""

    def __init__(self, settings: Settings, concurrency: int = 8) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(concurrency)
        self._regime_breakout = RegimeBreakoutAlpha(settings)
        self._trend_quality = TrendQualityAlpha(settings)
        self._slow_trend = SlowTimeSeriesMomentumAlpha(settings)
        self._empirical_trend = EmpiricalTrendAlpha(settings)
        self._layered_momentum = LayeredMomentumAlpha(settings)
        self._early_momentum = EarlyMomentumAlpha(settings)

    @property
    def minimum_score(self) -> Decimal:
        """Eligibility threshold, so callers filtering a raw pass agree with `screen`."""
        return self._settings.min_candidate_score

    @property
    def strategy_profile(self) -> str:
        return self._settings.strategy_profile.value

    async def screen(
        self, market_data: MarketDataPort, *, include_rejected: bool = False
    ) -> list[Candidate]:
        """Rank eligible candidates.

        ``include_rejected`` also returns candidates that scored zero, carrying the reasons
        they were blocked. Those reasons are computed on every pass and otherwise discarded,
        which makes questions like "why are there no shorts?" unanswerable from the outside.
        Rejected candidates are for diagnostics only and are never tradable: the trading
        cycle calls this without the flag.
        """
        all_instruments = await market_data.instruments()
        instruments = [
            instrument
            for instrument in all_instruments
            if instrument.trading
            and instrument.quote_asset == "USDT"
            and instrument.base_asset not in self._settings.excluded_base_assets
            and not (
                self._settings.exclude_multiplier_contracts
                and instrument.base_asset.startswith(("1000", "10000", "1000000"))
            )
        ]
        quotes = await market_data.quotes(instruments)
        quote_by_symbol = {quote.instrument_id.symbol: quote for quote in quotes}
        liquid = [
            instrument
            for instrument in instruments
            if (quote := quote_by_symbol.get(instrument.id.symbol))
            and quote.quote_volume_24h >= self._settings.min_quote_volume_24h
            and quote.spread_bps <= self._settings.max_spread_bps
        ]
        liquid.sort(
            key=lambda item: quote_by_symbol[item.id.symbol].quote_volume_24h,
            reverse=True,
        )
        selected = liquid[: self._settings.max_symbols_to_analyze]
        benchmark = next((item for item in instruments if item.base_asset == "BTC"), None)
        benchmark_candles: Sequence[Candle] | None = None
        if benchmark is not None:
            try:
                benchmark_candles = await market_data.candles(benchmark, "1h", 260)
            except Exception as exc:
                log.warning("btc_benchmark_fetch_failed", error=str(exc))
        batches = await asyncio.gather(
            *(
                self._evaluate(
                    market_data,
                    instrument,
                    quote_by_symbol[instrument.id.symbol],
                    benchmark_candles=benchmark_candles,
                )
                for instrument in selected
            ),
            return_exceptions=True,
        )
        candidates: list[Candidate] = []
        for instrument, batch in zip(selected, batches, strict=True):
            if isinstance(batch, asyncio.CancelledError):
                raise batch
            if isinstance(batch, InsufficientHistory):
                # A newly listed token, not a fault. Debug level so real screening
                # failures stay visible instead of being buried in routine noise.
                log.debug(
                    "instrument_too_new_to_screen",
                    instrument=str(instrument.id),
                    detail=str(batch),
                )
                continue
            if isinstance(batch, Exception):
                log.warning(
                    "instrument_screen_failed",
                    instrument=str(instrument.id),
                    error_type=type(batch).__name__,
                    error=str(batch),
                )
                continue
            if isinstance(batch, BaseException):
                raise batch
            candidates.extend(batch)
        candidates = self._apply_cross_sectional_ranking(candidates)
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        if include_rejected:
            return candidates
        return [
            candidate
            for candidate in candidates
            if candidate.score >= self._settings.min_candidate_score
        ]

    async def alt_momentum_leaderboard(
        self, market_data: MarketDataPort, *, limit: int = 15
    ) -> dict[str, object]:
        """Rank liquid alts independently of the configured entry strategy.

        This is observation-only. It intentionally does not return ``Candidate`` objects, so a
        high relative-strength score cannot leak into the order-execution path.
        """

        if limit <= 0 or limit > 100:
            raise ValueError("alt momentum leaderboard limit must be between 1 and 100")
        all_instruments = await market_data.instruments()
        instruments = [
            instrument
            for instrument in all_instruments
            if instrument.trading
            and instrument.quote_asset == "USDT"
            and instrument.base_asset != "BTC"
            and instrument.base_asset not in self._settings.excluded_base_assets
            and not (
                self._settings.exclude_multiplier_contracts
                and instrument.base_asset.startswith(("1000", "10000", "1000000"))
            )
        ]
        benchmark = next(
            (
                instrument
                for instrument in all_instruments
                if instrument.trading
                and instrument.quote_asset == "USDT"
                and instrument.base_asset == "BTC"
            ),
            None,
        )
        if benchmark is None:
            raise InsufficientHistory("BTC benchmark is unavailable for alt momentum")
        quotes = await market_data.quotes(instruments)
        quote_by_symbol = {quote.instrument_id.symbol: quote for quote in quotes}
        liquid = [
            instrument
            for instrument in instruments
            if (quote := quote_by_symbol.get(instrument.id.symbol))
            and quote.quote_volume_24h >= self._settings.min_quote_volume_24h
            and quote.spread_bps <= self._settings.max_spread_bps
        ]
        liquid.sort(
            key=lambda item: quote_by_symbol[item.id.symbol].quote_volume_24h,
            reverse=True,
        )
        selected = liquid[: self._settings.max_symbols_to_analyze]
        benchmark_candles = await market_data.candles(benchmark, "1h", 260)

        async def build_row(instrument: Instrument) -> AltMomentumRow:
            async with self._semaphore:
                candles = await market_data.candles(instrument, "1h", 260)
            return alt_momentum_row(
                instrument,
                quote_by_symbol[instrument.id.symbol],
                candles,
                benchmark_candles,
            )

        batches = await asyncio.gather(
            *(build_row(instrument) for instrument in selected), return_exceptions=True
        )
        rows: list[AltMomentumRow] = []
        skipped: dict[str, str] = {}
        for instrument, result in zip(selected, batches, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                skipped[str(instrument.id)] = str(result)
                continue
            if isinstance(result, BaseException):
                raise result
            rows.append(result)
        ranked = rank_alt_momentum(rows)
        long_rows = sorted(ranked, key=lambda row: row.long_score, reverse=True)[:limit]
        short_rows = sorted(ranked, key=lambda row: row.short_score, reverse=True)[:limit]
        observed = max((row.observed_at for row in ranked), default=None)
        return {
            "observed_at": observed,
            "venue": market_data.venue.value,
            "strategy_profile_unchanged": self._settings.strategy_profile.value,
            "execution_authorized": False,
            "universe": {
                "liquid_alts_considered": len(selected),
                "ranked": len(ranked),
                "skipped": len(skipped),
            },
            "building_up_count": sum(
                row.phase in {"emerging_up", "confirmed_up"} for row in ranked
            ),
            "building_down_count": sum(
                row.phase in {"emerging_down", "confirmed_down"} for row in ranked
            ),
            "long_leaders": [row.as_dict() for row in long_rows],
            "short_leaders": [row.as_dict() for row in short_rows],
            "method": (
                "cross-sectional BTC-residual 6h/24h/7d momentum + 6h acceleration + "
                "volume participation; indicators are context only"
            ),
        }

    async def screen_instrument(
        self, market_data: MarketDataPort, instrument: Instrument
    ) -> list[Candidate]:
        """Re-evaluate one instrument from fresh quote and closed-candle data."""
        if (
            not instrument.trading
            or instrument.quote_asset != "USDT"
            or instrument.base_asset in self._settings.excluded_base_assets
        ):
            return []
        quotes = await market_data.quotes((instrument,))
        quote = next(
            (item for item in quotes if item.instrument_id == instrument.id),
            None,
        )
        if (
            quote is None
            or quote.quote_volume_24h < self._settings.min_quote_volume_24h
            or quote.spread_bps > self._settings.max_spread_bps
        ):
            return []
        benchmark_candles: Sequence[Candle] | None = None
        try:
            benchmark = next(
                (item for item in await market_data.instruments() if item.base_asset == "BTC"),
                None,
            )
            if benchmark is not None:
                benchmark_candles = await market_data.candles(benchmark, "1h", 260)
        except Exception as exc:
            log.warning("btc_benchmark_fetch_failed", error=str(exc))
        candidates = await self._evaluate(
            market_data, instrument, quote, benchmark_candles=benchmark_candles
        )
        return [
            candidate
            for candidate in candidates
            if candidate.score >= self._settings.min_candidate_score
        ]

    async def _evaluate(
        self,
        market_data: MarketDataPort,
        instrument: Instrument,
        quote: Quote,
        *,
        benchmark_candles: Sequence[Candle] | None = None,
    ) -> Sequence[Candidate]:
        async with self._semaphore:
            tasks = [
                market_data.candles(instrument, "1h", 260),
                market_data.candles(
                    instrument, "4h", min(1000, self._settings.min_4h_history_bars + 5)
                ),
            ]
            if self._settings.strategy_profile in DAILY_REGIME_PROFILES:
                tasks.append(market_data.candles(instrument, "1d", 260))
            histories = await asyncio.gather(*tasks)
            one_hour, four_hour = histories[:2]
            daily = histories[2] if len(histories) > 2 else None
            derivatives: DerivativesSnapshot | None = None
            provider = getattr(market_data, "derivatives_snapshot", None)
            if (
                provider is not None
                and instrument.market_kind is MarketKind.FUTURES
                and self._settings.collect_derivatives_features
            ):
                try:
                    derivatives = await provider(instrument)
                except Exception as exc:
                    log.warning(
                        "derivatives_features_fetch_failed",
                        instrument=str(instrument.id),
                        error=str(exc),
                    )
        return self.evaluate_history(
            instrument,
            quote,
            one_hour,
            four_hour,
            daily=daily,
            derivatives=derivatives,
            benchmark_one_hour=benchmark_candles,
        )

    def evaluate_history(
        self,
        instrument: Instrument,
        quote: Quote,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        *,
        daily: Sequence[Candle] | None = None,
        derivatives: DerivativesSnapshot | None = None,
        benchmark_one_hour: Sequence[Candle] | None = None,
    ) -> Sequence[Candidate]:
        # Runtime and backtest replay share this exact decision path.
        one_hour_candles = list(one_hour)
        four_hour_candles = list(four_hour)
        available = len([candle for candle in four_hour_candles if candle.closed])
        if available < self._settings.min_4h_history_bars:
            raise InsufficientHistory(
                f"{available} closed 4h candles available, "
                f"{self._settings.min_4h_history_bars} required"
            )
        one = snapshot(one_hour_candles, momentum_lookback=self._settings.momentum_lookback_bars)
        four = snapshot(four_hour_candles)
        daily_candles = list(daily or ())
        levels = support_resistance(
            one_hour_candles, lookback=self._settings.support_resistance_lookback_bars
        )
        structure = classify(
            one_hour_candles,
            left=self._settings.swing_pivot_left,
            right=self._settings.swing_pivot_right,
        )
        directions = [Direction.LONG]
        profile = self._settings.strategy_profile
        if instrument.supports_short and self._settings.strategy_shorts_enabled:
            directions.append(Direction.SHORT)
        if profile in DAILY_REGIME_PROFILES:
            if len([candle for candle in daily_candles if candle.closed]) < 210:
                raise InsufficientHistory(
                    "210 closed daily candles required for slow trend profiles"
                )
            daily_snapshot = snapshot(daily_candles)
        if profile in EMPIRICAL_ALPHA_PROFILES:
            alpha = (
                self._layered_momentum
                if profile is StrategyProfile.LAYERED_MOMENTUM
                else self._empirical_trend
            )
            candidates = [
                self._score_regime_breakout(
                    instrument,
                    quote,
                    one_hour_candles,
                    four_hour_candles,
                    one,
                    four,
                    levels,
                    structure,
                    direction,
                    decision=alpha.evaluate(
                        one_hour_candles,
                        four_hour_candles,
                        daily_candles,
                        one,
                        four,
                        daily_snapshot,
                        quote,
                        direction,
                        continuation_trigger(
                            one_hour_candles,
                            direction,
                            lookback=self._settings.pullback_lookback_bars,
                            touch_buffer_atr=self._settings.pullback_touch_buffer_atr,
                            require_previous_bar_break=(
                                self._settings.pullback_require_previous_bar_break
                            ),
                        ),
                    ),
                )
                for direction in directions
            ]
        elif profile is StrategyProfile.EARLY_MOMENTUM:
            candidates = [
                self._score_regime_breakout(
                    instrument,
                    quote,
                    one_hour_candles,
                    four_hour_candles,
                    one,
                    four,
                    levels,
                    structure,
                    direction,
                    decision=self._early_momentum.evaluate(
                        one_hour_candles,
                        four_hour_candles,
                        one,
                        four,
                        quote,
                        direction,
                    ),
                )
                for direction in directions
            ]
        elif profile is StrategyProfile.SLOW_TREND:
            candidates = [
                self._score_regime_breakout(
                    instrument,
                    quote,
                    one_hour_candles,
                    four_hour_candles,
                    one,
                    four,
                    levels,
                    structure,
                    direction,
                    decision=self._slow_trend.evaluate(
                        four_hour_candles,
                        daily_candles,
                        four,
                        daily_snapshot,
                        quote,
                        direction,
                    ),
                )
                for direction in directions
            ]
        elif profile in {
            StrategyProfile.REGIME_BREAKOUT,
            StrategyProfile.TREND_QUALITY,
        }:
            candidates = [
                self._score_regime_breakout(
                    instrument,
                    quote,
                    one_hour_candles,
                    four_hour_candles,
                    one,
                    four,
                    levels,
                    structure,
                    direction,
                )
                for direction in directions
            ]
        else:
            candidates = [
                self._score(
                    instrument,
                    quote,
                    one,
                    four,
                    levels,
                    structure,
                    direction,
                    continuation_trigger(
                        one_hour_candles,
                        direction,
                        lookback=self._settings.pullback_lookback_bars,
                        touch_buffer_atr=self._settings.pullback_touch_buffer_atr,
                        require_previous_bar_break=(
                            self._settings.pullback_require_previous_bar_break
                        ),
                    ),
                )
                for direction in directions
            ]
        beta, correlation = (
            beta_correlation(one_hour_candles, benchmark_one_hour)
            if benchmark_one_hour is not None
            else (None, None)
        )
        alt_observation: AltMomentumRow | None = None
        if instrument.base_asset != "BTC" and benchmark_one_hour is not None:
            try:
                alt_observation = alt_momentum_row(
                    instrument,
                    quote,
                    one_hour_candles,
                    benchmark_one_hour,
                )
            except ValueError:
                # Missing aligned history is already represented by null research features.
                # It must not alter the configured strategy's existing eligibility decision.
                alt_observation = None
        return [
            replace(
                candidate,
                funding_rate=derivatives.funding_rate if derivatives else None,
                basis_rate=derivatives.basis_rate if derivatives else None,
                open_interest=derivatives.open_interest if derivatives else None,
                open_interest_change_1h=(
                    derivatives.open_interest_change_1h if derivatives else None
                ),
                taker_buy_sell_ratio=(derivatives.taker_buy_sell_ratio if derivatives else None),
                bid_depth_notional=(derivatives.bid_depth_notional if derivatives else None),
                ask_depth_notional=(derivatives.ask_depth_notional if derivatives else None),
                depth_imbalance=derivatives.depth_imbalance if derivatives else None,
                btc_beta=beta,
                btc_correlation=correlation,
                alt_momentum_phase=(alt_observation.phase if alt_observation else None),
                residual_return_6h=(
                    alt_observation.residual_return_6h if alt_observation else None
                ),
                residual_return_24h=(
                    alt_observation.residual_return_24h if alt_observation else None
                ),
                residual_return_7d=(
                    alt_observation.residual_return_7d if alt_observation else None
                ),
                momentum_acceleration_6h=(
                    alt_observation.acceleration_6h if alt_observation else None
                ),
                volume_participation_6h=(
                    alt_observation.participation_ratio if alt_observation else None
                ),
            )
            for candidate in candidates
        ]

    def _score_regime_breakout(
        self,
        instrument: Instrument,
        quote: Quote,
        one_hour: Sequence[Candle],
        four_hour: Sequence[Candle],
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        rolling_levels: PriceLevels,
        structure: MarketStructure,
        direction: Direction,
        *,
        decision: AlphaDecision | None = None,
    ) -> Candidate:
        """Translate the pure alpha decision into the execution-neutral candidate model."""
        alpha = (
            self._trend_quality
            if self._settings.strategy_profile is StrategyProfile.TREND_QUALITY
            else self._regime_breakout
        )
        if decision is None:
            decision = alpha.evaluate(one_hour, four_hour, one, four, quote, direction)
        long = direction is Direction.LONG
        entry_price = quote.ask if long else quote.bid
        failures = list(decision.failures)
        profile = self._settings.strategy_profile
        if profile in FOUR_HOUR_DECISION_PROFILES and one.closed_at != four.closed_at:
            failures.append("waiting for the next fully closed 4h decision bar")

        if (
            profile not in SOFT_STRUCTURE_PROFILES
            and self._settings.require_structure_alignment
            and structure.aligned_with(Direction.SHORT if long else Direction.LONG)
        ):
            failures.append(
                f"market structure is a confirmed {structure.trend.value} against this entry"
            )
        if instrument.market_kind is MarketKind.SPOT and not long:
            failures.append("spot short prohibited")

        # A breakout is expected to be above old resistance (or below old support), so that
        # broken level cannot cap the target. Only the protective side is carried forward;
        # the strategy then uses its configured R target and structural trailing policy.
        protective = structure.protective_level(direction, entry_price)
        support: Decimal | None = None
        resistance: Decimal | None = None
        if long:
            support = protective if protective is not None else rolling_levels.support
            if (
                profile is StrategyProfile.EARLY_MOMENTUM
                and rolling_levels.resistance > entry_price
            ):
                resistance = rolling_levels.resistance
        else:
            resistance = protective if protective is not None else rolling_levels.resistance
            if profile is StrategyProfile.EARLY_MOMENTUM and rolling_levels.support < entry_price:
                support = rolling_levels.support

        if profile is StrategyProfile.EARLY_MOMENTUM:
            room = self._settings.required_opposing_room_atr * one.atr14
            if long and resistance is not None and entry_price >= resistance - room:
                failures.append(
                    "known resistance leaves less than the minimum structural reward/risk room"
                )
            if not long and support is not None and entry_price <= support + room:
                failures.append(
                    "known support leaves less than the minimum structural reward/risk room"
                )

        eligible = decision.eligible and not failures
        momentum_state = self._momentum_state(one, direction)
        signal_snapshot = four if profile in FOUR_HOUR_DECISION_PROFILES else one
        score = decision.intrinsic_score
        soft_evidence: tuple[str, ...] = ()
        if (
            profile in {StrategyProfile.LAYERED_MOMENTUM, StrategyProfile.EARLY_MOMENTUM}
            and decision.eligible
        ):
            score, soft_evidence = self._soft_market_quality_score(
                score, one, decision, structure, direction
            )
        reasons = (
            (*decision.evidence, *soft_evidence)
            if eligible
            else tuple(f"entry blocked: {message}" for message in failures)
        )
        return Candidate(
            instrument=instrument,
            direction=direction,
            score=score if eligible else Decimal("0"),
            entry_price=entry_price,
            signal_bar_close=signal_snapshot.close,
            atr=signal_snapshot.atr14,
            spread_bps=quote.spread_bps,
            quote_volume_24h=quote.quote_volume_24h,
            momentum_state=momentum_state,
            reasons=reasons,
            rsi_1h=one.rsi14,
            rsi_4h=four.rsi14,
            support_price=support,
            resistance_price=resistance,
            evaluated_at=quote.observed_at,
            signal_bar_closed_at=signal_snapshot.closed_at,
            momentum_reference_closed_at=one.momentum_reference_closed_at,
            momentum_reference_close=one.momentum_reference_close,
            macd_hist=one.macd_hist,
            momentum_reference_macd_hist=one.momentum_reference_macd_hist,
            momentum_reference_rsi_1h=one.momentum_reference_rsi14,
            adx_1h=one.adx14,
            momentum_reference_adx_1h=one.momentum_reference_adx14,
            ema9_1h=one.ema9,
            ema20_1h=one.ema20,
            ema50_1h=one.ema50,
            ema200_1h=one.ema200,
            ema20_4h=four.ema20,
            ema50_4h=four.ema50,
            ema200_4h=four.ema200,
            relative_volume=one.relative_volume,
            structure_trend=structure.trend.value,
            structure_reasons=structure.reasons,
            levels_from_structure=protective is not None,
            momentum_lookback_bars=self._settings.momentum_lookback_bars,
            strategy_profile=self._settings.strategy_profile.value,
            market_regime=decision.market_regime,
            breakout_level=decision.breakout_level,
            return_7d=decision.return_7d,
            return_30d=decision.return_30d,
            return_90d=decision.return_90d,
            return_180d=decision.return_180d,
            return_6h=decision.return_6h,
            return_24h=decision.return_24h,
            volatility_percentile=decision.volatility_percentile,
            indicator_confirmations=decision.indicator_confirmations,
            indicator_conflicts=decision.indicator_conflicts,
            macd_cross_1h=decision.macd_cross_1h,
            rsi_divergence_1h=decision.rsi_divergence_1h,
            setup_signal=decision.setup_signal,
            setup_strength=decision.setup_strength,
            aligned_momentum_horizons=decision.aligned_momentum_horizons,
            directional_momentum_score=decision.directional_momentum_score,
            technical_quality_score=decision.technical_quality_score,
            breakout_relative_volume=decision.breakout_relative_volume,
            breakout_age_bars=decision.breakout_age_bars,
        )

    def _soft_market_quality_score(
        self,
        base_score: Decimal,
        one: IndicatorSnapshot,
        decision: AlphaDecision,
        structure: MarketStructure,
        direction: Direction,
    ) -> tuple[Decimal, tuple[str, ...]]:
        """Rank market quality without turning correlated observations into vetoes."""

        relative_volume_score = min(Decimal("2"), max(Decimal("0"), one.relative_volume))
        percentile = decision.volatility_percentile
        volatility_score = (
            max(Decimal("0"), Decimal("2") - abs(percentile - Decimal("0.5")) * Decimal("4"))
            if percentile is not None
            else Decimal("0")
        )
        if structure.aligned_with(direction):
            structure_score = Decimal("2")
            structure_evidence = "market structure aligns (+2 ranking points)"
        elif structure.aligned_with(
            Direction.SHORT if direction is Direction.LONG else Direction.LONG
        ):
            structure_score = Decimal("-2")
            structure_evidence = "opposing market structure retained as a -2 ranking warning"
        else:
            structure_score = Decimal("0")
            structure_evidence = "range/uncertain market structure is ranking-neutral"
        adjusted = min(
            Decimal("100"),
            max(
                self._settings.min_candidate_score,
                base_score + relative_volume_score + volatility_score + structure_score,
            ),
        )
        return adjusted, (
            "relative volume is bounded ranking evidence, not an entry veto",
            "volatility percentile is bounded ranking evidence, not an entry veto",
            structure_evidence,
        )

    def _apply_cross_sectional_ranking(self, candidates: list[Candidate]) -> list[Candidate]:
        """Reward relative opportunity strength without converting rank into eligibility.

        Hard strategy validity remains local to each instrument, so pre-trade revalidation
        can fail closed without downloading the whole universe again. Ranking only decides
        which already-valid opportunity receives scarce portfolio capacity first.
        """
        if self._settings.strategy_profile not in CROSS_SECTIONAL_RANKING_PROFILES:
            return candidates
        rankable = [
            candidate
            for candidate in candidates
            if candidate.score >= self._settings.min_candidate_score
            and candidate.return_30d is not None
        ]

        def directional_strength(candidate: Candidate) -> Decimal:
            if (
                self._settings.strategy_profile
                in {StrategyProfile.LAYERED_MOMENTUM, StrategyProfile.EARLY_MOMENTUM}
                and candidate.directional_momentum_score is not None
            ):
                return candidate.directional_momentum_score
            value = candidate.return_30d
            if value is None:  # Defensive; rankable excludes this case above.
                return Decimal("-Infinity")
            return value if candidate.direction is Direction.LONG else -value

        rankable.sort(key=directional_strength)
        ranks: dict[tuple[str, Direction], Decimal] = {}
        denominator = max(1, len(rankable) - 1)
        for index, candidate in enumerate(rankable):
            ranks[(str(candidate.instrument.id), candidate.direction)] = (
                Decimal(index) / Decimal(denominator) if len(rankable) > 1 else Decimal("1")
            )
        ranked: list[Candidate] = []
        for candidate in candidates:
            rank = ranks.get((str(candidate.instrument.id), candidate.direction))
            if rank is None:
                ranked.append(candidate)
                continue
            ranked.append(
                replace(
                    candidate,
                    score=min(Decimal("100"), candidate.score + rank * Decimal("5")),
                    cross_sectional_rank=rank,
                    reasons=(
                        *candidate.reasons,
                        (
                            "cross-sectional normalized directional-momentum rank"
                            if self._settings.strategy_profile
                            in {StrategyProfile.LAYERED_MOMENTUM, StrategyProfile.EARLY_MOMENTUM}
                            else "cross-sectional 30-day directional-strength rank"
                        ),
                    ),
                )
            )
        return ranked

    def _resolve_levels(
        self,
        structure: MarketStructure,
        rolling: PriceLevels,
        entry_price: Decimal,
        direction: Direction,
    ) -> tuple[PriceLevels, bool]:
        """Use the *nearest* structure behind price, and the *significant* level ahead of it.

        The two sides answer different questions and must not share a source:

        - **Behind price (stop):** the nearest confirmed swing is correct. That is where the
          trade thesis is invalidated, and it gives the tightest honest stop.
        - **Ahead of price (target and room):** the nearest swing is the wrong level. Minor
          swing highs are broken constantly inside a trend, and capping the target at the
          first one leaves a fraction of an ATR of headroom -- which can never clear the
          minimum reward/risk, so every candidate is rejected. Measured against live Binance
          data, sourcing both sides from swings blocked 100% of candidates in both
          directions. The meaningful obstacle is the range extreme.

        So the protective side comes from structure and the opposing side from the rolling
        window, with the wider of the two taken when structure reaches further.
        """
        protective = structure.protective_level(direction, entry_price)
        if direction is Direction.LONG:
            support = protective if protective is not None else rolling.support
            resistance = max(rolling.resistance, entry_price)
        else:
            resistance = protective if protective is not None else rolling.resistance
            support = min(rolling.support, entry_price)
        return PriceLevels(support=support, resistance=resistance), protective is not None

    def _score(
        self,
        instrument: Instrument,
        quote: Quote,
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        rolling_levels: PriceLevels,
        structure: MarketStructure,
        direction: Direction,
        trigger: ContinuationTrigger,
    ) -> Candidate:
        """Score a candidate using the V10-aligned momentum + confirmation pipeline.

        Phase 1: Momentum building detection (ATR-normalized displacement)
        Phase 2: MACD cross event (fresh cross within configured window)
        Phase 3: Confirmation layer (ribbon, macro, ADX, RSI, choppiness, candle, volume)
        """
        long = direction is Direction.LONG
        entry_price = quote.ask if long else quote.bid

        # ── Phase 1: Momentum Building Detection ────────────────────────────
        momentum_building = (
            one.momentum_normalized > ZERO if long else one.momentum_normalized < ZERO
        )

        # ── Phase 2: MACD Cross Event (core trigger from V10) ───────────────
        # A fresh bullish MACD cross: line crosses above signal. Check current bar
        # and look back within the configured window.
        macd_cross_age = self._macd_cross_age(one, direction)
        has_fresh_macd_cross = (
            macd_cross_age is not None
            and macd_cross_age <= self._settings.macd_cross_window_bars
        )
        # Also accept when MACD is already aligned in the trade direction even if
        # the cross happened earlier — matches V10's EMA21 crossover path.
        macd_aligned = (
            one.macd_line > one.macd_signal_line
            if long
            else one.macd_line < one.macd_signal_line
        )

        # ── Phase 3: Confirmation Layer ─────────────────────────────────────
        # EMA ribbon: 9 > 20 > 50 for longs (V10's bullRibbon)
        ema_ribbon = (
            one.ema9 > one.ema20 and one.ema20 > one.ema50
            if long
            else one.ema9 < one.ema20 and one.ema20 < one.ema50
        )
        # 4H trend alignment
        four_hour_trend = four.ema20 > four.ema50 if long else four.ema20 < four.ema50
        # Macro alignment (EMA200)
        one_hour_macro = one.close > one.ema200 if long else one.close < one.ema200
        four_hour_macro = four.close > four.ema200 if long else four.close < four.ema200
        # ADX trend strength (≥ 20)
        adx_strength = one.adx14 >= Decimal("20")
        # DI direction
        di_direction = one.plus_di > one.minus_di if long else one.minus_di > one.plus_di
        four_hour_adx_strength = four.adx14 >= self._settings.min_4h_adx
        four_hour_di_direction = (
            four.plus_di > four.minus_di if long else four.minus_di > four.plus_di
        )
        # RSI regime (> 45 for longs, < 55 for shorts — matching V10)
        rsi_regime = one.rsi14 > Decimal("45") if long else one.rsi14 < Decimal("55")
        # RSI exhaustion guards
        rsi_not_extended = (
            one.rsi14 <= self._settings.rsi_long_max
            if long
            else one.rsi14 >= self._settings.rsi_short_min
        )
        four_hour_rsi_not_exhausted = (
            four.rsi14 <= self._settings.four_hour_rsi_long_max
            if long
            else four.rsi14 >= self._settings.four_hour_rsi_short_min
        )
        # Choppiness filter — blocks entries in ranging markets (V10: < 58)
        not_choppy = one.choppiness <= self._settings.max_choppiness
        # Candle direction confirmation (V10: close > open for longs)
        candle_confirmed = one.is_bullish_candle if long else not one.is_bullish_candle
        # Volume ≥ 0.8× average (V10 default)
        volume_ok = one.relative_volume >= self._settings.min_relative_volume
        # Sufficient volatility
        sufficient_volatility = one.atr14 > 0 and one.atr14 / one.close >= Decimal("0.005")
        volatility_not_shocked = (
            one.atr14 > 0
            and one.close > 0
            and one.atr14 / one.close <= self._settings.max_atr_fraction
        )
        # Anti-chase: entry within 2.5 ATR of EMA20
        extension = (
            (entry_price - one.ema20 if long else one.ema20 - entry_price) / one.atr14
            if one.atr14 > 0
            else Decimal("Infinity")
        )
        entry_not_extended = extension <= self._settings.max_entry_extension_from_ema20_atr

        momentum_state = self._momentum_state(one, direction)

        # ── Assemble required checks ────────────────────────────────────────
        required_checks = (
            (momentum_building, "momentum is not building in the trade direction"),
            (
                has_fresh_macd_cross or macd_aligned,
                "no fresh MACD cross or MACD alignment in the trade direction",
            ),
            (ema_ribbon, "1h EMA ribbon (9/20/50) is not aligned"),
            (four_hour_trend, "4h trend is not aligned"),
            (one_hour_macro, "1h price is not aligned with EMA200"),
            (four_hour_macro, "4h price is not aligned with EMA200"),
            (adx_strength, "trend strength is below ADX 20"),
            (di_direction, "directional movement is not aligned"),
            (
                four_hour_adx_strength,
                f"4h trend strength is below ADX {self._settings.min_4h_adx}",
            ),
            (
                not self._settings.require_4h_di_alignment or four_hour_di_direction,
                "4h directional movement is not aligned",
            ),
            (rsi_regime, "RSI regime is not aligned (need > 45 long / < 55 short)"),
            (
                rsi_not_extended,
                f"1h RSI is extended beyond the exhaustion threshold ({one.rsi14:.1f})",
            ),
            (
                four_hour_rsi_not_exhausted,
                f"4h RSI is exhausted beyond the threshold ({four.rsi14:.1f})",
            ),
            (not_choppy, f"market is choppy (choppiness {one.choppiness:.1f} > {self._settings.max_choppiness})"),
            (candle_confirmed, "signal bar candle is not in the trade direction"),
            (volume_ok, f"relative volume {one.relative_volume:.2f} is below {self._settings.min_relative_volume}"),
            (sufficient_volatility, "volatility is insufficient"),
            (volatility_not_shocked, "volatility exceeds the configured shock ceiling"),
            (
                entry_not_extended,
                f"entry is too extended from the 1h EMA20 ({extension:.1f} ATR)",
            ),
            (
                not self._settings.require_pullback_confirmation or trigger.passed,
                "pullback continuation is unconfirmed: " + "; ".join(trigger.failures),
            ),
        )
        failed_checks = [message for passed, message in required_checks if not passed]
        levels, levels_from_structure = self._resolve_levels(
            structure, rolling_levels, entry_price, direction
        )

        # Structure alignment: reject entries against a confirmed opposing trend.
        if self._settings.require_structure_alignment and structure.aligned_with(
            Direction.SHORT if long else Direction.LONG
        ):
            failed_checks.append(
                f"market structure is a confirmed {structure.trend.value} against this entry"
            )

        chase_limit = one.atr14 * self._settings.max_entry_chase_atr
        if long and entry_price > one.close + chase_limit:
            failed_checks.append("price is too far above the signal close")
        if not long and entry_price < one.close - chase_limit:
            failed_checks.append("price is too far below the signal close")

        # Room to opposing S/R level.
        level_buffer = one.atr14 * self._settings.required_opposing_room_atr
        if long and entry_price >= levels.resistance - level_buffer:
            failed_checks.append(
                "entry has insufficient room to resistance for minimum reward/risk"
            )
        if not long and entry_price <= levels.support + level_buffer:
            failed_checks.append("entry has insufficient room to support for minimum reward/risk")

        if instrument.market_kind is MarketKind.SPOT and not long:
            failed_checks.append("spot short prohibited")

        eligible = not failed_checks
        macd_evidence = (
            f"fresh MACD cross {macd_cross_age} bars ago"
            if has_fresh_macd_cross and macd_cross_age is not None
            else "MACD aligned in trade direction"
            if macd_aligned
            else "no MACD confirmation"
        )
        reasons = (
            [
                f"momentum building ({one.momentum_normalized:.2f} ATR displacement)",
                macd_evidence,
                "1h EMA ribbon aligned (9 > 20 > 50)"
                if long
                else "1h EMA ribbon aligned (9 < 20 < 50)",
                "4h EMA trend",
                "1h macro alignment",
                "4h macro alignment",
                "1h ADX strength",
                "DI direction",
                *([f"4h ADX strength (≥ {self._settings.min_4h_adx})"] if self._settings.min_4h_adx > 0 else []),
                *(["4h DI direction"] if self._settings.require_4h_di_alignment else []),
                "RSI regime",
                f"not choppy (choppiness {one.choppiness:.1f})",
                "candle confirmed in trade direction",
                f"volume confirmed ({one.relative_volume:.2f}x avg)",
                "sufficient volatility",
                *(["volatility shock guard"] if self._settings.max_atr_fraction < 1 else []),
                f"entry within {extension:.1f} ATR of EMA20",
                *(
                    ["1h EMA20 pullback continuation confirmed"]
                    if self._settings.require_pullback_confirmation
                    else []
                ),
            ]
            if eligible
            else [f"entry blocked: {message}" for message in failed_checks]
        )
        score = (
            self._ranking_score(one, four, levels, quote, entry_price, direction)
            if eligible
            else Decimal("0")
        )
        return Candidate(
            instrument=instrument,
            direction=direction,
            score=min(score, Decimal("100")),
            entry_price=entry_price,
            signal_bar_close=one.close,
            atr=one.atr14,
            spread_bps=quote.spread_bps,
            quote_volume_24h=quote.quote_volume_24h,
            momentum_state=momentum_state,
            reasons=tuple(reasons),
            rsi_1h=one.rsi14,
            rsi_4h=four.rsi14,
            support_price=levels.support,
            resistance_price=levels.resistance,
            evaluated_at=quote.observed_at,
            signal_bar_closed_at=one.closed_at,
            momentum_reference_closed_at=one.momentum_reference_closed_at,
            momentum_reference_close=one.momentum_reference_close,
            macd_hist=one.macd_hist,
            momentum_reference_macd_hist=one.momentum_reference_macd_hist,
            momentum_reference_rsi_1h=one.momentum_reference_rsi14,
            adx_1h=one.adx14,
            momentum_reference_adx_1h=one.momentum_reference_adx14,
            ema9_1h=one.ema9,
            ema20_1h=one.ema20,
            ema50_1h=one.ema50,
            ema200_1h=one.ema200,
            choppiness_1h=one.choppiness,
            macd_cross_age_bars=macd_cross_age,
            momentum_normalized_1h=one.momentum_normalized,
            ema20_4h=four.ema20,
            ema50_4h=four.ema50,
            ema200_4h=four.ema200,
            relative_volume=one.relative_volume,
            structure_trend=structure.trend.value,
            structure_reasons=structure.reasons,
            levels_from_structure=levels_from_structure,
            continuation_trigger_passed=trigger.passed,
            continuation_trigger_evidence=trigger.evidence,
            continuation_trigger_failures=trigger.failures,
            momentum_lookback_bars=self._settings.momentum_lookback_bars,
            strategy_profile=StrategyProfile.LEGACY_MOMENTUM.value,
            market_regime="legacy_trend",
        )

    def _ranking_score(
        self,
        one: IndicatorSnapshot,
        four: IndicatorSnapshot,
        levels: PriceLevels,
        quote: Quote,
        entry_price: Decimal,
        direction: Direction,
    ) -> Decimal:
        """Rank only eligible candidates; hard strategy gates are evaluated separately."""

        def bounded(value: Decimal) -> Decimal:
            return min(Decimal("1"), max(Decimal("0"), value))

        trend_strength = bounded(abs(one.ema20 - one.ema50) / one.atr14) * Decimal("4")
        four_atr = max(four.atr14, four.close * Decimal("0.000001"))
        trend_strength += bounded(abs(four.ema20 - four.ema50) / four_atr) * Decimal("4")
        adx_strength = bounded((one.adx14 - Decimal("20")) / Decimal("30")) * Decimal("6")
        macd_base = max(abs(one.momentum_reference_macd_hist), one.close * Decimal("0.000001"))
        # Reward only movement in the candidate direction. An absolute delta made bearish
        # MACD deceleration increase a short's score (and bullish deceleration increase a
        # long's score), even though the momentum consensus correctly treated that input as
        # a conflict. A disagreement may be tolerated by the 3-of-4 gate, but it must never
        # improve ranking priority.
        macd_strength = bounded(
            self._directional_macd_change(one, direction) / macd_base
        ) * Decimal("6")
        room = (
            levels.resistance - entry_price
            if direction is Direction.LONG
            else entry_price - levels.support
        )
        room_strength = bounded(room / (one.atr14 * Decimal("5"))) * Decimal("6")
        spread_strength = bounded(
            Decimal("1") - quote.spread_bps / self._settings.max_spread_bps
        ) * Decimal("2")
        volume_strength = bounded(one.relative_volume / Decimal("2")) * Decimal("2")
        return min(
            Decimal("100"),
            Decimal("70")
            + trend_strength
            + adx_strength
            + macd_strength
            + room_strength
            + spread_strength
            + volume_strength,
        )

    @staticmethod
    def _directional_macd_change(one: IndicatorSnapshot, direction: Direction) -> Decimal:
        change = one.macd_hist - one.momentum_reference_macd_hist
        return change if direction is Direction.LONG else -change

    def _macd_evidence(self, one: IndicatorSnapshot, direction: Direction) -> str:
        prefix = f"{self._settings.momentum_lookback_bars}-bar MACD"
        if self._directional_macd_change(one, direction) > 0:
            return f"{prefix} directional acceleration"
        return f"{prefix} conflict tolerated by momentum consensus"

    @staticmethod
    def _macd_cross_age(one: IndicatorSnapshot, direction: Direction) -> int | None:
        """Return 0 if the MACD crossed on this bar, 1 if previous bar, else None.

        Detects a fresh bullish cross (line crosses above signal) for longs, or a fresh
        bearish cross (line crosses below signal) for shorts. Uses prev_macd_line/signal
        stored in the snapshot to check one bar back.
        """
        if direction is Direction.LONG:
            # Current bar cross: prev was at/below, now above
            if one.prev_macd_line <= one.prev_macd_signal_line and one.macd_line > one.macd_signal_line:
                return 0
            # Already crossed on the previous bar (we can detect up to 1 bar back)
            if one.macd_line > one.macd_signal_line:
                return 1  # Aligned but crossed earlier
        else:
            if one.prev_macd_line >= one.prev_macd_signal_line and one.macd_line < one.macd_signal_line:
                return 0
            if one.macd_line < one.macd_signal_line:
                return 1
        return None

    def _momentum_state(self, one: IndicatorSnapshot, direction: Direction) -> MomentumState:
        """Classify momentum using ATR-normalized displacement + MACD histogram direction.

        GAINING: momentum is building in the trade direction (positive displacement for
        longs, negative for shorts) AND MACD histogram is moving in the right direction.
        LOSING: momentum is fading or reversing.
        MIXED: ambiguous — some indicators agree, some don't.
        """
        long = direction is Direction.LONG
        # Primary: ATR-normalized momentum displacement
        momentum_aligned = (
            one.momentum_normalized > ZERO if long else one.momentum_normalized < ZERO
        )
        # Secondary: MACD histogram direction
        macd_hist_aligned = (
            one.macd_hist > one.momentum_reference_macd_hist
            if long
            else one.macd_hist < one.momentum_reference_macd_hist
        )
        # Tertiary: RSI and ADX still contribute for a fuller picture
        rsi_aligned = (
            one.rsi14 >= one.momentum_reference_rsi14
            if long
            else one.rsi14 <= one.momentum_reference_rsi14
        )
        adx_rising = one.adx14 >= one.momentum_reference_adx14

        positive_count = sum((momentum_aligned, macd_hist_aligned, rsi_aligned, adx_rising))
        if positive_count >= self._settings.momentum_min_rising_checks:
            return MomentumState.GAINING

        # Check for active deterioration
        momentum_against = (
            one.momentum_normalized < ZERO if long else one.momentum_normalized > ZERO
        )
        macd_hist_against = (
            one.macd_hist < one.momentum_reference_macd_hist
            if long
            else one.macd_hist > one.momentum_reference_macd_hist
        )
        rsi_against = (
            one.rsi14 < one.momentum_reference_rsi14
            if long
            else one.rsi14 > one.momentum_reference_rsi14
        )
        adx_falling = one.adx14 < one.momentum_reference_adx14

        negative_count = sum((momentum_against, macd_hist_against, rsi_against, adx_falling))
        if negative_count >= self._settings.momentum_min_rising_checks:
            return MomentumState.LOSING
        return MomentumState.MIXED


def candidate_payload(candidate: Candidate) -> dict[str, object]:
    """Explicit operator-safe serialization with the evidence behind each gate."""
    opposing_level_distance_atr: Decimal | None = None
    if candidate.atr > 0:
        if candidate.direction is Direction.LONG and candidate.resistance_price is not None:
            opposing_level_distance_atr = (
                candidate.resistance_price - candidate.entry_price
            ) / candidate.atr
        if candidate.direction is Direction.SHORT and candidate.support_price is not None:
            opposing_level_distance_atr = (
                candidate.entry_price - candidate.support_price
            ) / candidate.atr

    def text(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    def momentum_pair(current: Decimal | None, reference: Decimal | None) -> dict[str, object]:
        pair: dict[str, object] = {
            "current": text(current),
            "reference": text(reference),
        }
        # Backward-compatible alias for existing operator clients. Do not emit a misleading
        # name when the configurable lookback is not three bars.
        if candidate.momentum_lookback_bars == 3:
            pair["three_bars_ago"] = text(reference)
        return pair

    return {
        "instrument": str(candidate.instrument.id),
        "direction": candidate.direction.value,
        "candidate_eligible": candidate.score > 0,
        "strategy_profile": candidate.strategy_profile,
        "market_regime": candidate.market_regime,
        "momentum": candidate.momentum_state.value,
        "score": str(candidate.score),
        "evaluated_at": candidate.evaluated_at.isoformat(),
        "signal_bar_closed_at": (
            candidate.signal_bar_closed_at.isoformat() if candidate.signal_bar_closed_at else None
        ),
        "momentum_reference_closed_at": (
            candidate.momentum_reference_closed_at.isoformat()
            if candidate.momentum_reference_closed_at
            else None
        ),
        "expires_at": candidate.expires_at.isoformat() if candidate.expires_at else None,
        "entry_price": str(candidate.entry_price),
        "signal_bar_close": str(candidate.signal_bar_close),
        "atr": str(candidate.atr),
        "support_price": text(candidate.support_price),
        "resistance_price": text(candidate.resistance_price),
        "opposing_level_distance_atr": text(opposing_level_distance_atr),
        "spread_bps": str(candidate.spread_bps),
        "momentum_checks": {
            "lookback_bars": candidate.momentum_lookback_bars,
            "price": momentum_pair(candidate.signal_bar_close, candidate.momentum_reference_close),
            "macd_histogram": momentum_pair(
                candidate.macd_hist, candidate.momentum_reference_macd_hist
            ),
            "rsi_1h": momentum_pair(candidate.rsi_1h, candidate.momentum_reference_rsi_1h),
            "adx_1h": momentum_pair(candidate.adx_1h, candidate.momentum_reference_adx_1h),
        },
        "continuation_trigger": {
            "passed": candidate.continuation_trigger_passed,
            "evidence": list(candidate.continuation_trigger_evidence),
            "failures": list(candidate.continuation_trigger_failures),
        },
        "trend": {
            "1h": {
                "ema9": text(candidate.ema9_1h),
                "ema20": text(candidate.ema20_1h),
                "ema50": text(candidate.ema50_1h),
                "ema200": text(candidate.ema200_1h),
            },
            "4h": {
                "ema20": text(candidate.ema20_4h),
                "ema50": text(candidate.ema50_4h),
                "ema200": text(candidate.ema200_4h),
            },
        },
        "relative_volume": text(candidate.relative_volume),
        "v10_signals": {
            "choppiness_1h": text(candidate.choppiness_1h),
            "macd_cross_age_bars": candidate.macd_cross_age_bars,
            "momentum_normalized_1h": text(candidate.momentum_normalized_1h),
        },
        "regime_evidence": {
            "setup_signal": candidate.setup_signal,
            "setup_strength": text(candidate.setup_strength),
            "aligned_momentum_horizons": candidate.aligned_momentum_horizons,
            "directional_momentum_score": text(candidate.directional_momentum_score),
            "breakout_level": text(candidate.breakout_level),
            "breakout_relative_volume": text(candidate.breakout_relative_volume),
            "breakout_age_bars": candidate.breakout_age_bars,
            "return_6h": text(candidate.return_6h),
            "return_24h": text(candidate.return_24h),
            "return_7d": text(candidate.return_7d),
            "return_30d": text(candidate.return_30d),
            "return_90d": text(candidate.return_90d),
            "return_180d": text(candidate.return_180d),
            "volatility_percentile": text(candidate.volatility_percentile),
            "cross_sectional_rank": text(candidate.cross_sectional_rank),
        },
        "technical_quality": {
            "bounded_score": text(candidate.technical_quality_score),
            "confirmations": list(candidate.indicator_confirmations),
            "conflicts": list(candidate.indicator_conflicts),
            "macd_cross_1h": candidate.macd_cross_1h,
            "rsi_divergence_1h": candidate.rsi_divergence_1h,
        },
        "derivatives": {
            "funding_rate": text(candidate.funding_rate),
            "basis_rate": text(candidate.basis_rate),
            "open_interest": text(candidate.open_interest),
            "open_interest_change_1h": text(candidate.open_interest_change_1h),
            "taker_buy_sell_ratio": text(candidate.taker_buy_sell_ratio),
            "bid_depth_notional": text(candidate.bid_depth_notional),
            "ask_depth_notional": text(candidate.ask_depth_notional),
            "depth_imbalance": text(candidate.depth_imbalance),
        },
        "portfolio_exposure": {
            "btc_beta": text(candidate.btc_beta),
            "btc_correlation": text(candidate.btc_correlation),
        },
        "alt_momentum_discovery": {
            "observation_only": True,
            "used_for_candidate_eligibility": False,
            "phase": candidate.alt_momentum_phase,
            "residual_return_6h": text(candidate.residual_return_6h),
            "residual_return_24h": text(candidate.residual_return_24h),
            "residual_return_7d": text(candidate.residual_return_7d),
            "acceleration_6h": text(candidate.momentum_acceleration_6h),
            "volume_participation_6h": text(candidate.volume_participation_6h),
        },
        "reasons": list(candidate.reasons),
    }
