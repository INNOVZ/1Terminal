const { test } = require('node:test');
const assert = require('node:assert/strict');
const { source, oscillator, ENTRY, EXIT, synthetic, choppyBars, run, values, entries, marketBars } = require('./pine_v13_helpers.cjs');

/**
 * Verify that every accepted entry has a fresh MACD cross within the confirmation window,
 * that each cross is consumed exactly once, and that stop/target are set immediately.
 */
function verifyMacdContract(ctx, bars, window = 2) {
    const line   = values(ctx, 'MACD line (Data Window)');
    const signal = values(ctx, 'MACD signal (Data Window)');
    const ema    = values(ctx, 'Confirmation EMA');
    const hist   = values(ctx, 'MACD histogram (Data Window)');
    const used   = new Set();
    for (const { side, bar } of entries(ctx)) {
        let cross = -1;
        for (let i = bar; i >= Math.max(1, bar - window); i--) {
            if (side * (line[i] - signal[i]) > 0 && side * (line[i - 1] - signal[i - 1]) <= 0) {
                cross = i;
                break;
            }
        }
        assert.ok(cross >= 0, `Entry at bar ${bar} has no fresh directional MACD cross within ${window} bars`);
        const key = `${side}:${cross}`;
        assert.ok(!used.has(key), `MACD cross ${key} was consumed twice`);
        used.add(key);
        // Price must be on the correct side of EMA
        assert.ok(side * (bars[bar].close - ema[bar]) > 0,
            `Entry at bar ${bar}: close not on correct side of EMA`);
        // Stop and target must exist
        assert.ok(Number.isFinite(values(ctx, 'Protective stop')[bar]),
            `Entry at bar ${bar}: no protective stop`);
        assert.ok(Number.isFinite(values(ctx, 'Planned target')[bar]),
            `Entry at bar ${bar}: no planned target`);
    }
}

// ─── V13 CORE MACD CONTRACT ────────────────────────────────────────────────────

test('V13: both directions have fresh MACD entries and consume each cross once', async () => {
    // Use legacy MACD+EMA mode with relaxed filters to isolate MACD contract testing
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
        'Reject target beyond known opposing swing': false,
    };
    for (const side of [1, -1]) {
        const bars = synthetic(side);
        const ctx = await run(bars, overrides);
        assert.ok(entries(ctx).some(e => e.side === side),
            `Expected at least one ${side === 1 ? 'long' : 'short'} entry`);
        verifyMacdContract(ctx, bars);
        assert.ok(ctx.strategy.closedtrades.length > 0, 'Expected at least one closed trade');
        for (const trade of ctx.strategy.closedtrades) {
            assert.ok(entries(ctx).some(e => e.bar + 1 === trade.entry_bar_index));
            assert.equal(values(ctx, EXIT)[trade.exit_bar_index], Math.sign(trade.size));
        }
    }
});

test('V13: minimum confirmation window (1 bar) permits cross + one bar only', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Confirmation window after MACD cross': 1,
        'Require histogram expansion (2 bars for Confluence)': false,
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const bars = synthetic();
    const ctx = await run(bars, overrides);
    assert.ok(entries(ctx).length > 0);
    verifyMacdContract(ctx, bars, 1);
});

test('V13: no MACD recross means no entries in an uninterrupted linear trend', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const bars = synthetic().map((b, i) => ({
        ...b,
        open: 100 + i * 0.1,
        close: 100.05 + i * 0.1,
        high: 100.1 + i * 0.1,
        low: 99.95 + i * 0.1,
    }));
    const ctx = await run(bars, overrides);
    assert.equal(entries(ctx).length, 0);
});

// ─── V13 REGIME FILTERS ────────────────────────────────────────────────────────

