# Atlas architecture

## 1. Architectural goal

Atlas separates research decisions from account authority. A strategy cannot call an exchange,
an exchange adapter cannot size a trade, and the API cannot submit arbitrary user-shaped orders.
The only path to an order is:

```text
candidate → deterministic signal → portfolio risk approval → persisted intent → venue execution
                                                                                      ↓
                                                            managed exit (scale out → breakeven → trail)
```

This is a modular monolith for the first production stage. Splitting it into networked services
would add failure modes before scale requires them. Boundaries are expressed as Python protocols,
so components can later move behind queues or services without changing domain logic.

## 2. Modules

| Module | Responsibility | Explicitly forbidden |
|---|---|---|
| `domain` | Value objects, instruments, orders, positions, ports | Network and database access |
| `domain.structure` | Swing pivots, HH/HL classification, structural levels | Network and database access |
| `application.alpha` | Pure, profile-specific alpha eligibility and evidence | Account access, persistence, and orders |
| `application.alt_momentum` | Pure BTC-residual altcoin discovery features and symmetric ranks | Candidate eligibility and orders |
| `application.screener` | Universe filtering and deterministic ranking | Account access and orders |
| `application.strategy` | Candidate-to-signal conversion | Position sizing |
| `application.risk` | Portfolio gates and fixed-fraction sizing | Venue API calls |
| `application.execution` | Idempotency and entry/protection workflow | Signal generation |
| `application.position_manager` | Exit decisions from price and structure (pure) | Venue API calls |
| `application.manage` | Applying exit decisions to a venue | Exit policy decisions |
| `application.reconciliation` | Venue/local position truth and authoritative close booking | Screening and signal policy |
| `application.service` | Cycle, screening, and execution orchestration facade | Venue-specific payloads |
| `adapters.binance` | Signing, filters, endpoint mappings, order semantics | Trading policy |
| `adapters.paper` | Deterministic simulated fills | Live account access |
| `infrastructure` | State, wiring, deployment concerns | Strategy decisions |
| `api` | Authenticated operational controls | Arbitrary order placement |

## 3. Venue model

| Exposure | Venue | Long | Short | Non-production environment |
|---|---|---:|---:|---|
| Crypto cash | Binance Spot | Yes | No | Spot Testnet |
| Crypto perpetuals | Binance USD-M | Yes | Yes | Futures Testnet |
| Stocks/ETFs | IBKR | Yes | Subject to borrow | IBKR Paper |
| Index exposure | ETF/index future/option at IBKR | Yes | Product-dependent | IBKR Paper |
| Cash index value | Market-data benchmark | No | No | Not orderable |

Spot shorts are never synthesized. The routing layer must choose USD-M Futures or a broker product
that explicitly supports short exposure.

### Market data in testnet mode

Binance Spot Testnet serves roughly five days of 4h klines — 29 candles against the 480 the
screener requires — so screening fails outright there, and its synthetic prices would make any
signal computed on them describe a market that does not exist.

`MarketDataPort` and `ExecutionPort` are separate protocols, so testnet runs bind them to
different sources (`adapters.binance.bridge.LiveDataTestnetBridge`):

| Concern | Source | Why |
|---|---|---|
| `instruments` | execution venue | Authoritative for what is orderable, and carries the tick size, step size and min notional that decide order validity |
| `quotes` | live public API | Real spread and price |
| `candles` | live public API | Real history; testnet does not have enough |

Only unauthenticated endpoints are used against the live venue: no API key is presented there,
and the bridge does not implement `ExecutionPort`, so no order can reach it. Disable with
`ATLAS_TESTNET_USES_LIVE_MARKET_DATA=false`, accepting that Spot then produces no candidates.

## 4. Screening pipeline

Screening runs only after a candle is closed. It uses two stages to control rate usage:

1. Retrieve exchange instruments, 24h quote volume, and book tickers.
2. Keep active USDT instruments above liquidity and below spread thresholds.
3. Sort by liquidity and cap the analysis universe.
4. Retrieve 260 closed 1h candles and at least 480 closed 4h candles by default. The slow profile
   also retrieves 260 daily candles.
5. Calculate support and resistance from the preceding 48 closed 1h bars, excluding the signal bar.
6. Route observations through the configured alpha policy. The legacy policy classifies three-bar
   price/MACD/RSI/ADX momentum. The research breakout policy uses 7d/30d returns, a prior-channel
   break, token-relative volatility, and volume/spread quality.
7. Keep MACD/RSI/ADX as legacy hard gates only. In the breakout profile they are bounded ranking
   diagnostics and cannot independently admit or reject a trade.
