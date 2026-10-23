# Atlas pure-screener architecture

Status: current product boundary. Strategy validation remains incomplete.

## Runtime

```text
Next.js frontend
    -> FastAPI portal: scans, charts, macro context, watchlists
        -> screener runtime
            -> CryptoOpportunityScreener
                -> domain features / gates / ranking / hypothetical levels
            -> public Binance Spot and USD-M adapters
                -> allowlisted unsigned GET transport
            -> public macro providers
        -> SQLite watchlist + persisted scan results
```

The modular monolith remains appropriate for the existing single VM. It avoids additional
network boundaries and fits the current SQLite/watchlist and in-process scheduler design.

The runtime owns public market-data and macro HTTP clients. Shutdown cancels scans and cron
before closing shared clients. Startup does not contact exchange account endpoints.
The portal scheduler performs scans only. There is no broker, execution coordinator, trading
scheduler, credential signer, account probe, reconciliation service, or order endpoint.

## Enforced boundary

The Binance transport exposes only GET requests to an exact endpoint allowlist. It does not
accept credentials or signing arguments, attaches no API key, blocks authenticated parameters,
and does not follow redirects. Spot and USD-M adapters expose only market-data methods.
Configuration cannot re-enable trading; obsolete deployment variables are ignored.

Both historical ASGI paths resolve to the same screener application. The Docker command fixes the
screener entry point; an old ATLAS_APP_MODULE override cannot restore trading. Compose explicitly
forwards screener settings rather than importing the deployed environment file into the container.

Tests cover removed routes/commands, rejected account/order calls before transport, unsigned market
requests, readiness, cleanup and legacy environment compatibility.

## Research output contract

Prices, stops and targets are analytical scenarios. No account sizing or exchange submission follows.
For compatibility, JSON still contains fields named execution_mode, estimated_win_rate_pct and
expected_value_r. execution_mode is a setup entry condition; it does not imply automation.
Responses mark model outputs as uncalibrated_research and order_execution_enabled=false.
The frontend no longer labels assumed scores as empirical win rates.

Scan counts report completed per-symbol evaluations, selected symbols and failed evaluations.
They are not a claim that every input passed a freshness/completeness audit. That audit is still
required. Readiness measures process/runtime health, not the validity or profitability of signals.

## Retained offline code

Pure strategy, risk and position simulation calculations, historical domain records, SQLite research
storage and backtests remain for reproducible research. They have no network order capability.
Production code no longer imports the old TradingSystem facade. Future trading should be a separate
application and deployment with its own explicit authority and independently tested controls.

Removed implementations can be recovered from Git history; the historical architecture is archived
under docs/archive/algorithmic-trading-architecture.md.

## Remaining architectural work

- One shared acceptance/ranking policy for API, CLI, scheduled scans and replay.
- A versioned strategy contract replacing divergent AGENT.md, PDFs and source constants.
- Explicit data timestamps, availability, units, validity and source provenance.
- Immutable scan/feature snapshots and a forward-outcome observation store.
- Authentication and ownership for watchlist mutations; bounded/rate-limited scans.
- Separate readiness from data freshness, partial-provider failure and scan health.
- Application ports for persistence and macro data; some existing application services still
  access SQLite/providers directly.

See screener-research-roadmap.md for evidence, acceptance criteria and order of work.
