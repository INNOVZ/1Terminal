# MACD Trading Terminal V12

## What changed after the V11 feedback

V11 replaced your MACD concept with a narrow pullback strategy. MACD was optional,
the default higher-timeframe gate delayed eligibility, the chart showed overlapping
native/custom trade labels, and its close-only strategy could leave the terminal
uninitialized until the current candle closed. V12 corrects those design choices.

- **Main strategy:** [trend_momentum_indicator.pine](../trend_momentum_indicator.pine).
- **Visible MACD pane:** [trend_momentum_macd.pine](../trend_momentum_macd.pine).
- **Preserved V11:** [backup](../backups/trend_momentum_indicator.v11.pine).
- **Market check:** [reproducible results](macd-v12-market-check.json).

The strategy and companion use the same configurable EMA-based MACD calculation.
The companion is an oscillator, not another strategy. Its cross dots indicate
setups; the main strategy decides whether a setup becomes a trade.

## Explicit MACD strategy

The default model is **MACD + EMA**, with MACD lengths **12 / 26 / 9**.
All entry decisions require a completed candle.

**Long entry:** MACD crosses above its signal line, then on that candle or within
the next three bars, price closes above the confirmation EMA (default 21), MACD
remains above its signal, its histogram expands upward, and the candle closes green.
The shared risk, extension, position, cooldown and enabled optional gates must pass.

**Short entry:** mirror the rules: MACD crosses below its signal, close below EMA21,
histogram expanding downward and a red candle.

A cross starts one bounded confirmation window. An opposing cross replaces it;
an expired cross cannot trigger a later trade. An accepted entry consumes the cross.
MACD remaining bullish/bearish cannot generate repeated entries by itself. If a new
cross occurs while a trade is open, its window may still qualify after the position
has exited and cooldown has elapsed; stale windows remain disallowed.