8. Rank eligible breakout candidates cross-sectionally by directional 30-day strength. Ranking
   allocates scarce portfolio capacity; it never converts an ineligible setup into an eligible one.
9. Block longs within 0.5 ATR of resistance and shorts within 0.5 ATR of support.
10. For USD-M, capture public funding, mark/index basis, open interest and its 1h change, taker
    buy/sell ratio, top-20 bid/ask depth and depth imbalance. Failure is observable but does not
    grant eligibility.
11. Compute 168h BTC beta and return correlation from aligned closed bars. Risk rejects excessive
    individual beta and caps highly BTC-correlated same-direction crypto exposure.
12. Persist every evaluated direction—including rejected rows—with its feature vector, reasons,
    profile and confirmed bar timestamp. The unique hourly identity makes scheduler retries
    idempotent.
13. Return only eligible scores above the configured threshold and separately persist those as
    executable observations with two-bar expiry.
14. Immediately before execution, screen the selected instrument again; never order directly from
    a persisted observation.

The score is an interpretable ranking, not a probability. Every awarded point has a reason stored
with the candidate and signal.

## 5. Portfolio risk policy

### Protective bracket construction

Bracket construction is deterministic and symmetric:

| Component | Long | Short |
|---|---|---|
| ATR stop floor | `entry - 1.5 × ATR` | `entry + 1.5 × ATR` |
| Structural invalidation | below support by `0.25 × ATR` | above resistance by `0.25 × ATR` |
| Structural distance cap | ignore support beyond `3 × ATR` | ignore resistance beyond `3 × ATR` |
| Default target | `entry + 2R` | `entry - 2R` |
| Opposing-level target | before resistance by `0.25 × ATR` | before support by `0.25 × ATR` |
| Minimum payoff | reject below `1.5R` | reject below `1.5R` |

The ATR floor prevents ordinary volatility from invalidating a trade. Nearby structure can widen the
stop only when it remains inside the configured distance cap. The opposing level can shorten the
profit target, but never below the minimum reward/risk threshold. All checks are repeated after
venue tick-size normalization. A candidate that fails bracket construction is recorded as rejected;
the cycle evaluates the next ranked candidate rather than treating the rejection as a scheduler
failure.

Risk is calculated from the stop distance, not an arbitrary percentage allocation, and the
denominator charges the costs that are actually paid when a stop fires:

```text
risk budget = account equity × account risk per trade
loss per unit = abs(worst executable IOC price − stop)
              + entry taker fee + exit taker fee + expected stop slippage
quantity = risk budget ÷ loss per unit
```

Sizing on the bare stop distance understates realized risk: a stop is a trigger, so the exit
prints past it, and both legs pay taker fees. Every trade would then risk more than
`account_risk_per_trade` claims.

Daily limits are denominated on **day-start equity**; only the drawdown gate uses the
all-time peak. Anchoring a daily stop to the peak holds the dollar allowance fixed while
equity falls, so the effective percentage stop widens during a drawdown — the inverse of what
a circuit breaker is for.

Quantity is floored to the venue step size, then checked against minimum quantity, minimum notional,
available balance, gross exposure, daily loss, drawdown, open-position count, and existing symbol
exposure. Futures leverage is capped independently of Binance account settings. Futures sizing uses
the smallest of the stop-risk quantity, one-third-of-equity margin cap, remaining aggregate exposure,
and available margin.

The UTC day-start equity and all-time process peak are persisted across restarts. Realized daily PnL
comes only from durably closed trade records; mark-to-market movement is tracked independently and
has its own circuit breaker. A 2% realized loss or 3% intraday mark-to-market loss halts new entries
by default. Deposits, withdrawals, and exchange exits require authoritative ledger reconciliation
before live use.

All Binance crypto positions share a conservative correlation bucket. At most two positions may be
open in the same direction, even when their symbols differ, so three altcoin longs cannot be treated
as three independent risks.

Binance Spot Test Network seeds accounts with hundreds of virtual balances. Atlas requires an
explicit, persisted testnet inventory baseline and excludes that seed inventory from risk capital
and open-position counts. Only positive balance deltas above the baseline become managed Spot
positions. A balance falling below its captured non-USDT baseline is treated as stale account state
and fails closed until the operator confirms there are no managed positions and explicitly resets
the baseline. This policy is wired only in testnet mode; live Spot continues to value and count every
nonzero holding.

Initial conservative defaults:

