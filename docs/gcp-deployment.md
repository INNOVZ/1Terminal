# Pure-screener deployment and migration

The existing topology remains one GCP VM, one Compose project named alfa-terminal, one API worker,
a persistent SQLite volume, and the Vercel frontend. This migration changes the deployed capability
to public-data screening. It does not require a database reset.

## Before rollout

1. Record the currently running image and Compose project name. Confirm that the production project
   really is alfa-terminal before using the workflow; a different name would select a different
   SQLite volume and could leave the previous container running.
2. Back up SQLite using its backup API or a stopped-container copy including any WAL state.
   Preserve /home/deploy/alfa-terminal/data and existing watchlists.
3. Inspect VM cron, systemd and other containers for old atlas worker/run-once or testnet-trader
   processes. Stop those specific legacy services. Replacing this API cannot stop a separate VM
   process or another deployment.
4. Review any existing exchange orders/positions directly in Binance. Removing code does not cancel
   or close them. Do not blindly cancel protective orders around an open position.
5. Revoke the previously committed testnet credentials and remove exchange keys from VM/GitHub
   environment storage. Removing source literals does not revoke credentials or erase Git history.
   This release deliberately does not rewrite shared Git history.
6. Ensure /home/deploy/alfa-terminal/data is writable by container UID 10001. Existing deployments
   previously granted broad permissions; use explicit owner/group permissions instead.
7. Keep existing frontend API_BACKEND_URL pointing to this backend. No trading endpoint is needed
   by the current frontend.

## What the release changes

- Docker always starts algotrader.api.portal_app:app with one worker.
- The compatibility algotrader.api.app:app path also serves only the screener.
- No exchange credentials are loaded or forwarded by Compose.
- ATLAS_SCREENER_CRON_ENABLED controls scanning only; default true.
- ATLAS_SCREENER_INTERVAL_HOURS defaults to 4 and must divide 24.
- Runtime data is not copied into the image.
- Source bind mounts are removed; the container runs the built application.
- Watchlist volume and data directory are retained.

The workflow runs backend tests, source linting, strict typing, frontend lint/build, and a
network-disabled container smoke test. Deployment waits for these jobs. It creates a detached
release worktree for the exact verified commit instead of resetting the mutable VM checkout.
Images use the commit SHA tag. Compose builds before replacing the service and waits for health.

The data bind mount uses ATLAS_DATA_DIR=/home/deploy/alfa-terminal/data. The named state volume
uses the existing alfa-terminal project prefix. Do not change those during migration.
Release worktrees/images need a later bounded retention policy; never delete the active or
rollback release.

## Verify after rollout

```bash
docker compose --project-name alfa-terminal --file /home/deploy/atlas-releases/COMMIT_SHA/compose.yaml ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8000/
```

Root must report mode=screener and order_execution_enabled=false.
GET /api/testnet/status, GET /api/testnet/watchlist, and former /v1 routes must return 404.
Run one controlled scan through the frontend, verify candles/watchlist persistence and scheduler
status, and watch for provider failures. No Binance account permission or API key should be needed.

The API currently retains public watchlist mutation and scan-refresh behaviour for frontend
compatibility. Public exposure still needs authentication/ownership, TLS and rate limits.
The existing direct port mapping is retained to avoid silently breaking Vercel connectivity.
Use a trusted reverse proxy or restricted ingress as the next operations milestone.

## Failure and rollback

A failed build leaves the running service untouched. A failed Compose health wait fails deployment;
the workflow does not claim success and does not automatically restore an older trading image.
Inspect container logs and readiness. Prefer rolling forward with a corrected screener build.
Once a known-good screener release exists, restore that exact image/worktree with the same state
volume and data directory. Restoring the pre-migration image can re-enable trading.

Local verification cannot establish the VM's current process inventory, exchange state, backups or
firewall configuration. Check these in the deployment environment before treating the migration
as complete.
