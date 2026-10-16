# Security and robustness review — layered_tsmom_v6

Date: 2026-08-12

## Executive summary

No critical, high, or medium security finding was identified in the v6 implementation or its
execution integration. Bandit completed without a source finding, `pip-audit` reported no known
dependency vulnerability, the full test suite passed, Ruff and strict MyPy passed, and both source
and wheel packages built successfully.

The new profile remains fail-safe: it is rejected in live mode, its short sleeve is disabled by
default, candidates cannot cross strategy-profile authority boundaries, missing BTC exposure data
rejects risk approval, and bracket/exposure/loss interlocks are unchanged. Production configuration
now also rejects a wildcard trusted-host policy.

This review does not promote the strategy. BTC and SOL doubled-cost replays are negative, so v6 is
restricted to paper/testnet research regardless of its software-security posture.

## Scope and evidence

- Strategy/configuration: `src/algotrader/config.py`, `application/alpha.py`,
  `application/screener.py`, `application/strategy.py`, `application/risk.py`.
- Research persistence/reporting: `application/research.py`, `domain/models.py`, SQLite repository.
- Operational boundary: FastAPI operator API, Binance HTTP client, Dockerfile, `compose.yaml`,
  dependency manifests and lock files.
- Verification: full pytest suite, Ruff, strict MyPy, package build, Bandit, `pip-audit`, and manual
  review for authentication, host validation, injection, SSRF, secret exposure, unsafe execution,
  input validation, production docs/debug exposure, and container privilege.

## Controls verified

- V6 is a distinct research profile and production live-mode validation rejects every non-legacy
  profile (`config.py:26-56`, `config.py:304-315`).
- Short authority is explicit and off by default (`config.py:157-162`, `config.py:318-332`).
- Long and short use one sign transform and one bounded momentum formula
  (`application/alpha.py:63-86`).
- EMA/MACD/RSI/ADX conflicts affect bounded priority but cannot bypass or add hard eligibility
  (`application/alpha.py:589-655`). Relative volume, volatility, and structure are also bounded
  ranking inputs (`application/screener.py:533-572`).
- The screener uses fully closed 4h decisions, minimum history, global liquidity/spread limits, and
  an ATR anti-chase boundary; Futures alone can emit shorts.
- Research profiles fail closed when BTC beta/correlation is unavailable and preserve portfolio
  loss, drawdown, position, correlation, bracket, and reward/risk controls
  (`application/risk.py:67-138`, `application/risk.py:140-175`).
- Privileged API routes require a minimum-length bearer token compared in constant time; production
  disables schema/docs, uses trusted-host middleware, strict request models, and security headers
  (`api/app.py:45-105`).
- The container is non-root, read-only, loopback-published, capability-free, and protected with
  `no-new-privileges` (`Dockerfile`, `compose.yaml:1-30`).
- SQLite values are parameterized. The only formatted schema statements use closed, code-owned
  migration constants rather than request or market data.
- Outbound HTTP destinations and paths are adapter-owned Binance constants; request timeouts are
  set, and ambiguous order-submission failures are not retried.

## Residual findings

### [Low] SEC-001 — no application-level authentication rate limit

The bearer-token dependency rejects invalid credentials but does not throttle repeated failures
(`src/algotrader/api/app.py:90-100`). Exploitation requires the operations API to be exposed beyond
its loopback binding or an attacker to reach the trusted reverse proxy. A random 32+ character token
and `127.0.0.1` Compose binding make online guessing impractical in the current topology, but the
control should move to the GCP edge before any public exposure. Apply Cloud Armor/API-gateway rate
limits, preserve the real client IP only from a trusted proxy, alert on repeated 401s, and never
publish port 8000 directly.

### [Low] SEC-002 — deployment secrets are sourced from a local `.env` file

`compose.yaml:4` loads exchange keys and the operator token from `.env`. This is reasonable for a
single-host testnet deployment if permissions and backups are controlled, but host, process, or
backup access can expose environment secrets. For GCP, inject secrets from Secret Manager with a
least-privilege service account, keep the file out of images/source control, restrict API keys by IP
and permission, disable withdrawals, and rotate any credential that has appeared in logs or shell
history.

### [Informational] SEC-003 — TLS and HSTS belong at the deployment edge

The service intentionally publishes only on loopback and therefore does not terminate TLS or emit
HSTS. If a GCP load balancer or reverse proxy exposes the API, terminate TLS there, redirect HTTP,
set HSTS at that edge, and restrict backend ingress to the proxy. Do not weaken `allowed_hosts`;
production now refuses `*`.

## Robustness conclusion

The implementation is robust enough for controlled paper/testnet experiments. It is not approved
for live capital because the empirical promotion gate fails, not because of a software control
failure. The next evidence milestone is a chronological multi-symbol portfolio replay plus matured
forward information coefficients for `directional_momentum_score`, split by long and short, under
doubled costs and untouched holdouts.
