# Atlas security and trading-safety review — 2026-08-11

## Executive summary

Atlas remains well structured for local paper trading and a supervised, single-node GCP testnet
deployment. No critical application vulnerability, secret disclosure, injection path, permissive
CORS configuration, unprotected privileged route, unsafe order retry, or known vulnerable locked
dependency was found. The public readiness response was tightened during this review so it no
longer exposes component or exception details.

Atlas is **not secure enough for live capital** because continuous exchange-order/protection
reconciliation and externally verifiable least-privilege credentials are still missing. It also has
no validated positive-expectancy strategy: the legacy, regime-breakout, and indicator-quality
profiles are negative across their recorded two-year diagnostics. The correct approval is paper or
supervised testnet only, with automation disabled by default.

## Scope and verification

- Audited the Python/FastAPI API, configuration, Binance client, execution/reconciliation paths,
  SQLite state, Docker/Compose configuration, GCP deployment guide, and the new alpha policy.
- Searched reachable source for debug/reload modes, permissive CORS, cookie/session auth, unsafe
  body parsing, file serving/uploads, command execution, dynamic evaluation, open redirects,
  WebSockets, user-controlled outbound URLs, unparameterized SQL, and secret logging.
- Verified every `/v1/*` route inherits bearer authentication at the router boundary; `/healthz`
  and `/readyz` are the only intentionally public routes.
- Ran Ruff, strict MyPy, and the complete pytest suite.
- Ran `pip-audit` against `requirements.lock` on 2026-08-11: **no known vulnerabilities found**.
- Verified the local `.env` permission mode without reading its contents: owner read/write only.
- Docker execution itself was not tested; the image and deployment configuration were reviewed
  statically.

## Positive controls

- Alpha evaluation is pure and has no account, persistence, or order authority
  (`src/algotrader/application/alpha.py:1-5`).
- Candidate strategy profiles are checked again before signal construction, preventing a candidate
  generated under one policy from executing under another (`src/algotrader/application/strategy.py:16-23`).
- Automation, live trading, and live acknowledgement are all explicit configuration gates; paper
  remains the runtime default (`src/algotrader/config.py:50-65`).
- Privileged API routes use a router-level dependency with constant-time bearer comparison; debug
  is off, production docs are disabled, Host headers are allowlisted, CORS is absent, and security
  headers are centralized (`src/algotrader/api/app.py:45-105`).
- Public readiness now returns only `ready`/`not_ready`; failure details stay in structured logs
  (`src/algotrader/api/app.py:119-127`, `src/algotrader/infrastructure/runtime.py:64-77`).
- Binance endpoints are code-controlled HTTPS URLs. Signed calls use HMAC-SHA256, bounded timeout,
  receive window and clock synchronization. Unknown order-submission outcomes are not retried
  (`src/algotrader/adapters/binance/client.py:26-140`).
- SQL values are parameterized; SQLite uses WAL and `synchronous=FULL`, and database/WAL/SHM files
  are forced to `0600` (`src/algotrader/infrastructure/sqlite_state.py:43-71`).
- The container runs as a non-root user with a read-only root filesystem, drops all capabilities,
  adds `no-new-privileges`, and publishes only to host localhost in Compose (`Dockerfile:18-20`,
  `compose.yaml:5-17`).

## Open findings

### TRD-001

- **Rule ID:** ATLAS-TRADING-EDGE-001
- **Severity:** High
- **Location:** `docs/research-baseline.md:1-32`; `src/algotrader/config.py:50-65`
- **Evidence:** The legacy profile records 221 trades at a 43.4% hit rate with negative summed
  diagnostic P&L. The new long breakout sleeve records 140 trades at 44.3% with approximately
  -$59.68 across two non-overlapping years. The 2-of-4 EMA/MACD/RSI/ADX quality experiment records
  138 trades at 43.5% and approximately -$61.63. Automation is disabled by default, but any profile
  can be selected for research.
- **Impact:** Enabling unattended or live execution would automate a strategy that has not
  demonstrated positive net expectancy after modeled costs.
- **Fix:** Complete the chronological shared-capital multi-symbol replay and delisted-symbol
  point-in-time universe. Hourly rejected-opportunity capture, public funding/basis/OI/order-flow
  features, BTC exposure limits, doubled-cost stress and chronological holdout gates are now
  implemented, but have not accumulated enough forward live labels. Promote a profile only after
  the documented expectancy, profit-factor, drawdown, parameter-stability and cost-stress gates pass.
- **Mitigation:** Keep `ATLAS_AUTOMATION_ENABLED=false`, use paper/testnet only, and retain every
  rejected hypothesis in the research registry.
- **False-positive notes:** Single-symbol summed P&L is not a portfolio return; a true portfolio
  replay could differ. That uncertainty is a reason to withhold approval, not evidence of edge.

### SEC-001

- **Rule ID:** ATLAS-EXECUTION-RECONCILIATION-001
- **Severity:** High
- **Location:** `src/algotrader/application/service.py:443-497`; `docs/architecture.md:318-329`
- **Evidence:** Atlas manages locally tracked positions and performs fresh entry revalidation, but
  it does not continuously consume/replay the Binance user-data stream or prove on every cycle that
  every exchange position retains the intended protective order geometry and quantity.
- **Impact:** A manual exchange action, canceled protection, missed fill, stream gap, or account
  reset could leave exposure whose protection cannot be proven.
