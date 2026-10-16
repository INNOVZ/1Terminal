const { test } = require('node:test');
const assert = require('node:assert/strict');
const { source, oscillator, ENTRY, EXIT, synthetic, run, values, entries, marketBars } = require('./pine_v12_helpers.cjs');

function verifyMacdContract(ctx, bars, window = 3) {
    const line = values(ctx, 'MACD line (Data Window)');
    const signal = values(ctx, 'MACD signal (Data Window)');
    const ema = values(ctx, 'Confirmation EMA');
    const hist = values(ctx, 'MACD histogram (Data Window)');
    const used = new Set();
    for (const { side, bar } of entries(ctx)) {
        let cross = -1;
        for (let i = bar; i >= Math.max(1, bar - window); i--) {
            if (side * (line[i] - signal[i]) > 0 && side * (line[i - 1] - signal[i - 1]) <= 0) {
                cross = i; break;
            }
        }
        assert.ok(cross >= 0, `Entry ${bar} has no fresh directional MACD cross`);
        const key = `${side}:${cross}`;
        assert.ok(!used.has(key), `MACD cross ${key} was consumed twice`);
        used.add(key);
        assert.ok(side * (bars[bar].close - ema[bar]) > 0);
        assert.ok(side * (hist[bar] - hist[bar - 1]) > 0);
        assert.ok(Number.isFinite(values(ctx, 'Protective stop')[bar]));
        assert.ok(Number.isFinite(values(ctx, 'Planned target')[bar]));
    }
}

test('both directions have explicit fresh MACD entries and consume each cross once', async () => {
    for (const side of [1, -1]) {
        const bars = synthetic(side);
        const ctx = await run(bars);
        assert.ok(entries(ctx).some(e => e.side === side));
        verifyMacdContract(ctx, bars);
        assert.ok(ctx.strategy.closedtrades.length > 0);
        for (const trade of ctx.strategy.closedtrades) {
            assert.ok(entries(ctx).some(e => e.bar + 1 === trade.entry_bar_index));
            assert.equal(values(ctx, EXIT)[trade.exit_bar_index], Math.sign(trade.size));
        }
    }
});

test('zero-bar confirmation window permits only the cross candle', async () => {
    const bars = synthetic();
    const ctx = await run(bars, { 'Confirmation window after MACD cross': 0 });
    assert.ok(entries(ctx).length > 0);
    verifyMacdContract(ctx, bars, 0);
});

test('no MACD recross means no repeated entries in an uninterrupted linear trend', async () => {
    const bars = synthetic().map((b, i) => ({ ...b, open: 100 + i * 0.1,
        close: 100.05 + i * 0.1, high: 100.1 + i * 0.1, low: 99.95 + i * 0.1 }));
    const ctx = await run(bars);
    assert.equal(entries(ctx).length, 0);
});

test('direction and missing-volume filters apply to MACD entries', async () => {
    const longOnly = await run(synthetic(-1), { 'Entry direction': 'Long only' });
    assert.ok(entries(longOnly).every(e => e.side === 1));
    const zeroVol = await run(synthetic().map(b => ({ ...b, volume: 0 })), { 'Require volume on every entry': true });
    assert.equal(entries(zeroVol).length, 0);
});

test('opposing MACD with EMA confirmation exits independently of a short entry', async () => {
    const ctx = await run(synthetic(), { 'Target / signal risk': 10,
        'Initial stop ATR': 3, 'Maximum signal risk (ATR)': 5,
        'Exit on EMA50 structure loss': false, 'Entry direction': 'Long only' });
    assert.ok(ctx.strategy.closedtrades.some(t => t.exit_comment === 'MACD EXIT'));
    assert.ok(entries(ctx).every(e => e.side === 1));
});

test('protective bracket works on entry candle and cooldown prevents immediate replacement', async () => {
    const bars = synthetic();
    const baseline = await run(bars);
    const first = entries(baseline)[0];
    const stop = values(baseline, 'Protective stop')[first.bar];
    const i = first.bar + 1;
    bars[i] = { ...bars[i], low: stop - 1 };
    const ctx = await run(bars);
    const closed = ctx.strategy.closedtrades[0];
    assert.equal(closed.entry_bar_index, i);
    assert.equal(closed.exit_bar_index, i);
    assert.equal(closed.exit_comment, 'STOP');
    assert.equal(values(ctx, EXIT)[i], 1);
    assert.equal(values(ctx, ENTRY)[i], 0);
    assert.equal(values(ctx, ENTRY)[i + 1], 0);
});

test('first order is capped by signal risk budget and notional exposure', async () => {
    const bars = synthetic();
    const ctx = await run(bars);
    const first = entries(ctx)[0];
    const trade = ctx.strategy.closedtrades[0];
    const risk = Math.abs(bars[first.bar].close - values(ctx, 'Protective stop')[first.bar]);
    assert.ok(Math.abs(trade.size) * risk <= 50.001);
    assert.ok(Math.abs(trade.size) * bars[first.bar].close <= 2500.001);
});

test('invalid MACD lengths and non-higher HTF settings fail', async () => {
    await assert.rejects(run(synthetic(), { 'MACD fast length': 26, 'MACD slow length': 26 }));
    await assert.rejects(run(synthetic(), { 'Require confirmed higher-timeframe trend': true,
        'Higher timeframe selection': 'Manual', 'Manual higher timeframe': '60' }));
});

test('oscillator and strategy calculate identical MACD series with matched inputs', async () => {
    const bars = synthetic();
    const config = { 'MACD fast length': 8, 'MACD slow length': 21, 'MACD signal length': 5 };
    const strategy = await run(bars, config);
    const pane = await run(bars, config, { code: oscillator });
    for (const [a, b] of [['MACD', 'MACD line (Data Window)'], ['Signal', 'MACD signal (Data Window)'], ['Histogram', 'MACD histogram (Data Window)']]) {
        assert.deepEqual(values(pane, a), values(strategy, b));
    }
});

test('default display avoids duplicate custom trade labels and has history initialization', async () => {
    const ctx = await run(synthetic());
    assert.equal((ctx.plots.__labels__?.data?.[0]?.value || []).length, 0);
    assert.ok(source.includes('(barstate.islastconfirmedhistory or barstate.islast) and showDashboard'));
    assert.ok(ctx.plots.__tables__);
});

test('real TST 4H bars satisfy entry freshness, one-cross consumption and prefix consistency', async () => {
    const bars = marketBars();
    const options = { timeframe: '240', tick: 0.00001 };
    const full = await run(bars, {}, options);
    assert.ok(entries(full).length > 0);
    verifyMacdContract(full, bars);
    const partial = await run(bars.slice(0, 1000), {}, options);
    for (const title of [ENTRY, EXIT]) assert.deepEqual(values(partial, title), values(full, title).slice(0, 1000));
});
