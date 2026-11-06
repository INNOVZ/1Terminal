# Research runner release review — 2026-10-26

Status: local implementation verification; no publication, deployment or collection activation.
The pure screener remains separate from public-data research; no execution/account credentials.

Local integration is complete and uncommitted. All 43 integrated files were byte-compared against
the reviewed worktree. Combined-checkout verification passed 650 tests (125.17s), strict typing
(88 files), source lint, frontend clean install/audit/lint/build and offline CLI doctor/help.
Frontend retains 11 inherited lint warnings and an ESLint support warning. Unscoped whitespace
checking still flags only the untouched PDF generator at lines 1047/1056; the explicit release
paths pass. Excluded runtime JSON, PDFs and PDF generator were unchanged across integration.

Publication preflight confirms default branch `main`, a detached implementation worktree and the
unchanged API deploy trigger. The base GitHub commit also carries a successful Vercel status;
do not assume a push has no frontend side effects. A research-only release route must be settled
before triggering the out-of-scope API deployment or changing live frontend deployment/routing.

## Independent review

One fresh independent reviewer inspected the uncommitted runner and its full release prerequisites,
then resumed the same review after a rate-limit interruption. Four Important findings were accepted;
no Critical or Minor findings were reported. The reviewer reproduced these issues independently,
but did not independently repeat the author's complete release suite.

1. **Unbounded retained report evidence.** Eager snapshot/timeline assembly exceeded the 256 MiB
   worker cap in a 1,000-symbol macOS RSS experiment before a complete horizon was retained.
   Fix: sort compact timestamp/ID/checksum metadata, stream revalidated evidence, retain only first
   memberships/preceding outside observations and exact manifest references. Normal and frozen
   input paths have weak-reference retention regressions; policy/timeline/report fixtures preserve
   existing semantics. This is not permission to enlarge the cap or shrink the cohort.
2. **Out-of-window capture poisoned rank recovery.** A delayed request produced an immutable snapshot
   rejected on every restart. Fix: check actual request bounds before market construction; retain
   authentic out-of-window committed evidence as interrupted, not successful. Provenance corruption
   still fails closed. Regressions cover early/late clocks, existing commits and next-slot progress.
3. **Retry expiry extended by in-progress work.** A fresh pass budget allowed HTTP beyond the original
   12-hour expiry. Fix: cap all transport attempts, including pagination/retries, by the earlier
   original expiry/pass deadline; account for pre-pass preparation elapsed time. Read-only input
   preparation runs off the event loop. Regressions cover expiry within pagination, expiry during
   preparation and async control responsiveness. Durable publication is a separate boundary.
4. **Failed-prefix starvation.** Repeated failures in the first 30 symbols prevented later symbols
   receiving a first request. Fix: durable per-symbol visit reservations prioritize least-visited
   symbols, including after cancellation. Jithin explicitly approved an optional exact-permutation
   traversal interface on the existing observer. No truncation, formula, anchor or denominator
   change; no fabricated outcomes for the unvisited remainder.

Resource testing additionally exposed quadratic research rank computation. A deterministic test
measured 40,200 comparisons for 200 symbols before the fix. Returns are now sorted once with the
same competition-rank/tie semantics, and raw ticker rows are indexed once with duplicate coverage
preserved. Source fingerprints change intentionally; incompatible sessions remain blocked rather
than being relabelled. Existing archives remain readable. Core strategy scoring/admission is not
modified by these fixes.

## Verification checkpoints

- All seven initial review regressions failed before the fixes. Subsequent boundary/async/scaling
  regressions also failed before their corresponding fixes.
- Latest focused research policy/timeline/report package: 65 passed. Latest complete author suite:
  650 passed, one inherited Starlette deprecation warning. Strict typing, source lint and 31-file
  formatting checks passed. Exact CI warning-as-error verification also passed 650 tests, with
  the same inherited warning (112.84s).
- Approved frontend lockfile-only Zod 4.6.5 to 4.6.6 patch: clean npm install/audit/lint/build passed;
  audit reported zero advisories, lint reported 11 existing warnings, no errors. ESLint's unsupported
  version warning remains; no unapproved major upgrade was made. A clean audit is not a security
  guarantee. Advisory: https://github.com/advisories/GHSA-mrhx-c7xc-m3w5.
- The final optimized image passed network-disabled Linux amd64 API/stopped-worker/pending-worker
  packaging smoke. Its synthetic 1,000-symbol/577-snapshot report completed under the enforced
  256 MiB/0.5 CPU cap, reporting peak RSS 63,320 KiB (about 61.8 MiB). Image SHA256:
  `f30cd08fc74356533df9a44ed8d6b8ecb6c937fdadcec0d206117496ec2e296d`.
  This is emulated local report-capacity evidence, not sustained real-archive or target-VM load.
  Earlier superseded/invalid-fixture runs were not counted as passes; production validation was
  retained and the corrected fixture is preflighted before expensive evidence generation.

## Open gates and review exclusions

Synthetic report-capacity success does not establish sustained VM/provider load, real archive sizes,
timing objectives or predictive performance. VM storage ownership/backup restore, Secure Boot,
workload permissions, clock and operational load acceptance remain activation gates. Browser-rendered
UI and live direct/proxy parity are not verified by these local checks. No 24h/48h empirical result
or trading suitability is claimed.

Only `alfa2-512717/europe-west10-c/instance-20261025-174348` is an approved cloud target. The existing
main deployment targets the other API VM; it is unchanged and must not be triggered under this scope.
Publication, deployment and activation are separate decisions. The recoverable worktree and ledger
must remain while changes are uncommitted; runtime JSON, PDFs and unrelated edits are excluded.
