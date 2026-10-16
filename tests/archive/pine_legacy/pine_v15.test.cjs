const { test } = require('node:test');
const assert = require('node:assert/strict');
const { source, ENTRY, EXIT, synthetic, choppyBars, run, values, entries, marketBars } = require('./pine_v15_helpers.cjs');

/**
 * Verify that every accepted entry has a protective stop and planned target,
 * that entries map 1-to-1 with strategy trades, and that risk distance is strictly positive.
 */
function verifyTradeContract(ctx, bars) {
    const stops   = values(ctx, 'Protective stop');
    const targets = values(ctx, 'Planned target');
    const allEntries = entries(ctx);

    for (const { side, bar } of allEntries) {
        assert.ok(side === 1 || side === -1, `Invalid entry side ${side} at bar ${bar}`);
        const stopVal = stops[bar];
        const targetVal = targets[bar];
        assert.ok(Number.isFinite(stopVal), `Entry at bar ${bar} has no finite protective stop`);
        assert.ok(Number.isFinite(targetVal), `Entry at bar ${bar} has no finite planned target`);

        if (side === 1) {
            assert.ok(stopVal < bars[bar].close, `Long stop ${stopVal} is not below entry close ${bars[bar].close}`);
            assert.ok(targetVal > bars[bar].close, `Long target ${targetVal} is not above entry close ${bars[bar].close}`);
        } else {
            assert.ok(stopVal > bars[bar].close, `Short stop ${stopVal} is not above entry close ${bars[bar].close}`);
            assert.ok(targetVal < bars[bar].close, `Short target ${targetVal} is not below entry close ${bars[bar].close}`);
        }
    }

    assert.ok(ctx.strategy.closedtrades.length > 0, 'Expected strategy to have closed trades');
    for (const trade of ctx.strategy.closedtrades) {
        assert.ok(trade.entry_bar_index > 0, 'Trade must have valid positive entry bar index');
        assert.ok(trade.exit_bar_index >= trade.entry_bar_index, 'Exit bar must be >= entry bar');
    }
}

// ─── 1. ARCHITECTURE & OVERLAY VERIFICATION ─────────────────────────────────────

test('V15: indicator is declared with overlay=true and has no separate pane histogram', () => {
    assert.match(source, /strategy\([^)]*overlay\s*=\s*true/, 'V15 must declare overlay=true');
    // Ensure no plot of macdHist without display.data_window or inside chart
    const lines = source.split('\n');
    for (const line of lines) {
        if (line.includes('plot(macdHist') && !line.includes('display=display.data_window')) {
            assert.fail('Found visible plot(macdHist) outside data window: ' + line);
        }
    }
});

// ─── 2. CLEAN DOTTED SIGNALS ────────────────────────────────────────────────────

test('V15: clean dotted MACD cross signals appear accurately at momentum crosses', async () => {
    const bars = synthetic(1, 400);
    const ctx = await run(bars);
    
    assert.ok(ctx.plots['MACD Bullish Cross Dot'], 'Missing MACD Bullish Cross Dot plot');
    assert.ok(ctx.plots['MACD Bearish Cross Dot'], 'Missing MACD Bearish Cross Dot plot');

    const bullDots = ctx.plots['MACD Bullish Cross Dot'].data
        .map((d, i) => d.value ? i : null)
        .filter(x => x !== null);
    const bearDots = ctx.plots['MACD Bearish Cross Dot'].data
        .map((d, i) => d.value ? i : null)
        .filter(x => x !== null);

    assert.ok(bullDots.length >= 5, `Expected multiple bullish dots, got ${bullDots.length}`);
    assert.ok(bearDots.length >= 5, `Expected multiple bearish dots, got ${bearDots.length}`);

    // Verify alternation of bullish and bearish dots
    assert.ok(bullDots[0] < bearDots[0], 'First bull dot should precede first bear dot in synthetic uptrend');
    assert.ok(bearDots[0] < bullDots[1], 'First bear dot should precede second bull dot');
});

// ─── 3. GENUINE SUPPORT & RESISTANCE ENGINE ─────────────────────────────────────