MACD signal-line crossing means the **histogram crosses zero**. It does not mean
the MACD line itself crosses zero. Requiring the MACD line to be above/below zero
is a separate optional continuation filter. See
[TradingView's MACD strategy definition](https://www.tradingview.com/support/solutions/43000644943-macd-strategy/).

The alternative **Trend continuation** model additionally requires EMA21/EMA50
alignment and EMA50 slope in the entry direction. Neither model is labeled optimal
or validated profitable. Higher-timeframe confirmation is optional and OFF by
default; when enabled it uses only completed higher-timeframe bars and rejects
equal/lower timeframe selections. Daily charts no longer need weekly EMA50 history
unless you explicitly enable that filter.

## Exits and risk

- Initial stop defaults to 1.5 ATR; the target defaults to 2 times planned signal risk.
  An optional signal-structure stop uses the extreme observed during the cross window.
- Stop and target are submitted together with the entry decision. They do not wait
  for a later bar to detect that the market entry filled.
- Opposing MACD plus price beyond EMA21 requests an exit independently of whether
  a new opposite entry is permitted. EMA50 structure-loss exit remains separately
  configurable. The protective bracket stays active while an exit is pending.
- One simulated position at a time; no pyramiding or automatic same-bar reversal.
  Cooldown defaults to two bars after entry or completed exit.
- Planned risk defaults to **0.5% equity**, subject to a **25% equity notional cap**.
  Quantity uses stop distance, symbol point value and minimum contract increment.
  The exposure cap can make actual planned risk much smaller than 0.5%.
- Equity is converted to symbol currency using `strategy.convert_to_symbol()`.
  For instruments whose sizing/contract conventions differ, verify order quantities
  in TradingView before relying on this calculation.

The bracket is calculated from signal-close prices, while market orders ordinarily
fill at the next open. Gaps, slippage, costs and contract rounding change realized
risk and reward. A planned 2R target is not a promised 2R outcome. The optional trail
only tightens the stop at closing calculations and retains the target.

## Terminal and chart layout

The main chart shows confirmation/context EMAs and pending/active risk levels.
The terminal shows model, chart trend, HTF setting, MACD direction, histogram state,
cross-window age, current action/blocker, simulated position, entry/stop/target,
last event, accepted-entry/cross counts, last blocker and last calculation time.

**BULLISH** is context; **ENTRY PENDING** means an accepted decision waiting for a
simulated fill; **MANAGING TRADE** means an open position. **LAST BLOCK** is historical
diagnostic information, not necessarily the current blocker. Counters describe
loaded history and are not confidence scores.

The dashboard initializes on `barstate.islastconfirmedhistory` as well as `islast`.
This avoids waiting for an open daily/4H candle to close before first displaying it.
It reports completed-bar information; it is not a live order book. The AS OF row
makes that timestamp visible. See
[TradingView bar-state semantics](https://www.tradingview.com/pine-script-docs/concepts/bar-states/).

Custom trade labels default OFF, leaving TradingView's native strategy execution
markers as the single trade-label layer. Input values are hidden from the chart
legend. Diagnostic setup triangles default OFF. The MACD companion has its own
proper oscillator scale, so MACD values do not distort the price scale.

## Install both scripts

1. Create a **new** strategy in Pine Editor, paste the full main file, save as
   **MACD Trading Terminal V12**, and add it to the chart. Remove V11 from that chart.
   A new script instance avoids inheriting old saved inputs/display settings.
2. Create a separate indicator, paste `trend_momentum_macd.pine`, save and add it.
   This creates the MACD pane below the price chart. Match the three MACD lengths
   in both scripts if you change them; defaults already match.
3. Use ordinary time-based candles. Keep strategy recalculation at bar close and
   market fills on the next available tick. Verify Properties costs for your venue.
4. Leave **Show decision dashboard** ON. If you enable custom named trade labels,
   expect overlap unless you separately hide TradingView's native execution markers.

For main-strategy decision notifications, use **alert() function calls only**.
For emulator fills, use **order fills only**, with this Message field:

```text
{{strategy.order.alert_message}} | fill={{strategy.order.price}} | qty={{strategy.order.contracts}} | {{ticker}} {{interval}}
```

Decision messages distinguish a signal-close decision from a completed simulated
exit. Do not send both decision and fill notifications to an execution system as
though they were two entries. Recreate old alerts after changing the script.
No broker connection or webhook execution was configured by this work.

## What the actual market check found

Using 1,499 completed Binance TSTUSDT perpetual 4H candles, from January 1, 2026
08:00 UTC through September 8, 2026 04:00 UTC:

| Default system | Entries | Long | Short |
|---|---:|---:|---:|
| V11 pullback + required HTF | 1 | 0 | 1 |
| V12 MACD + EMA | 68 | 30 | 38 |

This establishes a substantial signal-availability difference on this sample.
It does **not** establish better returns. The V12 exploratory simulation recorded
17 winning trades out of 68 and approximately **-$440.36 net P&L on $10,000 initial
capital**. Maximum reported drawdown was approximately $760.33. More signals did
not create a positive edge in this development sample.

These numbers come from PineTS 0.9.33, not TradingView. The harness assumed a
0.00001 tick, quantity step 1 and point value 1 because contract-metadata retrieval
was blocked. It used the script's 0.05% per-order fee and two-tick slippage, without
funding or spread. V11/V12 sizing differs, so the report does not compare their
returns directly. The final downloaded candle was excluded as developing; V11's
daily context was aggregated from the same cached 4H bars. This was not held-out
validation or a parameter search. No settings were optimized to make this sample win.

Automatic approval review rejected the additional symbol/timeframe and metadata
downloads because the session hit its usage limit. The cross-market comparison
remains incomplete. The downloaded public fixture is preserved under
`tests/fixtures/pine/TSTUSDT-4h.json`; source URL, date range and SHA-256 are in the
JSON report.

## Verification and limits

The actual main Pine source and oscillator execute in PineTS. Eleven tests cover:
MACD-cross freshness, bounded confirmation, one entry per cross, both directions,
exits, same-entry-bar protection, risk/exposure sizing, invalid configuration,
oscillator parity, default display configuration, and real-data prefix consistency.

```sh
npm install --prefix /tmp/swing-v11-validation --cache /tmp/swing-v11-npm-cache --ignore-scripts --no-audit --no-fund pinets@0.9.33
NODE_PATH=/tmp/swing-v11-validation/node_modules node --test tests/pine_v12.test.cjs
NODE_PATH=/tmp/swing-v11-validation/node_modules node scripts/check_macd_market.cjs
```

The V11 regression suite now explicitly tests its archived V11 source.
PineTS is an independent runtime; passing these tests is not a successful TradingView
compiler run. No TradingView browser session was available. Server-side compilation,
visual confirmation of the dashboard and oscillator, live-alert checks and held-out
cost-aware validation are still required. V12 implements a transparent MACD terminal;
it must not be presented as a proven profitable system.
