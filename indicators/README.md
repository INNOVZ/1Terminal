# Atlas Institutional TradingView Indicators

This directory contains the Pine Script (v6) institutional indicators and strategies for TradingView.

## 1. Indicator Manifest

| Script | Type | Pine Version | Description |
| :--- | :--- | :--- | :--- |
| [`institutional_momentum_indicator.pine`](file:///Users/jithinjacob/Desktop/trdindicator/indicators/institutional_momentum_indicator.pine) | Overlay Indicator | v6 | **Zaakiy Institutional Momentum & Flow Engine v15.4**: Multi-factor trend ribbon (21/50 EMA), Supertrend (2.4x ATR), MACD acceleration, multi-zone RSI flow, dynamic discount/premium valuation, and live telemetry HUD table. |
| [`trend_momentum_indicator.pine`](file:///Users/jithinjacob/Desktop/trdindicator/indicators/trend_momentum_indicator.pine) | Strategy | v6 | **Zaakiy Institutional Momentum & Flow Strategy v15.4**: The backtestable strategy counterpart implementing automated trade lifecycle, position sizing, stop loss, and multi-target exits. |
| [`pre_breakout_accumulation.pine`](file:///Users/jithinjacob/Desktop/trdindicator/indicators/pre_breakout_accumulation.pine) | Overlay Indicator | v6 | **Zaakiy Pre-Breakout Accumulation & Squeeze v1.0**: 30-day base geometry, fixed-range volume profile (POC/VAH/VAL), Carter volatility squeeze (BB inside KC), and Wyckoff supply exhaustion metrics. |

## 2. Installation on TradingView

1. Open **TradingView** (Desktop or Web) on any crypto perpetual chart (e.g., `BINANCE:BTCUSDT.P`, `BINANCE:ETHUSDT.P`).
2. Open the **Pine Editor** at the bottom of the window.
3. Click **Open** -> **New Indicator** (or clear the editor).
4. Copy and paste the entire contents of the `.pine` file.
5. Click **Save** and then **Add to chart**.

## 3. Recommended Chart Setups

- **Primary Screener & Visual Verification**: 1-Hour (`1h`) and 4-Hour (`4h`) charts.
- **Trend Confirmation**: 4-Hour (`4h`) and Daily (`1d`).
- **Volume Profile**: Enabled for 30-day base lookback (`baseLookbackDays = 30`).
