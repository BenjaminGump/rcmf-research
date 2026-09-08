# Dataset Onboarding

Complete these gates in order. A later gate cannot repair a failed earlier
identity or provenance gate.

1. **Pin source and license.** Record repository, full commit, data/archive
   identities, redistribution terms, and environment version.
2. **Install environment.** Use a dataset-specific environment/process
   namespace. Record interpreter, package lock, external server/index, and a
   reset smoke. Do not install large assets during an unrelated milestone.
3. **Freeze prompt baseline.** Vendor legally permitted source assets, record
   hashes/license/extraction, define one named renderer, and prove exact
   role/content arrays and runtime token counts.
4. **Inventory official trajectories.** Keep expert, human sample, full human,
   IL/model, oracle, and agent-generated sources separate. Unknown provenance
   fails closed.
5. **Replay-validate trajectories.** Execute source actions in the exact
   deployment interface, preserve returned observations and raw rewards, and
   emit typed failures. Admit only successful training trajectories.
6. **Freeze split/leakage manifest.** Stable task/episode IDs and lineage keys
   must prove training/evaluation separation. No fixed split names or counts.
7. **Pass V2.1 adapter and executor conformance.** Implement the exact adapter,
   bind a loadable phase-executor factory, probe only the capabilities required
   by the selected graph, validate schemas/reward semantics/relocation, and
   check no benchmark import or benchmark-name dispatch enters portable core.
8. **Run bare-agent smoke.** Use a tiny engineering-only task set to validate
   rendering, tokenization, actions, reset/replay, evaluation, and audit.
9. **Run RCMF module diagnostics.** Test complete transition representation,
   selector inputs, independent write, reversible add/remove, fixed-size read,
   and terminal checkpoint validation without a scientific claim.
10. **Run a small integration test.** Predeclare a bounded train/evaluate slice;
    do not select methods from outcomes. Preserve full manifests and typed
    failures.
11. **Measure runtime/storage.** Give expected and conservative stage times,
    GPU-active time, storage, restart plan, and anomaly-cap formula. Do not
    reduce workload to fit a cap.
12. **Request formal approval.** Freeze source/config/contract/root and obtain
   explicit run-bound approval before scientific GPU work. Launch nothing
   automatically.

Development starts from the latest records commit while retaining explicit
`development_base_records_sha`, `canonical_executable_ancestor_sha`,
`canonical_archive_ref`, `bootstrap_generated_at_sha`, and
`last_verified_utc` fields in task/bootstrap documents. Executable state is
frozen first; later records bind that immutable source. A dataset profile must
own every required field, bind its content hash and prompt assets, and match
the instantiated adapter identity. Missing ownership never falls back.

Parallel ports use separate worktrees, branches, NFS roots, environments,
ports, and task state files. No shared mutable cache is allowed. Begin from
`tasks/<dataset>/STATE.md`; historical failure reports are demand-loaded via
`docs/FAILURE_MODES.md`.

The executable base for new ports is source
`0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`, frozen at
`archive/rcmf-portable-canonical-v2_1-0ca0101`. The AppWorld pilot proves the
shared executor contract can reach real training and evaluation; each new
dataset still owns its environment, records, replay, prompts, reward, and
phase handlers.
