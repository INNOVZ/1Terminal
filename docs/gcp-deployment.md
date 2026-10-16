# GCP single-node deployment

This is the supported deployment shape while Atlas uses SQLite: one Compute Engine VM, one worker
process, and one attached Persistent Disk mounted for `/app/state`. It is suitable for paper and
testnet burn-in. It is not highly available and is not approved for live capital.

Cloud Run is intentionally excluded from this phase. Its container filesystem is disposable, and
its instance lifecycle and horizontal scaling conflict with local SQLite plus a continuously running
singleton scheduler. Move durable state and leader fencing to PostgreSQL before reconsidering Cloud
Run or multiple replicas.

## Topology

```text
operator/IAP or TLS proxy
          |
          v
single Compute Engine VM
  Docker Compose: exactly 1 atlas container / 1 uvicorn worker
          |
          v
zonal Persistent Disk mounted by the atlas-state volume
  /app/state/atlas_state.sqlite3 (+ WAL/SHM)
```

## Release flow

1. Build the image from a reviewed source snapshot and tag it with an immutable release identifier,
   not only `latest`.
2. Push it to a private Artifact Registry repository using the Google credential helper or a
   short-lived access token. Avoid downloadable service-account keys.
3. Give the VM service account only Artifact Registry Reader plus the minimum monitoring/secret
   permissions it needs. Binance credentials must not be baked into the image.
4. Attach and mount one zonal Persistent Disk. Keep it single-writer; ordinary filesystems and
   SQLite are not a multi-writer HA design.
5. Put the production/testnet environment file on the VM with owner-only permissions, or materialize
   it at startup from Secret Manager. Verify `ATLAS_ALLOWED_HOSTS`, the 32+ character operator token,
   execution mode, testnet keys, and `/app/state/atlas_state.sqlite3` before starting Compose.
6. Start exactly one Compose project and confirm both `/healthz` and `/readyz`. The Compose health
   check uses `/readyz`; it fails if SQLite is unavailable or automation is enabled but its scheduler
   task stopped.
7. Run `atlas doctor`, reconciliation, and kill-switch drills before enabling automation. Keep
   testnet burn-in for at least 30 days and shadow live data for several weeks before any canary.

## Operations constraints

- Keep the API private where possible (IAP/VPN or a restricted load balancer). If reachable beyond
  localhost, terminate TLS at a trusted proxy, rate-limit it, and never transmit the bearer token on
  plain HTTP.
- Use a reserved egress IP and restrict Binance API keys to it. Disable withdrawals and grant only
  the exact trading permissions required by the selected venue.
- Alert on container unhealthy/restarts, `/readyz` failures, kill-switch activation, scheduler
  failures, reconciliation mismatch, disk usage, clock drift, and stale market data.
- Back up SQLite with a transactionally consistent SQLite backup or a stopped container. Copying
  only the main database while WAL mode is active can omit committed state. Test restoration on a
  separate VM.
- Deploy by image digest, retain the previous digest, and stop the old container before starting the
  new one. Never overlap two workers against the same account, even briefly.
- Do not attach the disk read-write to multiple VMs. HA requires PostgreSQL, a fenced leader lease,
  exchange user-data replay, and tested failover—not a shared SQLite volume.

## Go/no-go checks

- All tests, Ruff, and strict mypy pass for the exact image source.
- Container runs as the non-root `atlas` user with a read-only root filesystem; `/app/state` is the
  only durable writable path.
- State survives a container recreation and VM reboot.
- Unknown-order, scheduler-failure, and position-mismatch drills activate the persistent kill switch.
- Restore drill reproduces order intents, results, open trades, management state, and kill-switch
  state before automation is re-enabled.