- 0.25% equity risk per trade
- 2% daily realized-loss stop
- 3% intraday mark-to-market loss stop
- 5% peak-to-current drawdown stop
- three simultaneous positions
- two same-direction Binance crypto positions
- 1× gross cash exposure
- 3× maximum futures leverage
- one-third of Futures equity maximum margin allocation per position
- 3× Futures equity maximum aggregate Futures notional
- no martingale, averaging down, or recovery sizing
- no Kelly sizing without a stable out-of-sample edge estimate

## 5a. Market structure

Rolling `min(low)`/`max(high)` over a fixed window is a rolling extreme, not market structure.
It cannot distinguish an uptrend making higher lows from a range with the same floor, and the
"resistance" it produces is the level a genuine breakout is standing on top of — so it both
blocks the entry and mislocates the stop.

`domain.structure` derives confirmed swing pivots (`left`/`right` bar fractals), collapses them
to a strictly alternating high/low sequence, and classifies:

| Sequence | Trend |
|---|---|
| higher high **and** higher low | uptrend |
| lower high **and** lower low | downtrend |
| anything else | range |

Structure is used three ways: as a veto (never enter against a *confirmed* opposing structure —
a range is permitted, because after the EMA, ADX, DI and momentum gates, also demanding a
confirmed HH/HL sequence leaves almost no tradable intersection), as the source of support and
resistance for bracket construction, and as the ratchet reference for trailing. Pivots require
`right` bars of forward confirmation, so structure lags by exactly that many closed bars; that
lag is the price of pivots that do not repaint.

Screening gates on `required_opposing_room_atr`, derived from the reward/risk requirement
rather than configured independently:

```text
opposing − entry ≥ (min_reward_risk_multiple × atr_stop_multiple + protective_buffer) × ATR
```

Screening on a looser buffer admits candidates that score, persist, and then fail bracket
construction on minimum reward/risk every single time.

## 5b. Exit management

A static bracket has two outcomes: lose 1R, or win the fixed target. It cannot participate in
the move that pays for the losers, and between entry and target it protects nothing already
earned — an open trade at +40R and one at +0.1R carry the same stop.

| Stage | Trigger | Effect |
|---|---|---|
| `INITIAL` | entry | original stop, full size |
| `PARTIAL_TAKEN` | `+partial_take_profit_r` | sell a fraction, stop → cost-adjusted breakeven |
| `TRAILING` | `+trail_activation_r` | stop trails the last confirmed swing less `trail_buffer_atr` |

Two invariants hold at every stage, enforced in the decision layer rather than the adapters:

1. **The stop only moves toward price.** Every candidate passes `tightens` before it is emitted.
2. **The stop never crosses price.** A stop on the wrong side of the market is an immediate exit.

Breakeven is entry plus the round-trip taker fee, not entry: closing at entry still pays the fee.

Decisions are pure (`application.position_manager`); everything that can fail against an
exchange lives in `application.manage`. Failure posture matches the entry path — a protective
order that cannot be confirmed activates the kill switch, because the position is then live
with unknown protection, while a deferred scale-out leaves the existing stop in force and
retries next cycle.

Management runs on every cycle before new entries are considered: protecting open risk
outranks adding exposure.

## 6. Execution invariants

1. Persist the order intent before contacting a venue.
2. Derive a stable `clientOrderId` from the immutable signal ID.
3. Never automatically retry an order whose acceptance is unknown.
4. Reconcile unknown entries by client order ID.
5. Activate the kill switch when reconciliation is inconclusive.
6. Protect every executed quantity with a stop and target.
7. Submit entries as IOC limits capped by the smaller of 5 bps beyond the observed quote and the
   exact 0.5 ATR signal-close boundary; never chase an unfilled entry with an unbounded market
   order.
8. Persist explicit lifecycle stages: intent persisted, entry filled, stop confirmed, target
   confirmed, and position tracked.
9. Use Spot OCO so the same inventory is not reserved twice.
10. Use Futures hedge-side `closePosition` protection with mark-price triggers.
11. Place the Futures stop before the profit target.
12. Halt new entries if the target fails after the stop is active.
13. Halt new entries when a locally tracked trade is absent at the exchange until the authoritative
   exit fill and fees are reconciled.
14. Reject malformed long or short bracket geometry and payoff below the configured minimum again
    in the portfolio risk boundary.
15. Reject expired signals before account evaluation and again immediately before intent
    persistence.
16. On every cycle, reconcile any durable entry intent without a result. Only a confirmed terminal
    zero-fill result is safe to resume automatically; any fill, partial fill, open status, or
    unknown status activates the kill switch.
