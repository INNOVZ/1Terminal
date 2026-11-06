# Research VM security preflight — 2026-10-25

## Scope and result

Read-only GCP control-plane and temporary IAP tunnel checks for project
`alfa2-512717`, VM `instance-20261025-174348`, zone `europe-west10-c`.
No cloud resource, SSH key, IAM binding or guest configuration was changed.
No application was deployed or research session activated.

The VM is running Debian 13 on e2-small with a 30 GB boot disk. Its boot disk
currently has auto-delete enabled. A temporary loopback-only IAP tunnel to TCP 22
passed GCP's connection test and was closed. This verifies tunnel access and TCP
reachability, not successful authenticated guest SSH access or host identity.

## Findings requiring a decision before deployment

### VM-01 — High: unrestricted SSH network ingress

The effective NIC firewall includes `default-allow-ssh`, priority 65534,
allowing TCP 22 from `0.0.0.0/0`; the VM has an external IP. This unnecessarily
exposes SSH to internet-wide connection attempts. It does not prove weak SSH
authentication or compromise.

Recommended: a VM-specific tag and higher-priority IAP-only TCP 22 allow
(`35.235.240.0/20`), followed by a lower-precedence VM-specific ingress deny.
Validate IAP and authenticated SSH before tightening access. Do not modify the
shared default rules, which might affect other instances.

### VM-02 — Medium: unnecessary RDP and broad internal ingress

Effective rules also allow TCP 3389 from anywhere and all TCP/UDP plus ICMP from
`10.128.0.0/9`. Guest services actually listening on these ports have not been
inspected. The proposed research-only VM needs no inbound application ports.
The VM-scoped ingress policy above can remove this unnecessary access without
changing shared network defaults.

### VM-03 — Medium: centralized SSH controls not configured in metadata

Neither instance nor project metadata enables OS Login or blocking project SSH
keys. Recommended: enable OS Login on this VM and verify the intended operator's
login and administrative access before changing other access paths. No SSH key
has been generated, imported or published by this preflight.

### VM-04 — Hardening and data-lifecycle decisions

- vTPM and integrity monitoring are enabled; Secure Boot is disabled. Assess
  compatibility and obtain approval for any stop/restart required to enable it.
- The default Compute Engine service account is attached with limited OAuth
  scopes. The direct project IAM binding query found no matching roles; this
  does not audit inherited, group, resource-level or future grants. Public-market
  workers should not need broad cloud permissions or exchange credentials.
- Boot-disk auto-delete is enabled. Research storage persistence, ownership,
  capacity and recovery/backup expectations must be decided before collection.
  No evidence currently establishes a backup or restore guarantee.

## Outstanding verification

Authenticated SSH with host-key verification; guest users/sudo/SSH settings;
listening sockets; patch state; container isolation; disk headroom; persistent
archive permissions; restart behavior and measured workload capacity. The runner
implementation and independent review remain incomplete. No production readiness
or zero-vulnerability claim is made.

## Primary guidance

- https://docs.cloud.google.com/iap/docs/using-tcp-forwarding
- https://docs.cloud.google.com/compute/docs/connect/ssh-best-practices/network-access

## Approved VM-scoped hardening — verified 2026-10-26

Jithin explicitly approved hardening only the named VM, without changing other
projects, instances or access policies. The following is a subsequent change
record; the read-only results above describe the original preflight state.

Exact instance ID: `6237690318220919058`. No restart, deployment, package install,
service-account change, project metadata change or IAM grant was performed.

### Applied controls

- Instance metadata: `enable-oslogin=TRUE`, `block-project-ssh-keys=TRUE`.
  Existing `enable-osconfig=TRUE` was preserved.
- Unique instance tag: `atlas-research-6237690318220919058`.
  A post-change tag query returned only `instance-20261025-174348` in
  `europe-west10-c`.
- New rule `atlas-research-20261025-iap-ssh`: priority 900, ingress TCP 22
  allowed only from `35.235.240.0/20`, targeting that unique tag.
- New rule `atlas-research-20261025-deny-ingress`: priority 1000, all other
  IPv4 ingress denied, targeting the same tag. This VM currently has an IPv4-only
  network interface. An IPv6 configuration change would require a separate review.
- The effective NIC policy shows both rules ahead of the unchanged shared
  defaults at priority 65534. Outbound rules and the external IP were not changed
  by this hardening. The external IP is retained for outbound connectivity.

### Verification evidence

- OS Login and `sudo -n true` succeeded through an authenticated IAP tunnel.
- The initial host key was pinned through IAP (trust on first use, not an
  independently attested host key). Its fingerprint matched the observation
  before interruption. Subsequent pre- and post-firewall logins succeeded with
  `--strict-host-key-checking=yes`, without agent forwarding.