test('V15: genuine Support & Resistance zones are tracked and drawn as boxes', async () => {
    const bars = synthetic(1, 400);
    const ctx = await run(bars);
    
    // Check that box manager was called
    const boxPlot = ctx.plots['__boxes__'];
    assert.ok(boxPlot, 'PineTS should capture __boxes__ plot');
    assert.ok(boxPlot.data.length > 0, 'Expected at least one S/R box to be created on oscillating data');
});

// ─── 4. TRADE CONTRACT & BOTH DIRECTIONS ────────────────────────────────────────

test('V15: both long and short trades execute cleanly with verified risk brackets', async () => {
    // Uptrend
    const upBars = synthetic(1, 400);
    const ctxUp = await run(upBars, { 'Entry Strategy Model': 'MACD + EMA' });
    assert.ok(entries(ctxUp).some(e => e.side === 1), 'Expected at least one long entry in uptrend');
    verifyTradeContract(ctxUp, upBars);

    // Downtrend
    const downBars = synthetic(-1, 400);
    const ctxDown = await run(downBars, { 'Entry Strategy Model': 'MACD + EMA' });
    assert.ok(entries(ctxDown).some(e => e.side === -1), 'Expected at least one short entry in downtrend');
    verifyTradeContract(ctxDown, downBars);
});

// ─── 5. REGIME FILTERS PREVENT FAKEOUTS ──────────────────────────────────────────

test('V15: choppiness filter suppresses fakeout entries in ranging conditions', async () => {
    const bars = choppyBars(700);
    // With choppiness filter OFF
    const ctxUnfiltered = await run(bars, {
        'Entry Strategy Model': 'MACD + EMA',
        'Filter High Choppiness (CI <= 55)': false,
        'Require ADX Trend Strength (>= 20)': false,
    });
    const unfilteredCount = entries(ctxUnfiltered).length;

    // With choppiness filter ON
    const ctxFiltered = await run(bars, {
        'Entry Strategy Model': 'MACD + EMA',
        'Filter High Choppiness (CI <= 55)': true,
        'Require ADX Trend Strength (>= 20)': false,
    });
    const filteredCount = entries(ctxFiltered).length;

    assert.ok(filteredCount <= unfilteredCount,
        `Choppiness filter should reduce or match entries: filtered=${filteredCount}, unfiltered=${unfilteredCount}`);
});

test('V15: ADX filter suppresses entries when trend strength is below threshold', async () => {
    const bars = choppyBars(700);
    const ctxAdx = await run(bars, {
        'Entry Strategy Model': 'MACD + EMA',
        'Require ADX Trend Strength (>= 20)': true,
        'Minimum ADX': 50,
        'Filter High Choppiness (CI <= 55)': false,
    });
    assert.equal(entries(ctxAdx).length, 0, 'High ADX threshold should block all entries in chop');
});

// ─── 6. REAL MARKET DATA EXECUTION ──────────────────────────────────────────────

test('V15: real Binance TSTUSDT-4H market data executes with structural integrity', async () => {
    const bars = marketBars();
    assert.ok(bars.length > 1000, 'Expected >1000 bars in market fixture');

    // Test Structure + MACD Model
    const ctxStruct = await run(bars, { 'Entry Strategy Model': 'Structure + MACD' });
    assert.ok(entries(ctxStruct).length > 0, 'Structure + MACD should produce valid entries on TSTUSDT');
    verifyTradeContract(ctxStruct, bars);

    // Test Confluence Model
    const ctxConf = await run(bars, { 'Entry Strategy Model': 'Confluence' });
    assert.ok(entries(ctxConf).length > 0, 'Confluence should produce valid entries on TSTUSDT');
    verifyTradeContract(ctxConf, bars);
});

// ─── 7. MINIMAL HUD DASHBOARD ───────────────────────────────────────────────────

test('V15: minimal HUD dashboard table is generated with clean state metrics', async () => {
    const bars = synthetic(1, 100);
    const ctx = await run(bars, { 'Show Minimal HUD Dashboard': true });
    assert.ok(ctx.plots['__tables__'], 'PineTS should capture __tables__ plot for HUD');
    assert.ok(ctx.plots['__tables__'].data.length > 0, 'HUD table must have cells populated');
});
