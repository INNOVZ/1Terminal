# Swing Structure V11

Historical V11 notes. The active file is now V12; see [MACD Terminal V12](macd-terminal-v12.md).
V11 source and its regression tests are preserved against the versioned backup.

Implemented in [trend_momentum_indicator.pine](../trend_momentum_indicator.pine).
The original 943-line V10 is preserved in
[backups/trend_momentum_indicator.v10.pine](../backups/trend_momentum_indicator.v10.pine).

V11 replaces independent signal paths with a single decision system. It remains a
TradingView **strategy** with indicator-style visuals and simulated position tracking.
There is no orders-OFF mode: that mode prevented V10's position-dependent exits from
working. No exchange integration or webhook automation is configured by this change.

## Load it in TradingView

1. Open a standard candle chart and the Pine Editor.
2. Create a new strategy named **Swing Structure V11** and paste the entire main file.
   Keep your saved V10 script for comparison.
3. Save and add it to the chart. Remove V10 from that chart to avoid mixed markers.
4. Start with default inputs. The pullback setup is ON; experimental direct breakouts,
   ADX, MACD, volume confirmation and trailing are OFF. Opposing-swing room checks are ON.
5. Keep the declared calculation behavior: no recalculation on every tick or order fill,
   and no fills on the signal-bar close. Review simulated fees, slippage and order size
   in Properties for your market. The inherited starting assumptions are 0.05% per order,
   two ticks of slippage and 10% equity **position size**, not 10% risk or stop-based sizing.

With Auto HTF, a 1H chart uses confirmed 4H context; 4H uses daily; daily uses weekly.
Weekly uses monthly. At sufficiently large chart intervals Auto may not be higher;
select a valid larger manual interval. Equal/lower manual timeframes are rejected
when the HTF filter is enabled. Disabling HTF also removes it from context eligibility.

## What a signal means

| Marker | Meaning | Timing |
|---|---|---|
| BUY / PULLBACK or BREAKOUT | Accepted long-entry decision | Confirmed signal-bar close |
| SHORT / PULLBACK or BREAKOUT | Accepted short-entry decision | Confirmed signal-bar close |
| EXIT LONG / STOP, TARGET, TREND LOST or DATE END | Completed simulated long exit | Observed at the closing calculation of the exit bar |
| EXIT SHORT / reason | Completed simulated short exit | Same observation rule |

BUY/SHORT labels represent decisions, not guaranteed fills. The broker emulator
normally fills market entries at the next bar's open with configured slippage.
TradingView's native strategy markers show fills; our labels show named decisions
and completed exits. Disable **Show named entry and completed-exit labels** if you
prefer only native strategy markers. Label history is limited to 500 objects; the
Strategy Tester and Data Window event series remain available for older events.

The dashboard describes the last calculated bar, not the historical candle under
the crosshair. **BULLISH** is context. **WAITING FOR SETUP**, **WAITING FOR RECLAIM**,
**TOO EXTENDED**, **ENTRY PENDING** and **MANAGING TRADE** describe the decision state.
No numerical quality score or STRONG BUY/SELL rating is used.

## Entry rules

### Pullback and reclaim (default)

- Long context requires EMA21 above EMA50, a rising EMA50 over five bars and, if
  enabled, the last completed HTF close above its EMA50. Short context is symmetric.
- A pullback candle must intersect the EMA21 +/- 0.5 ATR band measured from the
  **previous completed bar**. Its zone and extreme are saved. The candle must close
  on the correct side of EMA50. A touch alone never emits an entry.
- Over the next five bars, a long reclaim must close above EMA21 and the previous
  candle's high; a short reclaim must close below EMA21 and the previous low.
- The setup expires after five bars or invalidates on lost context. Expired/consumed
  touches require a full departure from the band before another touch can arm.
- The setup extreme includes subsequent completed candles through the reclaim bar.
  The stop is placed beyond it with a 0.25 ATR buffer and rounded outward to the tick.

### Direct range breakout (optional)

- Uses the previous 20 completed bars' high/low; the current bar is excluded.
- Requires a directional close beyond that boundary plus 0.1 ATR and a previous
  close inside the boundary. Trend context must agree.
- Stops reference the prior five-bar low/high plus the same ATR buffer.
- An accepted entry consumes that direction's breakout latch. A return to EMA21
  while flat and eligible is required before the direction can arm another breakout.
  This deliberately prevents repeated new-high/new-low signals in a continuing move.
- When both setups trigger together, pullback takes priority. Breakout is not a
  squeeze-release exception and cannot bypass any shared filter.

### Shared gateway

Every entry requires: warmed-up data, permitted direction and dates, flat position,
no pending entry, no completed exit that bar, cooldown, valid context/trigger,
extension <= 1.5 ATR, enabled ADX/volume/MACD checks, correct candle color, valid
structure risk and sufficient room to known opposing swings.

