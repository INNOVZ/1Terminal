# Alt-momentum v8 discovery preregistration

Date registered: 2026-08-17

Status: observation-only research layer; it cannot create an executable signal.

## Problem statement

The existing strategy profiles answer whether one instrument passes a local entry hypothesis.
Their cross-sectional rank is applied only after that eligibility decision. They therefore do not
answer the separate operator question: "which liquid altcoins are beginning to gain or lose
token-specific momentum relative to the rest of the universe and to BTC?"

V8 separates discovery from entry. Discovery should surface an emerging move before a complete
breakout/retest entry exists; execution must still wait for a separately validated setup,
anti-chase, structural room, sizing and portfolio-risk approval.

## Point-in-time universe

- USD-M USDT contracts that are currently trading and orderable on the configured execution venue.
- BTC, stablecoins and configured multiplier contracts are excluded from the displayed altcoin
  ranks. BTC remains the benchmark.
- The existing minimum 24h quote volume and maximum spread controls apply before ranking.
- The universe is capped by the existing volume-ranked `max_symbols_to_analyze` setting.
- A minimum of 169 aligned, closed 1h candles is required. V8 intentionally does not inherit the
  strategy screener's 480 closed 4h-candle requirement.

This current-listing universe is suitable for live observation but is not survivorship-safe
historical evidence.

## Registered features

For every altcoin, using closed hourly candles only:

1. Absolute 6h, 24h and 7d returns.
2. Six-hour acceleration: current 6h return minus the preceding 6h return.
3. Seven-day hourly OLS BTC beta and return correlation.
4. BTC-residual 6h, 24h and 7d returns:
   `alt return - beta * BTC return`.
5. Recent participation: mean quote volume over the latest 6h divided by the preceding 24h mean.
6. EMA20/EMA50 location, RSI14, MACD histogram, ADX and DMI as descriptive context only.

Missing or degenerate BTC history fails the observation closed; the row is not ranked with a
fabricated zero beta.

## Cross-sectional score

Each timestamp ranks every feature within the same point-in-time altcoin universe. The long score
is a 0-100 weighted percentile composite:

- BTC-residual 6h return: 30%
- BTC-residual 24h return: 25%
- BTC-residual 7d return: 15%
- six-hour acceleration: 20%
- volume participation: 10%

The short score mirrors the three residual-return and acceleration percentiles and uses the same
participation percentile. This makes long/short ranking mathematically symmetric without claiming
that their trading expectancy is symmetric.

## Phase labels

- `emerging_up`: positive absolute and BTC-residual 6h return, positive acceleration and close
  above EMA20.
- `confirmed_up`: at least two absolute and two residual horizons are positive and close is above
  EMA50.
- `emerging_down` and `confirmed_down`: exact directional mirrors.
- otherwise `mixed`.

Phase is explanatory and does not alter the numeric rank. RSI, MACD and ADX cannot create or veto
a phase.

## Promotion boundary

The first implementation exposes `atlas alt-momentum` and unit-tested pure feature/ranking logic.
It does not modify the configured strategy profile, candidate eligibility or order execution.
Before any v8 feature may enter execution it must demonstrate forward rank information on stored
hourly observations, then positive expectancy in a point-in-time multi-symbol portfolio replay
under doubled costs and untouched chronological holdouts.

## Operational smoke result — 2026-08-17

The first live-public-data run evaluated the 40 highest-volume liquid USD-M alts permitted by the
configured execution venue. All 40 had aligned history and were ranked; none was skipped. The
configured `legacy_momentum_v1` strategy remained unchanged. The output marks the v8 block as
`observation_only` and `used_for_candidate_eligibility: false`; candidate execution eligibility is
reported separately so the two concepts cannot be confused.

The snapshot demonstrated why discovery must remain distinct from entry. ZEC ranked first for
relative long strength with positive absolute and BTC-residual momentum, positive six-hour
acceleration and 4.66x recent participation. Other high-return instruments showed negative
acceleration or exhausted RSI, which is visible operator context rather than evidence to chase.
This smoke run verifies calculation and presentation only; it supplies no forward performance
label and makes no profitability claim.
