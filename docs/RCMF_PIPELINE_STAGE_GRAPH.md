# RCMF Pipeline Stage Graph

EXP-037A contains 57 ordered stages. The labels below classify orchestration
ownership, not scientific importance.

## Shared

- `S00` environment manifest: GENERIC orchestration, APPWORLD-SPECIFIC identity.
- `S01` authoritative corpus: APPWORLD-SPECIFIC adapter.
- `S02` task and parent splits: APPWORLD-SPECIFIC leakage contract.
- `S03` transition records: APPWORLD-SPECIFIC adapter.
- `S04` selector supervision: APPWORLD-SPECIFIC optional supervision.
- `S05` transition representations: GENERIC frozen-model encoding with an
  APPWORLD-SPECIFIC transition renderer.
- `S06` CV folds and sampling: GENERIC grouped-manifest machinery.
- `S07` initial parameter snapshots: GENERIC initialization contract.
- `S08` two-arm contract: GENERIC.
- `S09` runtime preflight and authorization validation: GENERIC.

## Three-Demo Arm

`D00` through `D22` use the generic scheduler and artifact contract. State
rendering, procedural selector labels, AppWorld causal conditions, live worlds,
and evaluator calls are APPWORLD-SPECIFIC. Selector optimization,
factorization, writer/field/reader optimization, reversible field algebra,
checkpointing, controls, paired statistics, and reproduction-gate scheduling
are GENERIC once their tensors and metrics are supplied by the adapter.

The sequence is state representations, selector CV and selection, final
ensemble and factorization, selected memories, paired outcomes, policy
teachers, zero cache/training units, two writer/reader epochs, heldout
teacher-forced/one-step/full-trajectory validation, checkpoint selection,
401-memory field, incremental addition of 98 memories, 499-memory validation,
common one-demo bare/correct/shuffle dev trajectories, read-only historical
comparison, and the frozen reproduction gate.

## Conditional One-Demo Arm

`O00` through `O19` call the same implementation as the corresponding `D00`
through `D17`, `D19`, and `D20` stages. They are eligible only when the
sealed `D22` output is exactly `THREE_DEMO_REPRODUCTION_PASS`. The
scheduler launches `O00` directly after validating `D22`; the monitor is
not involved.

## Final

- `F00` paired two-arm analysis: GENERIC statistics over adapter metrics.
- `F01` portability validation: GENERIC boundary audit.
- `F02` Git-safe audit export: GENERIC redaction plus adapter redaction.
- `F03` report and handoff: GENERIC record assembly.

When the 3D gate does not pass, all `O` stages are skipped and the final stages
record the stopped branch. No 1D scientific output is created.