- **Fix:** Add startup and recurring REST snapshot reconciliation plus sequence-aware user-data
  replay. Verify every position and open order in both directions, fail closed on orphans or stale
  state, and test crashes at every execution lifecycle boundary.
- **Mitigation:** Exchange-side protection, stop-first placement, durable lifecycle stages,
  bidirectional position checks and the kill switch reduce—but do not eliminate—the risk.
- **False-positive notes:** Exchange-side brackets survive process failure but do not prove that an
  order remained active or still matches the intended position.

### SEC-002

- **Rule ID:** ATLAS-SECRETS-LEAST-PRIVILEGE-001
- **Severity:** High
- **Location:** `src/algotrader/infrastructure/runtime.py:113-152`; GCP/Binance account configuration
- **Evidence:** Startup proves only that signed endpoints accept the supplied keys. Repository code
  cannot verify that withdrawals are disabled, IP restrictions exist, or a limited-balance
  subaccount is being used.
- **Impact:** Theft of an over-privileged credential could permit unauthorized trading and, if
  withdrawals were enabled, direct asset loss.
- **Fix:** Store deployment secrets in GCP Secret Manager, restrict keys to the reserved egress IP,
  disable withdrawals, grant only required trading permissions, isolate a limited-balance account,
  rotate credentials, and record the operational verification.
- **Mitigation:** Secrets use `SecretStr`, are excluded from source/container context, are never
  persisted in SQLite, and no withdrawal endpoint exists in Atlas.
- **False-positive notes:** The current keys may already be restricted; this cannot safely be
  inferred or probed from application code.

### SEC-003

- **Rule ID:** FASTAPI-PROXY-001 / FASTAPI-LIMITS-001
- **Severity:** Medium
- **Location:** `Dockerfile:20`; `compose.yaml:5-6`; `docs/gcp-deployment.md:45-53`
- **Evidence:** Uvicorn listens on `0.0.0.0` inside the container. Compose publishes it only to
  localhost, but the application does not terminate TLS or provide distributed rate/body limits.
- **Impact:** Publishing port 8000 directly could expose the bearer token over plaintext and permit
  resource abuse of authenticated screening endpoints.
- **Fix:** Keep the service private behind IAP/VPN or a trusted TLS gateway with network ACLs,
  request/body/concurrency limits and access logs. Trust forwarded headers only from that proxy.
- **Mitigation:** Localhost-only Compose publication, strict Host allowlist, bearer auth, no CORS,
  production docs disabled, and no arbitrary-order API.
- **False-positive notes:** This is adequately mitigated only while the GCP firewall and port
  publication preserve the documented private topology.

### SEC-004

- **Rule ID:** ATLAS-SINGLE-WRITER-LEASE-001
- **Severity:** Medium
- **Location:** `src/algotrader/infrastructure/sqlite_state.py:235-278`; `Dockerfile:20`
- **Evidence:** The SQLite lease is atomic but has no renewable fencing token. A process paused past
  expiry cannot be technically fenced after a second process acquires authority.
- **Impact:** In an unsupported multi-worker/multi-host deployment, two processes could briefly
  believe they can submit orders.
- **Fix:** Before HA/live use, move state to PostgreSQL and use advisory locking or a renewable lease
  with a monotonic fencing token checked when reserving each order intent.
- **Mitigation:** The supported topology is exactly one VM, one container and one Uvicorn worker;
  client order IDs and persisted intents are deterministic.
- **False-positive notes:** Risk is low for the documented single-node testnet deployment and high
  if operators add replicas.

### SEC-005

- **Rule ID:** FASTAPI-SUPPLY-001
- **Severity:** Medium
- **Location:** `pyproject.toml`; `requirements.lock`; no CI workflow present
- **Evidence:** The current hash-pinned dependency set passed `pip-audit`, but vulnerability
  auditing and lock verification are manual.
- **Impact:** A newly disclosed FastAPI/Starlette/httpx/Uvicorn vulnerability could remain deployed
  until the next manual review.
- **Fix:** Add CI for lock verification, `pip-audit`, Ruff, strict MyPy, tests, container build and
  image scanning; use reviewed dependency-update pull requests.
- **Mitigation:** Runtime dependencies are exact-version and hash pinned in `requirements.lock`.
- **False-positive notes:** The dependency set was clean on the review date; this finding concerns
  detection latency for future advisories.

### SEC-006

- **Rule ID:** ATLAS-STATE-CONFIDENTIALITY-001
- **Severity:** Low
- **Location:** `src/algotrader/infrastructure/sqlite_state.py:69-135`
- **Evidence:** Signals, order results, positions, P&L and events are stored as plaintext
  rows/JSON. API credentials are not stored there, and local file permissions are `0600`.
- **Impact:** A copied disk or unprotected backup reveals trading activity and permits offline
  tampering.
- **Fix:** Use encrypted GCP Persistent Disk, encrypted authenticated backups, least-privilege
  backup access, restore drills and integrity monitoring.
- **Mitigation:** Owner-only state files and no stored API secrets.
- **False-positive notes:** Application-layer encryption adds limited protection after full host
  compromise; infrastructure encryption and access control are the primary controls.

## Release decision

- **Local paper:** approved.
- **Single-node GCP paper/testnet:** approved with operator supervision, private API networking and
  recovery/kill-switch drills.
- **Automated strategy promotion:** not approved; TRD-001 remains open.
- **Any live capital:** not approved until SEC-001 and SEC-002 are closed and a strategy passes the
  documented out-of-sample portfolio gates.
- **HA or multiple replicas:** not approved until SEC-004 is closed.
