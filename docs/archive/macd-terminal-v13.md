# MACD Confluence Terminal V13

## What changed from V12

V12 relied on MACD signal-line crosses with a single EMA21 confirmation as its only
entry filter. Its defaults disabled ADX, volume, higher-timeframe trend, and choppiness
checks — which meant entries fired in ranging, choppy markets where MACD oscillates
randomly. The market check showed 17 wins from 68 entries (25% win rate, -$440 net
on $10k) on TSTUSDT 4H. V13 corrects these structural weaknesses.

- **Main strategy:** [trend_momentum_indicator.pine](../trend_momentum_indicator.pine).
- **Preserved V12:** [backup](../backups/trend_momentum_indicator.v12.pine).
- **V13 test suite:** [tests/pine_v13.test.cjs](../tests/pine_v13.test.cjs).

## Four-layer confluence architecture

V13 replaces "MACD cross → enter" with a layered system where each layer provides
genuinely independent information:

```
Layer 1: REGIME    → "Should I be looking at this market?"
Layer 2: STRUCTURE → "Is price at a meaningful level?"
Layer 3: TRIGGER   → "Do multiple independent confirmations agree?"
Layer 4: RISK      → "Can I manage this trade with acceptable risk?"
```

### Layer 1: Regime filters (all ON by default)

| Filter | Purpose | Default |
|--------|---------|---------|
| EMA200 macro trend | Longs only above EMA200, shorts below | ON |
| Higher-timeframe trend | Confirmed HTF EMA alignment | ON (Auto) |
| ADX ≥ 20 | Filters ranging/trendless markets | ON |
| Choppiness < 55 | Blocks choppy, oscillating markets | ON |
| EMA21/50 alignment | Trend slope and order | Required in Confluence |

### Layer 2: Structure-aware entry

- **Pullback-to-value:** Price must have recently touched the EMA21 zone (within 8 bars)
  and reclaimed it. Prevents entries far from institutional interest zones.
- **Momentum candle:** Entry candle must close beyond the previous bar's high (longs)
  or low (shorts), confirming directional commitment.

### Layer 3: Multi-confluence trigger (Confluence mode)

MACD cross is the **setup**, not the entry. Entry requires **2 of 3** independent
confirmations within the confirmation window:

1. **RSI direction:** RSI above 50 (longs) or below 50 (shorts)
2. **Volume participation:** Volume ≥ 1.2× the 20-bar average
3. **Momentum candle pattern:** Close beyond previous bar's extreme

Additionally, MACD histogram must expand for **2 consecutive bars** (not just 1).

### Layer 4: Enhanced risk management

| Feature | V12 | V13 | Why |
|---------|-----|-----|-----|
| Default stop | ATR (1.5×) | Signal structure | Stops at meaningful swing levels, not arbitrary distances |
| Partial profit | Not available | 50% at +1R, stop to breakeven | Locks in gains, reduces drawdown |
| Stagnation exit | Not available | Exit at market if < 0.5R in 5 bars | Cuts dead trades consuming capital |
| Trailing stop | OFF | ON (1.5R activation, 2 ATR trail) | Lets winners run |
| Structure target | Not used | Cap at nearest opposing level | Realistic profit targets |
| Trend loss exit | 2 closes beyond EMA50 | 1 close beyond EMA50 | Faster exit on trend loss |
| Cooldown | 2 bars | 3 bars | More rest between trades |

## Entry models

V13 supports three entry models for flexibility:

| Model | Description | When to use |
|-------|-------------|-------------|
| **Confluence** (default) | Full 4-layer system with 2-of-3 confirmations | Recommended for live trading |
| **MACD + EMA** | V12-compatible: MACD cross + EMA21 confirmation | Comparison / debugging |
| **Trend continuation** | MACD cross + EMA alignment + EMA50 slope | Trend-following purists |

All regime filters (ADX, choppiness, EMA200, HTF) apply regardless of entry model.

## Dashboard

The V13 dashboard adds regime state visibility:

| Row | Description |
|-----|-------------|
| EMA200 MACRO | Bullish/Bearish based on close vs EMA200 |
| ADX | Current value with ✓/✗ indicator |
| CHOPPINESS | Current value with TRENDING/CHOPPY label |
| RSI(14) | Current RSI with directional coloring |
| CONFLUENCE | Count of passing confirmations vs minimum required |

## Install in TradingView

1. Create a **new** strategy in Pine Editor, paste the full V13 source, save as
   **MACD Confluence Terminal V13**, and add it to the chart. Remove V12.
2. Keep the companion MACD oscillator pane — its MACD values match V13.
3. Use standard time-based candles with bar-close calculation.
4. Review the default regime filters; disable any that don't suit your asset class.

## Verification

The V13 test suite covers 14 tests (13 pass, 1 skipped for missing oscillator):

```sh
npm install --prefix /tmp/swing-v11-validation --cache /tmp/swing-v11-npm-cache --ignore-scripts --no-audit --no-fund pinets@0.9.33
NODE_PATH=/tmp/swing-v11-validation/node_modules node --test tests/pine_v13.test.cjs
```

Tests verify:
- MACD contract: both directions, cross freshness, one-cross-one-entry
- Confirmation window behavior at minimum (1 bar) and default (2 bars)
- Regime filters: choppiness blocking, ADX filtering
- Direction and volume filtering
- Exit logic: MACD exits, bracket protection, cooldown
- Sizing: risk budget and notional exposure caps
- Invalid configuration rejection
- Confluence mode produces fewer entries than MACD+EMA mode
- Real TSTUSDT 4H data: contract satisfaction and prefix consistency

PineTS is an independent runtime. These tests validate logic correctness, not
TradingView compilation. Server-side compile and visual verification are still required.

## Limits

No backtest result predicts future profitability. V13 should produce **fewer,
higher-conviction trades** — but real validation requires:
- Server-side Pine v6 compile in TradingView
- Visual chart inspection across multiple symbols and timeframes
- Walk-forward, out-of-sample testing with the Python backend
- Live paper trading with realistic costs, funding, and slippage