Signal risk must be between 0.5 and 3 ATR. An overly wide structure stop is **rejected**,
not moved inside the structure. The default target is twice the signal risk.
The optional volume gate uses the prior 20 completed bars' average and rejects
missing/zero volume. MACD confirmation checks direction; it does not demand a cross.

The room filter keeps at most 12 confirmed, unbroken swing highs and 12 swing lows.
It compares the nearest opposing level against the planned target plus a buffer.
Levels become available only after five right-hand confirmation bars. Missing
levels do not block entries, but do not mean the market has no resistance/support.
This is a deliberately limited swing-level model, not full market-depth analysis.

## Position and exit lifecycle

The initial absolute stop and target are submitted **in the same branch as the entry**.
They are not delayed until a later calculation detects the position. Pending plans
are shown on the chart. A rejected/unfilled market entry clears its plan on the next
closing calculation and is recorded as ENTRY NOT FILLED.

An open position blocks all new entries; there is no pyramiding or automatic reversal.
Two closes beyond EMA50 against the position request a market exit for the next
available tick. The protective bracket remains active while that close is pending.
Completed exits, including an entry and stop within the same candle, reset state and
start the cooldown. A new pullback must depart and touch again after that reset.

An optional ATR trail tightens the existing stop while retaining the target. It uses
frozen entry ATR and activates relative to actual fill-to-initial-stop risk. Stop
updates happen at closing calculations, apply going forward, and never loosen the
previous stop. This is not an intrabar tick trail. No breakeven feature is included.

**Price assumptions:** the initial bracket is frozen around the signal close and
setup structure. A next-open gap or slippage changes the actual entry risk and reward
ratio; the displayed 2R plan is not a guaranteed realized 2R trade. Stop fills can also
gap. No funding, spread, partial-fill or liquidation model has been added. When both
stop and target are touched within a candle, TradingView's emulator/intrabar data
determine ordering; inspect sensitive cases with Bar Magnifier.

## Alerts

V11 uses `alert()`, which works in a strategy, rather than ineffective strategy
`alertcondition()` declarations.

- For the same BUY/SHORT decisions and completed-exit observations as the named
  labels, create an alert using **alert() function calls only**. Leave **Enable
  bar-close decision alerts** ON. Messages include the event, symbol, chart interval,
  reference price, stop/target where applicable and bar-close timestamp.
- To receive broker-emulator fills when they happen, create an **order fills only**
  alert instead. An example Message field is:

  ```text
  {{strategy.order.alert_message}} | fill={{strategy.order.price}} | qty={{strategy.order.contracts}} | position={{strategy.position_size}} | {{ticker}} {{interval}}
  ```

- Selecting both alert types produces different decision and fill notifications;
  do not treat both as duplicate instructions to enter a position.
- Historical plots are not historical alert delivery. Recreate existing alerts after
  changing the script or its inputs because TradingView alerts retain a saved copy.
- Do not reuse V10's old alert configuration: event names and semantics changed.

Official references:
[strategy order lifecycle](https://www.tradingview.com/pine-script-docs/concepts/strategies/),
[strategy alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/),
[confirmed HTF requests](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/).

## Verification performed

On 8 September 2026, the actual Pine source was parsed and executed in
**PineTS 0.9.33**, using a deterministic in-memory OHLCV provider and no exchange data.
All **16 tests** in [tests/pine_v11.test.cjs](../tests/pine_v11.test.cjs) passed:
long/short lifecycle, next-bar fill, same-entry-bar stop, expired setup, direction,
volume, extension/risk, date restrictions, opposing swing room, trend-loss exit,
cooldown, monotonic trailing with target retained, breakout gating, invalid inputs,
default aggregated HTF path and prefix consistency with future bars appended.

Reproduce without adding dependencies to the Python application:

```sh
npm install --prefix /tmp/swing-v11-validation --cache /tmp/swing-v11-npm-cache --ignore-scripts --no-audit --no-fund pinets@0.9.33
NODE_PATH=/tmp/swing-v11-validation/node_modules node --test tests/pine_v11.test.cjs
```

PineTS is an independent runtime, **not TradingView's compiler or broker emulator**.
Its runtime `input.timeframe` overrides are ignored in this version; the invalid-HTF
test changes only that input's declared default in memory to exercise the real guard.
Other scenario tests run the source as written with normal input overrides. See the
[PineTS strategy documentation](https://github.com/LuxAlgo/PineTS/blob/main/docs/strategy.md)
for the runtime's documented differences.

**Still required in TradingView:** a successful Pine v6 compile, chart/replay visual
inspection, live alert verification and cost-aware market-data backtests. No browser
session was available to perform the server-side compile here. No improved win rate
or profitability is claimed by these synthetic correctness tests.

The next evaluation should compare V10 and V11 with identical data, sizing and cost
assumptions, then compare pullback-only against breakout-enabled separately. Keep a
chronological final test period untouched while tuning development data.
