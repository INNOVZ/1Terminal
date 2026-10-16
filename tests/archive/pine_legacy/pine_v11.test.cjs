// Runs the actual Pine source in PineTS, not a separately rewritten signal model.
// PineTS is a secondary implementation: passing here is not a TradingView compile.
// NODE_PATH=/tmp/swing-v11-validation/node_modules node --test tests/pine_v11.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { PineTS, Indicator } = require('pinets');
const source = fs.readFileSync(path.join(__dirname, '../backups/trend_momentum_indicator.v11.pine'), 'utf8');
const ENTRY = 'Entry decision (+1 long / -1 short)';
const EXIT = 'Completed exit (+1 long / -1 short)';
const HOUR = 3600000;

function fixture({ side = 1, warmup = 180, outcome = 'target' } = {}) {
    const bars = [];
    const add = (open, high, low, close) => {
        const i = bars.length;
        bars.push({ open, high, low, close, volume: 1000,
            openTime: Date.UTC(2025, 0, 1) + i * HOUR,
            closeTime: Date.UTC(2025, 0, 1) + (i + 1) * HOUR });
    };
    for (let i = 0; i < warmup; i++) {
        const c = 100 + i * 0.1;
        add(c - 0.05, c + 0.3, c - 0.3, c);
    }
    const shift = (warmup - 180) * 0.1;
    const scaled = (o, h, l, c) => add(o + shift, h + shift, l + shift, c + shift);
    scaled(117.5, 117.6, 116.9, 117.25); // touch known band, no entry yet
    if (outcome === 'expired') {
        for (let i = 0; i < 6; i++) scaled(117.3, 117.6, 116.9, 117.25);
    }
    scaled(117.3, 117.8, 117.2, 117.7); // later reclaim
    if (outcome === 'stop') scaled(117.7, 117.8, 115.5, 116.8);
    for (let i = 0; i < 30; i++) {
        const c = 117.8 + i * 0.15;
        scaled(c - 0.1, c + 0.2, c - 0.2, c);
    }
    return side === 1 ? bars : bars.map(b => ({ ...b,
        open: 260 - b.open, high: 260 - b.low,
        low: 260 - b.high, close: 260 - b.close }));
}

function provider(bars, requests) {
    return {
        async getMarketData(_symbol, tf) {
            requests.push(tf);
            const minutes = ({ D: 1440, '1D': 1440, W: 10080, '1W': 10080 })[tf] || Number(tf);
            assert.ok(Number.isFinite(minutes), `Unsupported test interval ${tf}`);
            if (minutes === 60) return bars;
            const buckets = new Map();
            for (const bar of bars) {
                const start = Math.floor(bar.openTime / (minutes * 60000)) * minutes * 60000;
                const old = buckets.get(start);
                if (old) {
                    old.high = Math.max(old.high, bar.high);
                    old.low = Math.min(old.low, bar.low);
                    old.close = bar.close;
                    old.volume += bar.volume;
                } else buckets.set(start, { ...bar, openTime: start, closeTime: start + minutes * 60000 });
            }
            return [...buckets.values()];
        },
        async getSymbolInfo() {
            return { tickerid: 'TEST', ticker: 'TEST', main_tickerid: 'TEST',
                timezone: 'Etc/UTC', type: 'crypto', mintick: 0.01, minmove: 1,
                pricescale: 100, pointvalue: 1, mincontract: 0.001, currency: 'USD', session: '24x7' };
        },
        configure() {},
    };
}

async function run(bars, inputs = {}) {
    const requests = [];
    // PineTS 0.9.33 ignores runtime input.timeframe overrides. Change only that
    // declaration's default for this test; the actual validation/engine is untouched.
    const testSource = inputs['Manual higher timeframe'] === undefined ? source : source.replace(
        'input.timeframe("1D", "Manual higher timeframe"',
        `input.timeframe(${JSON.stringify(inputs['Manual higher timeframe'])}, "Manual higher timeframe"`,
    );
    const ctx = await new PineTS(provider(bars, requests), 'TEST', '60').run(new Indicator(testSource, {
        'Require confirmed higher-timeframe trend': false, ...inputs,
    }));
    return { ctx, requests };
}
function events(ctx, name = ENTRY) { return ctx.plots[name].data.filter(x => x.value !== 0); }
function values(ctx, name) { return ctx.plots[name].data.map(x => x.value); }

