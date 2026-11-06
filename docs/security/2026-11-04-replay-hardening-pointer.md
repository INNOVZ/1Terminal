# Replay hardening continuation

Implementation is in the existing isolated worktree:
`/Users/jithinjacob/.codex/worktrees/dual-track-shadow/trdindicator`.

Read `docs/security/2026-11-04-replay-hardening.md` there for the strict/archive replay contract,
explicit diagnostic mode, tests, limitations and commands. Do not copy the dirty checkouts wholesale.
Existing collector repair and all other uncommitted prerequisite changes were preserved.
No core screening thresholds, old archive schemas, deployment or observation anchors were changed.

The supplied six-token history remains unavailable for strict historical comparison; diagnostic
results are explicitly labelled and excluded from strict predictive denominators.