17. Compare exchange and local positions in both directions and include quantity in the identity
    check; an orphan exchange position is never treated as harmless inventory.
18. Refuse kill-switch clearing until unresolved intents, incomplete lifecycles, and position
    mismatches are gone.
19. Distinguish a decision not to trade from a safety interlock. An unfilled IOC entry, a risk
    rejection, and an expired signal leave no venue state behind: they raise `TradeRejected`,
    the cycle advances to the next ranked candidate, and no failure is counted. Only conditions
    that leave state unproven raise `SafetyInterlockError`. Conflating the two allowed three
    routine no-fills to activate the durable kill switch.
20. Amend a protective bracket by cancel-then-replace, never by mutating one leg, so there is
    no window in which two stops are live against the same inventory. Client order ids carry a
    deterministic digest of the bracket geometry, so an amended stop gets a fresh id while a
    retry of the same amendment stays idempotent.
21. Preserve the entry stop and entry size for the life of the trade. Every R multiple is
    measured against the risk taken at entry, and once the stop has ratcheted that risk cannot
    be recovered from the live bracket.

## 7. Consistency and recovery

SQLite with WAL and full synchronous writes is sufficient for a single-node testnet deployment. A
durable SQLite lease prevents two local processes sharing the state database from owning the same
trading cycle and expires after five minutes if an owner crashes. Before active/passive production
deployment, implement the same `StateRepository` port with PostgreSQL and move the lease to a
database advisory lock or fencing-token design.

Every SQLite connection has an explicit transaction and close lifetime. Schema version 2 upgrades
existing version-1 volumes, including management/P&L columns and the research registry; startup
refuses a database newer than the running binary. Research opportunities and events have
configurable retention, and feature reports use a bounded lookback. Export research observations
before expiry when a longer analytical history is required.

The current cycle guard compares tracked open trades with exchange positions in both directions and
fails closed on a symbol, direction, or quantity mismatch. It also detects persisted entry intents
without results and execution lifecycles interrupted after a fill or protection step, and allows
automatic continuation only when Binance confirms a terminal zero-fill result. A full recovery
worker must still reconcile:

- exchange open orders absent from local state
- exchange positions without protective orders
- local positions no longer present at the venue
- expired or disconnected user-data streams

Until that recovery worker is implemented and tested, live mode should remain operationally disabled.

## 8. Security model

- Secrets come only from environment/secret storage and use Pydantic `SecretStr`.
- Secrets are never included in doctor output, HTTP responses, events, or logs.
- The SQLite trading-state file is forced to owner read/write permissions (`0600`).
- The operations API uses bearer authentication with constant-time comparison.
- All privileged routes inherit authentication at the router boundary; no arbitrary-order route exists.
- CORS is disabled, Host headers are allowlisted, docs are disabled in production, and security
  headers are set centrally.
- The API binds to localhost unless deployment networking is explicitly configured.
- Production keys must disable withdrawals and use IP restrictions.
- A remotely reachable deployment must terminate TLS and enforce request limits at a trusted reverse
  proxy; the application server does not provide public-edge TLS or distributed rate limiting.

## 9. Reliability roadmap

Required before live capital:

- PostgreSQL repository and leader election
- Binance user-data stream with keepalive and replay/reconciliation
- market WebSocket aggregation with REST snapshots and sequence validation
- IBKR Paper adapter and contract resolver
- distributed scheduler/leader lease with fencing tokens (the single-node SQLite lease is
  implemented)
- metrics, alerting, clock drift, and stale-data monitors
- multi-symbol portfolio backtests, walk-forward splits, and survivorship-safe data (the implemented
  single-symbol replay already shares production decision code, including the exit policy, but
  the cross-sectional ranking that selects *which* instrument to trade remains unvalidated —
  this is the largest open question about whether the system has an edge at all)
- order-state and exchange-outage integration tests
- backup/restore and kill-switch runbooks

## 10. Single-node automation

When explicitly enabled, the scheduler wakes after each configurable aligned UTC boundary plus a
grace period. The default is hourly discovery with an independent rolling eight-hour successful-
entry cooldown. Eligible observations are durable and expire after two bars. A process-local lock
prevents overlapping cycles, and the system opens at most one position per cycle across venues.
Three consecutive scheduler failures activate the durable kill switch by default. This is
appropriate for testnet on one host. The SQLite lease also prevents accidental competing local
workers, but production HA still requires a PostgreSQL-backed lease with fencing so only one
instance owns order authority during network partitions.