- Observed ED25519 host fingerprint:
  `SHA256:2MCYFiAhkUXAR/hCio0URgD9dFifpCv+2Jz524QrYGY`.
- A fresh direct connection to the VM's then-current public IPv4 TCP 22 timed
  out after the lockdown. The effective firewall also denies non-IAP RDP and
  internal inbound connections; it does not depend on stopping guest services.
- `sshd -T`: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`, `pubkeyauthentication yes`, `usepam yes`.
- Guest socket inspection found SSH plus OS resolver, local mail and Google
  Ops Agent listeners. No research/API application listener was present. The
  non-SSH listeners are not allowed inbound by the new policy.
- Before deployment: about 1493 MiB available memory, no swap, and about 26 GB
  available on the boot filesystem. This is a point-in-time idle measurement,
  not a workload capacity validation.

The temporary SSH key was generated solely for these checks with a ten-minute
OS Login expiry. Its exact public key was explicitly removed from OS Login
after verification (successful command exit). The generated local private/public
key files were deleted; no pre-existing user key was replaced or removed.
The pinned server host key is retained for strict checking on future connections.

Secure Boot remains disabled as previously agreed; vTPM/integrity monitoring
were not changed. Boot-disk auto-delete, research archive persistence/backup,
patch-state audit, container isolation and workload verification remain open.
The research runner is still not deployed or activated. VM-01/02/03 are addressed
by the tested VM-specific controls; this is not a full security certification.

## Refreshed deployment-route preflight — 2026-10-26

After Jithin approved preparing the research-only route, control-plane reads again
confirmed the exact instance ID above, RUNNING/e2-small, Secure Boot disabled,
the attached default Compute Engine service account and boot-disk auto-delete.
No IAM, disk, firewall, service-account or instance setting was changed.

Two strict-host-key IAP SSH inspections succeeded using a new ten-minute OS Login
key, without agent forwarding. Guest observations:

- Debian 13.7, x86_64; about 26 GB free disk and 1,475 MiB available memory.
- Docker and Git were not installed; no `deploy` user was found. The expected
  `/home/deploy/alfa-terminal` deployment path and research control file were absent.
- No Atlas systemd units or Compose files under the inspected `/opt`, `/srv` and
  `/home` directories (maximum depth three) were found. This is a bounded inventory,
  not proof that no unrelated application or credential exists anywhere on disk.
- Listening services were SSH, resolver, local mail and Google Ops Agent. No Atlas
  application listener was observed.
- The existing APT index was dated 2026-10-26; `apt list --upgradable` listed no
  packages and no reboot-required marker existed. No index refresh, upgrade or
  vulnerability certification was performed. The queried `docker-compose-v2`
  package had no candidate in that index; do not assume Compose is available.

The exact temporary public key was removed from OS Login successfully, then only
its generated local private/public key files were deleted. The server host pin
was retained. No packages, containers, application files or services were installed.
Fresh local deployment-control regression tests passed (15 tests).

### Proposed first rollout route — approval pending

Use a reviewed local release commit and checksum-verified artifact transfer over
IAP to this VM, rather than pushing/merging the existing GitHub main deployment.
This deliberately separates the first VM rollout from GitHub publication: main
targets the older API VM, and feature-branch Vercel effects are not yet ruled out.
Do not introduce new CI identities, reuse old API deployment secrets or change
existing workflows/routing as a shortcut.

Before executing this route:

1. Approve the publication-order change (local immutable release first, GitHub
   publication later). Follow release instructions, stage only reviewed manifest
   paths and inspect the actual staged diff before creating the local commit.
2. Reconfirm the exact VM identity; bootstrap Docker/Compose using a verified
   package source. Recheck versions, local Docker context and daemon isolation.
   Never give worker containers the Docker socket or cloud/exchange credentials.
3. Resolve workload access to the attached service account/metadata endpoint,
   persistent archive ownership (UID10001), storage lifecycle and recovery policy.
   Disabling auto-delete is not itself a backup. Do not silently detach the
   service account or stop the VM for Secure Boot.
4. Transfer only reviewed source/image artifacts; verify checksums at destination,
   verify checkout/content against the exact release SHA and use its full-SHA
   release directory. The existing wrapper validates the directory name, not the
   Git contents; this operator check is mandatory.
5. Keep control absent or explicitly false. Use only the existing wrapper's
   `atlas-research` project and explicit worker services. Never invoke bare
   `docker compose up` because the default service is the API.
6. Verify image identity, offline doctor, mount permissions and disabled-worker
   state on the guest. No production API listener is expected on this VM.
   A stopped-worker check is not evidence of collection or sustained capacity.

Candidate capture/enrollment and enabling either worker remain a separate approval
after disabled-install verification and operational gates. No API/Vercel parity
or live deployment claim follows from this VM-only preflight.
