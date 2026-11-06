# Dedicated research VM deployment — 2026-10-26

## Result and scope

Jithin explicitly approved a local immutable release followed by direct IAP
deployment, postponing GitHub publication and production rollout until after
prospective observation. The release is installed **with collection disabled**.
No production API/Vercel changes, GitHub pushes, project IAM changes, trading
credentials, automatic enrollment or market-data collection were performed.

- Project / zone: `alfa2-512717` / `europe-west10-c`.
- Instance: `instance-20261025-174348`, numeric ID `6237690318220919058`.
- Local release branch: `codex/research-vm-direct-release` in the existing isolated
  worktree; commit `d311d68d1bfa3142d5cef4c8defbac5ea8cd2b1f`.
- Main checkout remains uncommitted at its original HEAD; its unrelated runtime
  data, PDFs, generator edits and embedded frontend repository were preserved.
- Guest release: `/home/deploy/atlas-releases/d311d68d1bfa3142d5cef4c8defbac5ea8cd2b1f`.
- Guest receipt: `installation-receipt.json` inside that release, also retained
  locally in the ignored runner checkpoint directory.
- Root-only recovery artifacts: `/home/deploy/atlas-release-artifacts/d311d68d1bfa3142d5cef4c8defbac5ea8cd2b1f`.

This VM is not an API replacement and does not have a new dashboard URL. Installation
does not start the 24h/48h observation clock or prove predictive performance.

## Identity and verification

All 118 reviewed manifest hashes matched before staging. Only reviewed paths were
staged: 111 changed files; no excluded data/control/secrets/PDF files. The isolated
checkout is clean after the local release commit. No remote publication occurred.

Fresh verification in this deployment session:

- Full backend suite: **650 passed**, one inherited Starlette deprecation warning,
  119.96 seconds. This is not a fresh frontend build/audit claim; those were part of
  the prior verified release checkpoint. No frontend is deployed here.
- Exact AMD64 image passed the local network-disabled packaging/restart smoke.
- Source archive: 176 committed files, each compared against the release commit.
- Guest image `/app/src` and installed Python package: all 86 Python source files
  matched committed source byte-for-byte.
- Guest offline doctor: screener; execution=false; credentials_required=false.
  The unconfigured offline probe reports environment=development, not production.
- Native AMD64 synthetic tests passed: stopped rank worker survives restart;
  pending outcome worker stays healthy/idle. Both used network=none, UID10001,
  read-only roots, no ports, no-new-privileges, all capabilities dropped,
  256MiB and 0.5 CPU. These are not live provider/load or 24h/48h observations.
- Final audit: all 176 installed file hashes match the generated receipt; release
  is root-owned/non-writable; real archive empty; zero containers remaining;
  synthetic volumes removed; no API8000 or Docker2375/2376 TCP listener.
- Wrapper health: `healthy=true`, `enabled=false`, `services=[]`.

Exact artifact SHA256s:

| Artifact | SHA256 |
| --- | --- |
| Source tar.gz | `361c8d09ae1026a57609784e5c03d00c1e19dcc5b6b3aa454b40d77ee0aa699e` |
| Image tar.gz | `f4488ad8f34751ea00b03fb7ab2ea80206374d05b2e408003d14c43f1193d6fb` |
| Verified image configuration | `f30cd08fc74356533df9a44ed8d6b8ecb6c937fdadcec0d206117496ec2e296d` |
| Imported OCI manifest | `d87a56a2ee7fbabfdbb4a9a4e5131d10eb9a04e5299caaa14f752db1ba62f60d` |

## Guest setup and security controls

Docker Engine29.9.2, Compose5.6.1, containerd2.4.2 and Buildx0.38.1 were installed
from Docker's signed Debian13 repository, following the
[official Debian installation guidance](https://docs.docker.com/engine/install/debian/).
The signing-key fingerprint was checked before repository installation:
`9DC858229FC7DD38854AE2D88D81803C0EBFCD88`.
No Docker group access was granted to application/users. The default Docker
context was verified as this guest's Unix socket, not a remote daemon.

Release paths are root-owned, directories0555/files0444. The persistent control
`/home/deploy/alfa-terminal/research-control.env` is root-owned0600 and contains
only `ATLAS_RESEARCH_ENABLED=false`. The archive at
`/home/deploy/alfa-terminal/data/research` is UID/GID10001 and0700; a bounded,
network-disabled container confirmed write permission without creating evidence.
No worker receives the control file, Docker socket, application state or keys.
Only the existing wrapper's isolated `atlas-research` project and explicit worker
service names were used; no default API service was started on this VM.

Point-in-time post-check capacity: about25GB free disk,1235MiB available RAM,
no swap. Docker is enabled at boot. This is not sustained acquisition capacity.
The existing IAP-only ingress policy, OS Login and strict server host pin were
preserved. The exact temporary deployment key was successfully removed from OS
Login after final acceptance; its generated local private/public key files were
deleted. The server host pin was retained. Only deployment-created guest staging
files and synthetic containers/volumes were removed; the verified release,
root-only recovery artifacts and real research archive were retained.

## Investigated deployment issues

1. The initial image check stopped safely because local classic Docker exposes
   the configuration digest as image ID, while the guest's fresh Docker29
   containerd store exposes the OCI manifest digest. Independent archive inspection
   verified manifest SHA, its reference to the original tested configuration SHA,
   and all runtime layer identities. The one-off installer was corrected to
   validate that chain, not to accept an arbitrary replacement image ID. Existing
   extracted files were checksum-compared and reused without overwriting them.
   Docker documents the [new store default](https://docs.docker.com/engine/storage/containerd/).
2. The first native synthetic smoke failed with `ArchiveIntegrityError`. Local
   reproduction traced it to macOS AppleDouble `._` entries in the fixture tar,
   which correctly violated journal-role integrity. Repackaging with
   `COPYFILE_DISABLE=1` produced a verified metadata-free fixture; local archive
   status and native worker tests then passed. No application validator, archive
   schema, source fingerprint, strategy or release-image bytes were changed.

## Activation gates and recovery

Collection is deliberately **not active** and there is no real session yet.
Before enrolling a fresh compatible parent/child and starting observation:

- Resolve worker access to instance metadata/attached default service-account
  credentials. Do not detach the service account, change project IAM or assume
  no cloud permissions without a separate scoped decision and verification.
- Agree and verify persistent-evidence backup/restore and retention expectations.
  Boot-disk auto-delete remains enabled; retained artifacts on the same disk are
  release recovery material, not an independent backup of future observations.
- Secure Boot remains disabled; vTPM/integrity monitoring remain enabled. No VM
  stop/restart or silent setting change was performed.
- Obtain explicit cohort capture/enrollment/activation approval, then check actual
  snapshots/cadence/checksums, bounded attempts, disk growth and provider/VM load.
  A heartbeat or offline synthetic test is not successful live collection.

There is no prior release to roll back to on this fresh guest. Keep the control
false and use the wrapper's stop action if deployment acceptance regresses;
retain source/image/receipt and do not delete future research evidence. Replaying
archives requires compatible source/dependencies, not just a Git revision.
Do not run the one-off installer over an already accepted release; its empty-archive
and exact-content recovery guards intentionally refuse altered or active state.

This is verified disabled installation, not a zero-vulnerability claim or proof
of institutional readiness. GitHub publication, production rollout and empirical
performance evaluation remain separate milestones.