test('V13: choppiness filter blocks entries in ranging markets', async () => {
    const bars = choppyBars();
    const withChop = await run(bars, {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': true,
        'Require price on correct side of EMA200': false,
    });
    const withoutChop = await run(bars, {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    });
    // The choppy data should produce fewer or zero entries when the filter is active
    assert.ok(entries(withChop).length <= entries(withoutChop).length,
        `Choppiness filter should reduce entries: ${entries(withChop).length} vs ${entries(withoutChop).length}`);
});

test('V13: ADX filter blocks entries when trend strength is low', async () => {
    const bars = synthetic();
    const withAdx = await run(bars, {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': true,
        'Minimum ADX': 40,  // Very high threshold
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    });
    const withoutAdx = await run(bars, {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    });
    assert.ok(entries(withAdx).length <= entries(withoutAdx).length,
        'ADX filter with high threshold should reduce entries');
});

// ─── V13 DIRECTION AND VOLUME FILTERS ──────────────────────────────────────────

test('V13: direction and volume filters apply to entries', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const longOnly = await run(synthetic(-1), { ...overrides, 'Entry direction': 'Long only' });
    assert.ok(entries(longOnly).every(e => e.side === 1));
    const zeroVol = await run(synthetic().map(b => ({ ...b, volume: 0 })), {
        ...overrides, 'Require volume on every entry (legacy gate)': true,
    });
    assert.equal(entries(zeroVol).length, 0);
});

// ─── V13 EXITS ─────────────────────────────────────────────────────────────────

test('V13: opposing MACD with EMA confirmation exits independently', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Target / signal risk': 10,
        'Initial stop model': 'ATR',
        'ATR stop distance (when ATR mode)': 3,
        'Maximum signal risk (ATR)': 5,
        'Exit on EMA50 structure loss': false,
        'Entry direction': 'Long only',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
        'Exit stagnant trades': false,
        'Take partial profit': false,
        'Tighten stop with ATR trail (keeps target)': false,
        'Closes beyond trend EMA to exit': 1,
        'Reject target beyond known opposing swing': false,
    };
    const ctx = await run(synthetic(), overrides);
    assert.ok(ctx.strategy.closedtrades.length > 0, 'Expected at least one closed trade');
    // PineTS exit_comment may be a series buffer: extract the comment string
    const getComment = (t) => {
        if (typeof t.exit_comment === 'string') return t.exit_comment;
        if (t.exit_comment && t.exit_comment.data) return t.exit_comment.data[t.exit_comment.data.length - 1];
        return String(t.exit_comment);
    };
    const comments = ctx.strategy.closedtrades.map(getComment);
    assert.ok(comments.some(c => c === 'MACD EXIT' || c === 'STOP'),
        `Expected MACD EXIT or STOP in comments: ${JSON.stringify(comments.slice(0, 5))}`);
    assert.ok(entries(ctx).every(e => e.side === 1));
});

// ─── V13 PROTECTIVE BRACKET & COOLDOWN ──────────────────────────────────────────

test('V13: protective bracket works on entry candle and cooldown prevents replacement', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
        'Exit stagnant trades': false,
        'Take partial profit': false,
        'Tighten stop with ATR trail (keeps target)': false,
    };
    const bars = synthetic();
    const baseline = await run(bars, overrides);
    const first = entries(baseline)[0];
    const stop = values(baseline, 'Protective stop')[first.bar];
    const i = first.bar + 1;
    bars[i] = { ...bars[i], low: stop - 1 };
    const ctx = await run(bars, overrides);
    const closed = ctx.strategy.closedtrades[0];
    assert.equal(closed.entry_bar_index, i);
    assert.equal(closed.exit_bar_index, i);
    assert.equal(closed.exit_comment, 'STOP');
    assert.equal(values(ctx, EXIT)[i], 1);
    assert.equal(values(ctx, ENTRY)[i], 0);
    assert.equal(values(ctx, ENTRY)[i + 1], 0);
});

// ─── V13 SIZING ─────────────────────────────────────────────────────────────────