test('long pullback arms first, emits once, fills next bar and reports completed exit', async () => {
    const { ctx } = await run(fixture());
    assert.equal(events(ctx).length, 1);
    assert.equal(values(ctx, ENTRY)[180], 0);
    assert.equal(values(ctx, ENTRY)[181], 1);
    assert.equal(ctx.strategy.closedtrades.length, 1);
    const trade = ctx.strategy.closedtrades[0];
    assert.equal(trade.entry_bar_index, 182);
    assert.equal(trade.exit_comment, 'TARGET');
    assert.equal(events(ctx, EXIT).length, 1);
    assert.equal(values(ctx, EXIT)[trade.exit_bar_index], 1);
    assert.ok(Number.isFinite(values(ctx, 'Protective stop')[181]));
    assert.ok(Number.isFinite(values(ctx, 'Planned target')[181]));
});

test('short pullback has distinct SHORT and EXIT SHORT events', async () => {
    const { ctx } = await run(fixture({ side: -1 }));
    assert.equal(events(ctx).length, 1);
    assert.equal(events(ctx)[0].value, -1);
    assert.equal(events(ctx, EXIT).length, 1);
    assert.equal(events(ctx, EXIT)[0].value, -1);
    assert.equal(ctx.strategy.closedtrades[0].entry_id, 'Short');
});

test('initial bracket can stop a trade on its entry bar; exit is observed even when flat', async () => {
    const { ctx } = await run(fixture({ outcome: 'stop' }));
    const trade = ctx.strategy.closedtrades[0];
    assert.equal(trade.entry_bar_index, 182);
    assert.equal(trade.exit_bar_index, 182);
    assert.equal(trade.exit_comment, 'STOP');
    assert.equal(values(ctx, EXIT)[182], 1);
    assert.equal(values(ctx, ENTRY)[182], 0);
});

test('expired touch cannot become a delayed reclaim entry', async () => {
    const { ctx } = await run(fixture({ outcome: 'expired' }));
    assert.equal(events(ctx).length, 0);
});

test('direction restriction applies to plotted entries and simulated orders', async () => {
    const { ctx } = await run(fixture(), { 'Entry direction': 'Short only' });
    assert.equal(events(ctx).length, 0);
    assert.equal(ctx.strategy.closedtrades.length, 0);
});

test('enabled volume filter rejects missing/zero volume and accepts adequate volume', async () => {
    const config = { 'Require volume on every entry': true };
    const missing = await run(fixture().map(b => ({ ...b, volume: 0 })), config);
    const adequate = await run(fixture(), config);
    assert.equal(events(missing.ctx).length, 0);
    assert.equal(events(adequate.ctx).length, 1);
});

test('extension and structure-risk limits block the otherwise valid setup', async () => {
    for (const config of [
        { 'Maximum distance from pullback EMA (ATR)': 0.25 },
        { 'Maximum signal risk (ATR)': 0.8 },
    ]) {
        const { ctx } = await run(fixture(), config);
        assert.equal(events(ctx).length, 0);
    }
});

test('date gate prevents entry decisions outside the evaluation period', async () => {
    const { ctx } = await run(fixture(), {
        'Restrict entry decisions to dates': true, 'Start': Date.UTC(2026, 0, 1),
    });
    assert.equal(events(ctx).length, 0);
});

test('confirmed opposing swing blocks a target that needs more room', async () => {
    const bars = fixture();
    bars[170] = { ...bars[170], high: 119.0 };
    const protectedRun = await run(bars);
    const withoutGate = await run(bars, { 'Reject target beyond known opposing swing': false });
    assert.equal(events(protectedRun.ctx).length, 0);
    assert.equal(events(withoutGate.ctx).length, 1);
});

