# Alfa Terminal — Atlas Crypto Screener

Atlas is a public-market cryptocurrency screener with charts, setup diagnostics, macro context,
and a persistent watchlist. Binance supplies public Spot and USD-M data. The application has no
exchange account access, API-key loading, order submission, paper broker, or position-management
runtime.

Institutional quality is the engineering and research target. The current scoring model is
uncalibrated research: grades, model EV, and legacy win-rate fields are not validated probabilities
or demonstrated performance.

## Run locally

Requirements: Python 3.12+, uv, and Node.js 22 for the frontend.

```bash
uv sync --frozen --extra dev
uv run atlas doctor
uv run atlas crypto-screen --mode all --max-symbols 250
uv run atlas portal
```

In a separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Copy .env.example if you need configuration overrides. No Binance keys are needed.
The public REST client permits only explicitly listed GET market-data endpoints.
Legacy trading settings in an existing .env file are ignored and cannot enable execution.

## Product and API

- `GET /api/screener/live`: current scan results; supports on-demand refresh.
- `POST /api/screener/rescan`: request a scan.
- `GET /api/chart/{symbol}`: public candles and analytical levels.
- `/api/watchlist`: user-managed research watchlist, including live audits.
- `GET /api/macro` and `GET /api/macro/overextension`: macro context.
- `GET /api/screener/cron/status`: configured schedule and observed scan count.
- `/healthz` and `/readyz`: process and runtime readiness.

Both `algotrader.api.portal_app:app` and the former `algotrader.api.app:app` now serve
the screener. Trading `/v1/*`, `/api/testnet/*`, and trading CLI commands are removed.
The default scan schedule is every four hours, one minute after the UTC candle boundary.
Entry/stop/target fields describe hypothetical research levels; they do not create orders.

The package/import name remains `algotrader` to avoid unnecessary deployment/import churn.
Offline simulations and their pure strategy/risk calculations remain available as research code,
without exchange execution adapters. Their results have not validated this screener.

## Verification

```bash
uv run ruff check src
uv run mypy
uv run pytest
cd frontend
npm run lint
npm run build
```

CI runs these checks and a credential-free container startup test before deployment.
The Docker image excludes runtime data and historical trading artifacts.

## Current priorities

1. Ship the pure-screener migration and verify the existing deployment.
2. Correct data horizons and missing-data semantics; record reproducible snapshots.
3. Consolidate universe selection, acceptance rules, and versioned strategy parameters.
4. Measure forward outcomes and compare simple baselines before calibrating probabilities.
5. Harden access control, freshness, observability, backups, and release operations.
6. Reconsider a separate execution product only after the research supports it.

See [the research and ordered delivery plan](docs/screener-research-roadmap.md),
[current architecture](docs/architecture.md), and
[deployment migration](docs/gcp-deployment.md).
The old [trading architecture](docs/archive/algorithmic-trading-architecture.md) is historical.
[Research baseline](docs/research-baseline.md) records earlier experiments, not evidence that
the current screener has an edge. AGENT.md and previously generated PDFs describe older strategy
versions and must not be treated as proof of production readiness.
