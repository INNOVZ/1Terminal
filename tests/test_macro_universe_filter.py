from __future__ import annotations

from algotrader.application.macro_service import MacroIntelligenceService


def test_macro_filters_out_non_perpetual_and_illiquid_contracts() -> None:
    svc = MacroIntelligenceService()
    active_perps = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "ILLIQUIDUSDT"}

    tickers = [
        {"symbol": "BTCUSDT", "priceChangePercent": "-2.5", "quoteVolume": "5000000000"},
        {"symbol": "ETHUSDT", "priceChangePercent": "-1.5", "quoteVolume": "2000000000"},
        {"symbol": "SOLUSDT", "priceChangePercent": "3.0", "quoteVolume": "900000000"},
        {"symbol": "ILLIQUIDUSDT", "priceChangePercent": "25.0", "quoteVolume": "100000"},
        {"symbol": "FRHCUSDT", "priceChangePercent": "12.0", "quoteVolume": "50000000"},
        {"symbol": "GPROUSDT", "priceChangePercent": "8.0", "quoteVolume": "10000000"},
    ]
    premiums = [
        {"symbol": "FRHCUSDT", "lastFundingRate": "0.00577"},
        {"symbol": "GPROUSDT", "lastFundingRate": "0.00141"},
        {"symbol": "ILLIQUIDUSDT", "lastFundingRate": "0.00400"},
        {"symbol": "SOLUSDT", "lastFundingRate": "0.00025"},
        {"symbol": "BTCUSDT", "lastFundingRate": "0.00010"},
        {"symbol": "ETHUSDT", "lastFundingRate": "-0.00030"},
    ]

    liquid = svc._filter_liquid_crypto_symbols(tickers, active_perps)
    assert liquid == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    breadth = svc._parse_market_breadth(tickers, allowed_symbols=liquid)
    assert breadth.total_pairs_analyzed == 3
    assert breadth.pct_above_20_sma == 33.3
    assert breadth.median_24h_change_pct == -1.5

    heat = svc._parse_derivatives_heat(premiums, allowed_symbols=liquid)
    heated_syms = [s["symbol"] for s in heat.top_heated_symbols]
    discount_syms = [s["symbol"] for s in heat.top_discount_symbols]
    assert "FRHCUSDT" not in heated_syms
    assert "GPROUSDT" not in heated_syms
    assert "ILLIQUIDUSDT" not in heated_syms
    assert heated_syms[0] == "SOLUSDT"
    assert discount_syms[0] == "ETHUSDT"

