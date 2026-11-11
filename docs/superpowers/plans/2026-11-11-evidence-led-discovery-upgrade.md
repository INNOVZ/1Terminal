# Evidence-led discovery upgrade

## Intended outcome

Prepare locally verified research tooling that compares the existing engine with a
versioned discovery challenger using the November 11 findings. User performs deployment.
Existing uncommitted work and completed studies are preserved. No trading integration.

## Implementation slices

- [x] Isolate reporting from rank acquisition, add lightweight progress health, distinguish
  observed/missed/failed/absent samples, and verify concurrent/restart behavior.
- [x] Persist precise selection and accumulation rejection evidence without altering
  default screening outcomes. Record actual selection allocations and capture budget.
- [x] Add an explicit research feature contract with complete 336-hour volume history
  and real 4H RVOL. Preserve the current engine as the control.
- [x] Add a bounded discovery watch pool using existing base liquidity requirements,
  and expose readiness/activation evidence independently of published opportunities.
- [x] Wire capture, offline replay and prospective arm reports through the normal CLI
  entry points, with explicit resource/input budgets and versioned policies.
- [x] Verify focused regressions, full suite and representative resource capacity. Prepare
  a disabled-by-default handoff with exact remaining deployment/empirical checks.

## Research rules

No token-specific exceptions or threshold fitting. Discovery observations carry their
availability and rejection evidence. Missing inputs invalidate the affected comparison
arm. A watch observation has its own outcome metrics. It must never imply an executed
trade or automatically satisfy production publication gates.

Repeated-cohort relative-strength transition research requires periodic point-in-time
inputs. The completed single frozen cohort supplies historical hypotheses. Any cadence
extension must bound work and preserve each issuance timestamp and fixed outcome anchor.

## Verification ledger

Implementation started November 11 after explicit user approval. Baseline contains the
earlier lifecycle/research work and November 11 analysis artifacts, all uncommitted.

Focused integration checks passed for acquisition/replay/preparation/reporting, source and
history guards, short-history availability, selection bounds, and transition chronology.
The full-size diagnostic payload exposed the definition-size guard incompatibility, which
was repaired through a checksummed companion and compact frozen evidence. Full-suite and
final image/resource checks are in progress. Synthetic artifacts remain local and clearly
labelled. No new live cohort or VM deployment was performed.
