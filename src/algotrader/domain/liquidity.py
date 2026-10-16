from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


def percentile_rank(values: Sequence[Decimal]) -> list[Decimal]:
    """Compute standard empirical percentile rank (0.0 to 1.0) for each element in values.

    pct_rank(x_i) = (count(x < x_i) + 0.5 * count(x == x_i)) / len(values)
    For empty list returns [].
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [Decimal("1.0")]

    n_dec = Decimal(n)
    half = Decimal("0.5")

    ranks: list[Decimal] = []
    for val in values:
        less_count = sum(1 for v in values if v < val)
        equal_count = sum(1 for v in values if v == val)
        rank = (Decimal(less_count) + half * Decimal(equal_count)) / n_dec
        ranks.append(rank)
    return ranks


@dataclass(frozen=True, slots=True)
class SymbolLiquidityMetrics:
    symbol: str
    quote_vol_24h: Decimal
    median_quote_vol_30d: Decimal | None = None
    open_interest_usd: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RankedLiquiditySymbol:
    symbol: str
    ili: Decimal
    quote_vol_24h: Decimal
    median_quote_vol_30d: Decimal
    open_interest_usd: Decimal


def compute_ili_ranks(
    metrics: Sequence[SymbolLiquidityMetrics],
) -> list[RankedLiquiditySymbol]:
    """Compute Institutional Liquidity Index (ILI) across candidate symbols:

    ILI = 0.50 * pct_rank(median_quote_vol_30d) + 0.35 * pct_rank(open_interest_usd) + 0.15 * pct_rank(quote_vol_24h)
    """
    if not metrics:
        return []

    med_30d_list = [
        m.median_quote_vol_30d
        if m.median_quote_vol_30d is not None and m.median_quote_vol_30d > Decimal("0")
        else m.quote_vol_24h
        for m in metrics
    ]
    oi_usd_list = [
        m.open_interest_usd
        if m.open_interest_usd is not None and m.open_interest_usd > Decimal("0")
        else m.quote_vol_24h
        for m in metrics
    ]
    qvol_24h_list = [m.quote_vol_24h for m in metrics]

    rank_med = percentile_rank(med_30d_list)
    rank_oi = percentile_rank(oi_usd_list)
    rank_24h = percentile_rank(qvol_24h_list)

    w_med = Decimal("0.50")
    w_oi = Decimal("0.35")
    w_24h = Decimal("0.15")

    results: list[RankedLiquiditySymbol] = []
    for i, m in enumerate(metrics):
        ili = (w_med * rank_med[i] + w_oi * rank_oi[i] + w_24h * rank_24h[i]).quantize(
            Decimal("0.0001")
        )
        results.append(
            RankedLiquiditySymbol(
                symbol=m.symbol,
                ili=ili,
                quote_vol_24h=m.quote_vol_24h,
                median_quote_vol_30d=med_30d_list[i],
                open_interest_usd=oi_usd_list[i],
            )
        )

    results.sort(key=lambda x: x.ili, reverse=True)
    return results