test('confirmed trend loss requests and completes an exit independently of a short entry', async () => {
    const bars = fixture();
    for (let i = 0; i < 20; i++) {
        const close = 121.9 - i * 0.2;
        const previous = bars.at(-1);
        bars.push({ open: close + 0.1, high: close + 0.2, low: close - 0.2,
            close, volume: 1000, openTime: previous.closeTime, closeTime: previous.closeTime + HOUR });
    }
    const { ctx } = await run(bars, { 'Target / signal risk': 10, 'Entry direction': 'Long only' });
    assert.equal(ctx.strategy.closedtrades[0].exit_comment, 'TREND LOST');
    assert.ok(events(ctx, EXIT).some(x => x.value === 1));
    assert.ok(events(ctx).every(x => x.value === 1));
});

test('cooldown counts from completed exit, with no immediate reversal or new entry', async () => {
    const { ctx } = await run(fixture({ outcome: 'stop' }), { 'Minimum bars after entry or exit': 10 });
    const exitBar = ctx.strategy.closedtrades[0].exit_bar_index;
    assert.ok(values(ctx, ENTRY).slice(exitBar, exitBar + 10).every(x => x === 0));
});

test('enabled trailing stop never loosens and retains its target', async () => {
    for (const side of [1, -1]) {
        const { ctx } = await run(fixture({ side }), {
            'Tighten stop with ATR trail (keeps target)': true,
            'Trail activation / actual initial fill risk': 0.5,
            'Trail distance / frozen entry ATR': 0.5,
        });
        const stops = values(ctx, 'Protective stop').filter(Number.isFinite);
        const targets = values(ctx, 'Planned target').filter(Number.isFinite);
        assert.ok(stops.length > 2);
        assert.ok(stops.some(x => Math.abs(x - stops[0]) > 0.01));
        assert.ok(stops.every((x, i) => i === 0 || side * (x - stops[i - 1]) >= -1e-9));
        assert.ok(targets.every(x => x === targets[0]));
    }
});

test('optional breakout uses shared filters and does not repeatedly signal new highs', async () => {
    const bars = fixture();
    bars[180] = { ...bars[180], open: 118.3, high: 118.8, low: 118.2, close: 118.7 };
    const config = { 'Pullback and reclaim': false,
        'Direct range breakout (experimental)': true,
        'Maximum distance from pullback EMA (ATR)': 3 };
    const { ctx } = await run(bars, config);
    assert.equal(events(ctx).length, 1);
    assert.equal(values(ctx, ENTRY)[180], 1);
    const blocked = await run(bars.map(b => ({ ...b, volume: 0 })), {
        ...config, 'Require volume on every entry': true,
    });
    assert.equal(events(blocked.ctx).length, 0);
});

test('invalid HTF, risk, EMA, date and setup configurations fail clearly', async () => {
    const configs = [
        { 'Require confirmed higher-timeframe trend': true,
            'Higher timeframe selection': 'Manual', 'Manual higher timeframe': '60' },
        { 'Minimum signal risk (ATR)': 3, 'Maximum signal risk (ATR)': 3 },
        { 'Pullback EMA': 50, 'Trend EMA': 50 },
        { 'Restrict entry decisions to dates': true, 'Start': Date.UTC(2028, 0, 1) },
        { 'Pullback and reclaim': false, 'Direct range breakout (experimental)': false },
    ];
    for (const config of configs) await assert.rejects(run(fixture(), config), JSON.stringify(config));
});

test('default confirmed HTF path runs using aggregated 4H data', async () => {
    const { ctx, requests } = await run(fixture({ warmup: 300 }), {
        'Require confirmed higher-timeframe trend': true,
    });
    assert.ok(requests.includes('240'));
    assert.equal(events(ctx).length, 1);
});

test('future bars do not alter earlier entry/exit decisions on confirmed HTF path', async () => {
    const bars = fixture({ warmup: 300 });
    const config = { 'Require confirmed higher-timeframe trend': true };
    const full = await run(bars, config);
    const prefix = await run(bars.slice(0, 305), config);
    for (const title of [ENTRY, EXIT]) {
        assert.deepEqual(values(prefix.ctx, title), values(full.ctx, title).slice(0, 305));
    }
});
