const fs = require('node:fs');
const path = require('node:path');
const { PineTS, Indicator } = require('pinets');
const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'trend_momentum_indicator.pine'), 'utf8');
const ENTRY = 'Entry decision (+1 long / -1 short)';
const EXIT = 'Completed exit (+1 long / -1 short)';

/**
 * Generate synthetic bars with a directional sine-trend overlay.
 * @param {number} side +1 for uptrend, -1 for downtrend.
 * @param {number} count Total number of bars to generate.
 */
function synthetic(side = 1, count = 700) {
    const bars = [];
    for (let i = 0; i < count; i++) {
        const close = 100 + side * (i * 0.04 + Math.sin(i / 8) * 2);
        const open = i ? bars[i - 1].close : close - side * 0.1;
        bars.push({
            open,
            high: Math.max(open, close) + 0.05,
            low: Math.min(open, close) - 0.05,
            close,
            volume: 1000,
            openTime: Date.UTC(2025, 0, 1) + i * 3600000,
            closeTime: Date.UTC(2025, 0, 1) + (i + 1) * 3600000,
        });
    }
    return bars;
}

/**
 * Generate choppy / ranging bars for regime-filter testing.
 * Price oscillates in a tight range, producing high choppiness.
 */
function choppyBars(count = 700) {
    const bars = [];
    for (let i = 0; i < count; i++) {
        const close = 100 + Math.sin(i / 6) * 1.5;
        const open = i ? bars[i - 1].close : close;
        bars.push({
            open,
            high: Math.max(open, close) + 0.3,
            low: Math.min(open, close) - 0.3,
            close,
            volume: 500,
            openTime: Date.UTC(2025, 0, 1) + i * 3600000,
            closeTime: Date.UTC(2025, 0, 1) + (i + 1) * 3600000,
        });
    }
    return bars;
}

function provider(bars, baseTf, tick) {
    return {
        async getMarketData(_ticker, tf) {
            if (tf === baseTf) return bars;
            const minutes = ({ D: 1440, '1D': 1440, W: 10080, '1W': 10080 })[tf] || Number(tf);
            if (!Number.isFinite(minutes)) throw Error(`Unsupported test timeframe ${tf}`);
            const buckets = new Map();
            for (const b of bars) {
                const start = Math.floor(b.openTime / (minutes * 60000)) * minutes * 60000;
                const previous = buckets.get(start);
                if (previous) {
                    previous.high = Math.max(previous.high, b.high);
                    previous.low = Math.min(previous.low, b.low);
                    previous.close = b.close;
                    previous.volume += b.volume;
                } else buckets.set(start, { ...b, openTime: start, closeTime: start + minutes * 60000 });
            }
            return [...buckets.values()];
        },
        async getSymbolInfo() {
            return {
                tickerid: 'TEST', ticker: 'TEST', main_tickerid: 'TEST',
                timezone: 'Etc/UTC', type: 'crypto', mintick: tick, minmove: 1,
                pricescale: 1 / tick, pointvalue: 1, mincontract: tick < 0.01 ? 1 : 0.001,
                currency: 'USD', session: '24x7',
            };
        },
        configure() {},
    };
}

async function run(bars, inputs = {}, { code = source, timeframe = '60', tick = 0.01 } = {}) {
    if (inputs['Manual HTF'] !== undefined)
        code = code.replace(
            'input.timeframe("1D", "Manual HTF"',
            `input.timeframe(${JSON.stringify(inputs['Manual HTF'])}, "Manual HTF"`,
        );
    return new PineTS(provider(bars, timeframe, tick), 'TEST', timeframe).run(new Indicator(code, inputs));
}

function values(ctx, name) { return ctx.plots[name].data.map(p => p.value); }
function entries(ctx) { return values(ctx, ENTRY).map((side, bar) => ({ side, bar })).filter(x => x.side); }
function marketBars() {
    return JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/pine/TSTUSDT-4h.json')))
        .slice(0, -1)
        .map(k => ({
            openTime: k[0], open: +k[1], high: +k[2], low: +k[3],
            close: +k[4], volume: +k[5], closeTime: k[6] + 1,
        }));
}

module.exports = { ROOT, source, ENTRY, EXIT, synthetic, choppyBars, run, values, entries, marketBars };
