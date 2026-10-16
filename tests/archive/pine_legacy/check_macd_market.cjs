// Exploratory behavior check only; no tuning or out-of-sample selection.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { ROOT, run, values, entries, marketBars } = require('../tests/pine_v12_helpers.cjs');
(async () => {
 const bars = marketBars();
 const options = { timeframe: '240', tick: 0.00001 };
 const versions = {
  V11: fs.readFileSync(path.join(ROOT, 'backups/trend_momentum_indicator.v11.pine'), 'utf8'),
  V12: fs.readFileSync(path.join(ROOT, 'trend_momentum_indicator.pine'), 'utf8'),
 };
 const runs = {};
 for (const [version, code] of Object.entries(versions)) {
  const ctx = await run(bars, {}, { ...options, code });
  const signals = entries(ctx);
  const trades = ctx.strategy.closedtrades;
  const decisions = Object.entries(ctx.var).find(([k]) => k.endsWith('_decision'))?.[1]?.data || [];
  runs[version] = {
   entries: signals.length, longEntries: signals.filter(x => x.side === 1).length,
   shortEntries: signals.filter(x => x.side === -1).length, closedTrades: trades.length,
   decisionBars: decisions.slice(0, bars.length).reduce((a, x) => (a[x] = (a[x] || 0) + 1, a), {}),
   entryTimes: signals.map(e => ({ time: new Date(bars[e.bar].openTime).toISOString(), side: e.side })),
  };
  if (version === 'V12') {
   runs[version].exploratoryPerformance = {
    netProfit: ctx.strategy.netprofit, maxDrawdown: ctx.strategy.max_drawdown,
    wins: trades.filter(t => t.profit > 0).length,
    warning: 'PineTS emulator only; assumed contract metadata; no funding/spread; not held out; not comparable to V11 position sizing.',
   };
  }
 }
 const raw = fs.readFileSync(path.join(ROOT, 'tests/fixtures/pine/TSTUSDT-4h.json'));
 const report = {
  purpose: 'MACD freshness, event coverage and blockage diagnostics; not evidence of superior returns',
  source: 'https://fapi.binance.com/fapi/v1/klines?symbol=TSTUSDT&interval=4h&limit=1500',
  market: 'Binance USD-M TSTUSDT perpetual', timeframe: '4H', bars: bars.length,
  firstOpenUtc: new Date(bars[0].openTime).toISOString(), lastCloseUtc: new Date(bars.at(-1).closeTime).toISOString(),
  dataSha256: crypto.createHash('sha256').update(raw).digest('hex'),
  assumptions: ['Final downloaded candle excluded as developing', 'PineTS 0.9.33',
   'Tick=0.00001, quantity step=1 and point value=1 are unverified harness assumptions',
   'Daily HTF for V11 aggregated from the same cached 4H bars',
   'Defaults differ by design: V11 HTF required/pullback; V12 chart MACD/EMA',
   'No parameter optimization; no reserved holdout; this is an exploratory development sample'],
  blocked: 'Additional symbol/interval and contract metadata downloads rejected by automatic approval review due to usage limit',
  runs,
 };
 const target = path.join(ROOT, 'docs/macd-v12-market-check.json');
 fs.writeFileSync(target, JSON.stringify(report, null, 2) + '\n');
 console.log(JSON.stringify({bars:report.bars,first:report.firstOpenUtc,last:report.lastCloseUtc,
  V11:{...runs.V11,entryTimes:undefined}, V12:{...runs.V12,entryTimes:undefined}}, null, 2));
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