test('V13: first order is capped by signal risk budget and notional exposure', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
        'Exit stagnant trades': false,
        'Take partial profit': false,
    };
    const bars = synthetic();
    const ctx = await run(bars, overrides);
    const first = entries(ctx)[0];
    const trade = ctx.strategy.closedtrades[0];
    const risk = Math.abs(bars[first.bar].close - values(ctx, 'Protective stop')[first.bar]);
    assert.ok(Math.abs(trade.size) * risk <= 50.001);
    assert.ok(Math.abs(trade.size) * bars[first.bar].close <= 2500.001);
});

// ─── V13 INVALID CONFIGURATION ──────────────────────────────────────────────────

test('V13: invalid MACD lengths and non-higher HTF settings fail', async () => {
    await assert.rejects(run(synthetic(), { 'MACD fast length': 26, 'MACD slow length': 26 }));
    await assert.rejects(run(synthetic(), {
        'Require confirmed higher-timeframe trend': true,
        'Higher timeframe selection': 'Manual',
        'Manual higher timeframe': '60',
    }));
});

// ─── V13 OSCILLATOR PARITY ──────────────────────────────────────────────────────

test('V13: oscillator and strategy calculate identical MACD series with matched inputs', { skip: !oscillator }, async () => {
    const bars = synthetic();
    const config = {
        'MACD fast length': 8, 'MACD slow length': 21, 'MACD signal length': 5,
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const strategy = await run(bars, config);
    const pane = await run(bars, config, { code: oscillator });
    for (const [a, b] of [
        ['MACD', 'MACD line (Data Window)'],
        ['Signal', 'MACD signal (Data Window)'],
        ['Histogram', 'MACD histogram (Data Window)'],
    ]) {
        assert.deepEqual(values(pane, a), values(strategy, b));
    }
});

// ─── V13 DISPLAY ────────────────────────────────────────────────────────────────

test('V13: default display avoids duplicate custom labels and has dashboard', async () => {
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const ctx = await run(synthetic(), overrides);
    assert.equal((ctx.plots.__labels__?.data?.[0]?.value || []).length, 0);
    assert.ok(source.includes('(barstate.islastconfirmedhistory or barstate.islast) and showDashboard'));
    assert.ok(ctx.plots.__tables__);
});

// ─── V13 CONFLUENCE MODE ────────────────────────────────────────────────────────

test('V13: confluence mode produces fewer entries than MACD+EMA mode on same data', async () => {
    const bars = synthetic();
    const baseOverrides = {
        'Require confirmed higher-timeframe trend': false,
        'Require price on correct side of EMA200': false,
    };
    const macdEma = await run(bars, { ...baseOverrides, 'Entry model': 'MACD + EMA',
        'Require ADX strength': false, 'Require low choppiness (trending market)': false });
    const confluence = await run(bars, { ...baseOverrides, 'Entry model': 'Confluence' });

    // Confluence should have fewer or equal entries due to additional filters
    assert.ok(entries(confluence).length <= entries(macdEma).length,
        `Confluence (${entries(confluence).length}) should have ≤ entries than MACD+EMA (${entries(macdEma).length})`);
});

// ─── V13 REAL DATA ──────────────────────────────────────────────────────────────

test('V13: real TST 4H bars satisfy contract and prefix consistency', async () => {
    const bars = marketBars();
    const overrides = {
        'Entry model': 'MACD + EMA',
        'Confirmation window after MACD cross': 3,
        'Require confirmed higher-timeframe trend': false,
        'Require ADX strength': false,
        'Require low choppiness (trending market)': false,
        'Require price on correct side of EMA200': false,
    };
    const options = { timeframe: '240', tick: 0.00001 };
    const full = await run(bars, overrides, options);
    assert.ok(entries(full).length > 0);
    verifyMacdContract(full, bars, 3);
    const partial = await run(bars.slice(0, 1000), overrides, options);
    for (const title of [ENTRY, EXIT]) {
        assert.deepEqual(values(partial, title), values(full, title).slice(0, 1000));
    }
});
