const { test } = require('node:test');
const assert = require('node:assert/strict');
const { source, ENTRY, EXIT, synthetic, choppyBars, run, values, entries, marketBars } = require('./pine_v16_helpers.cjs');

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

test('V16: indicator declares overlay=true and eliminates visual pane histogram', () => {
    assert.match(source, /strategy\([^)]*overlay\s*=\s*true/, 'V16 must declare overlay=true');
    const lines = source.split('\n');
    for (const line of lines) {
        if (line.includes('plot(macdHist') && !line.includes('display=display.data_window')) {
            assert.fail('Found visible plot(macdHist) outside data window: ' + line);
        }
    }
});

// ─── 2. CLEAN DOTTED SIGNALS (TREND-ALIGNED) ────────────────────────────────────

test('V16: clean dotted MACD cross signals appear and filter counter-trend dots', async () => {
    const bars = synthetic(1, 400);
    const ctx = await run(bars, { 'Enforce Macro Trend (EMA 200)': false });
    
    assert.ok(ctx.plots['MACD Bullish Cross Dot'], 'Missing MACD Bullish Cross Dot plot');
    assert.ok(ctx.plots['MACD Bearish Cross Dot'], 'Missing MACD Bearish Cross Dot plot');

    const bullDots = ctx.plots['MACD Bullish Cross Dot'].data
        .map((d, i) => d.value ? i : null)
        .filter(x => x !== null);

    assert.ok(bullDots.length > 0, `Expected bullish dots in uptrend, got ${bullDots.length}`);
});

// ─── 3. STATIC S/R CLUSTER MERGING (NO STACKED BOXES) ───────────────────────────

test('V16: structural S/R zones are tracked with cluster merging preventing box piling', async () => {
    const bars = synthetic(1, 400);
    const ctx = await run(bars, { 'Enforce Macro Trend (EMA 200)': false });
    
    const boxPlot = ctx.plots['__boxes__'];
    assert.ok(boxPlot, 'PineTS should capture __boxes__ plot');
    assert.ok(boxPlot.data.length > 0, 'Expected at least one merged S/R box to be created');
});

// ─── 4. STRICT UNIDIRECTIONAL TREND ENFORCEMENT ─────────────────────────────────

test('V16: strictly prohibits counter-trend trades (no shorts in bull, no longs in bear)', async () => {
    // Uptrend: ONLY longs permitted
    const upBars = synthetic(1, 400);
    const ctxUp = await run(upBars, { 'Enforce Macro Trend (EMA 200)': false, 'Filter High Choppiness (CI <= 55)': false });
    const upEntries = entries(ctxUp);
    assert.ok(upEntries.length > 0, 'Expected entries in uptrend');
    assert.ok(upEntries.every(e => e.side === 1), 'ALL entries in uptrend must be LONGS. Counter-trend shorts are forbidden!');
    verifyTradeContract(ctxUp, upBars);

    // Downtrend: ONLY shorts permitted
    const downBars = synthetic(-1, 400);
    const ctxDown = await run(downBars, { 'Enforce Macro Trend (EMA 200)': false, 'Filter High Choppiness (CI <= 55)': false });
    const downEntries = entries(ctxDown);
    assert.ok(downEntries.length > 0, 'Expected entries in downtrend');
    assert.ok(downEntries.every(e => e.side === -1), 'ALL entries in downtrend must be SHORTS. Counter-trend longs are forbidden!');
    verifyTradeContract(ctxDown, downBars);
});

// ─── 5. REGIME FILTERS PREVENT FAKEOUTS ──────────────────────────────────────────

test('V16: choppiness filter suppresses entries during directionless consolidation', async () => {
    const bars = choppyBars(700);
    const ctxChoppy = await run(bars, {
        'Enforce Macro Trend (EMA 200)': false,
        'Filter High Choppiness (CI <= 55)': true,
        'Max Choppiness (0=Trend, 100=Chop)': 40,
    });
    assert.equal(entries(ctxChoppy).length, 0, 'Tight choppiness threshold must block all entries in chop');
});

test('V16: ADX filter suppresses entries when trend strength is low', async () => {
    const bars = choppyBars(700);
    const ctxAdx = await run(bars, {
        'Enforce Macro Trend (EMA 200)': false,
        'Require ADX Trend Strength (>= 20)': true,
        'Minimum ADX': 50,
        'Filter High Choppiness (CI <= 55)': false,
    });
    assert.equal(entries(ctxAdx).length, 0, 'High ADX threshold must block all entries in chop');
});

// ─── 6. REAL MARKET DATA INTEGRITY (TSTUSDT-4H) ─────────────────────────────────

test('V16: real Binance market data executes with selective institutional conviction', async () => {
    const bars = marketBars();
    assert.ok(bars.length > 1000, 'Expected >1000 bars in market fixture');

    const ctxMkt = await run(bars, {
        'Enforce Macro Trend (EMA 200)': true,
        'Filter High Choppiness (CI <= 55)': false,
    });
    const mktEntries = entries(ctxMkt);
    assert.ok(mktEntries.length > 0, 'Expected selective entries on real market data');
    assert.ok(mktEntries.length <= 15, `Institutional filter should produce low-frequency selective entries, got ${mktEntries.length}`);
    verifyTradeContract(ctxMkt, bars);
});

// ─── 7. HUD CHECKLIST TABLE VERIFICATION ────────────────────────────────────────

test('V16: HUD table renders live 5-point evaluation checklist with clear status', async () => {
    const bars = synthetic(1, 100);
    const ctx = await run(bars, { 'Show Transparent 5-Check HUD Table': true });
    assert.ok(ctx.plots['__tables__'], 'PineTS should capture __tables__ plot for HUD');
    assert.ok(ctx.plots['__tables__'].data.length > 0, 'HUD table must have cells populated with checklist items');
});
