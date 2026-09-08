# RCMF Portable Canonical V2.1 M1

Decision: `READY_FOR_PARALLEL_DATASET_ADAPTATION_V2_1`

Last verified UTC: `2026-09-08T17:33:33Z`

## Identity

- Development base: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`
- V2.0 executable ancestor: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`
- V2.1 source: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Archive: `archive/rcmf-portable-canonical-v2_1-0ca0101`
- Branch: `platform/rcmf-portable-canonical-v2_1`
- Pipeline config SHA256: `48e784ad3a806da5bf66e3f78af7d9c159651c44f3db916294ab2488b8037f0c`
- Dataset profile SHA256: `ff57e0a102e81b1ce076c179cf3e134e595ef5cc65a9bf7a726ce02320fa16b6`
- Semantic P00-P11 graph SHA256: `42eb720c76fb5c8f58d4c0d12a7860d1f7ecde186e246c0667d5c202767c93bb`

## Verified Contracts

Portable records reject null/non-string/blank required text and require the
exact supported schema version. Run validation closes task, split, trajectory
source, transition, parent-step, decision-state, and prompt-profile identities.
IDs are unique, steps are contiguous, terminal/replay/success combinations are
coherent, and continuous rewards with optional binary success remain valid.

Required capabilities are derived from the selected phase graph. A bounded
probe executes every claimed method needed by that graph before model loading
or training. Token counting and interactive runtime claims require configured
implementations; optional capabilities remain optional when unused.

The config loads and hashes its dataset profile, binds exact adapter/executor
factories, validates benchmark/prompt identities and safety fields, and rejects
missing ownership. The portable core owns the semantic DAG. Strict manifests
hash source, run, config, dataset, adapter, phase, dependencies, inputs, and
outputs; empty/no-op work cannot manufacture a pass.

Terminal deployment is limited to the configured final epoch. Expected units
come from a strict upstream manifest or frozen policy, not the checkpoint
producer. Pointer/content hashes and run/config/data/unit identities are
checked before deserialization. Missing/extra epochs, nonfinite tensors,
metric selection, and fallback fail closed.

The 39-row ownership inventory has zero missing evidence paths and zero
unresolved reachable defects. Machine scans report zero generic benchmark
imports, benchmark-name or version dispatch, historical counts in generic
success conditions, silent adapter fallbacks, unowned paths, or unproven
prompt assets. One compatible executor binding is present.

## Tests

- Local focused candidate: `57 passed`.
- Final focused local and Lambda/CUDA: `28 passed` each.
- Final local full: `1070 passed, 3 skipped` in `106.95 s`.
- Final Lambda/CUDA full: `1073 passed` in `35.55 s`.
- Every run started with `PYTHONHASHSEED=25101`; no failure was waived.

## Real AppWorld Pilot

The successful isolated pilot is
`rcmf_portable_v2_1_appworld_3demo_pilot_20260909_003`, rooted at
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_portable_v2_1_appworld_3demo_pilot_20260909_003`.
Specification/summary SHA256 values are
`836011dd4b315c1b2277dacf7dede00c9f2e4d1053d7450524df2ee326efaf96`
and `3d30bc1693304d18928138af6f938ed51b233beabc2744e7abe62618aa742fd2`.

The predeclared population contained 12 deterministic training states, four
per causal label, plus two heldout engineering fixtures. One engineering epoch
executed 20 units with 20 backward and 20 optimizer steps. Terminal loss was
finite (`0.32304854588292076`), Qwen and selector remained frozen, and the
checkpoint SHA256 is
`872653bf586be1fdeca9bb7ee3d50a26cd525ffebb87f1ef677495a64423a1a8`.

All 12 P00-P11 manifests passed strict validation. The 499-memory field had
`A=[960,8,256]`, `B=[8,256]`, finite values, and exact add/remove/restore/read
behavior. Its SHA256 is
`c4ae7181bcff27858089ae2eb8bd0198a418e9ba439db57aa319939412a5c643`.
Official-dev task `530b157_3` reached real Qwen generation, AppWorld, and a
typed evaluator with no infrastructure exception. Its outcome is not an
acceptance criterion and is not a scientific result.

Measured wall time was `317.897 s`; H100-active upper bound was `0.0883 h`.
Predeclared expected/conservative estimates were `1.5/4.0 h`, under the `8 h`
cap. Storage was approximately `1.1 GiB` versus `4/8 GiB` estimates.

Attempts `_001` and `_002` are immutable. Both completed 20/20 units before
diagnostic-wrapper-only failures: a stale loss-key assumption, then a local
variable rename error. Neither supplied outputs to `_003`; both defects were
fixed minimally and regression-tested before the fresh successful run.

## Interpretation And Next Actions

The pilot is `ENGINEERING_EXECUTABLE_INTEGRATION_EVIDENCE`, not an AppWorld
result, reproduction, statistical claim, or evidence that another dataset will
succeed. Writer/field/reader mathematics, model, prompts, selector, objective,
formal 14n, and R19 are unchanged.

ALFWorld should create `adapt/alfworld-v2_1`, seal environment/data/license
identities, and replay a few official training expert plans through TextWorld
without Qwen or training. WebShop should create `adapt/webshop-v2_1`, seal
licenses and an isolated server/data/index, then replay the small official
human sample without Qwen or training. Both remain `NOT_EVALUATED` and need
their own adapter/executor, preflight, frozen identity, and authorization.

`READY_FOR_PARALLEL_DATASET_ADAPTATION_V2_1`

`NO FULL APPWORLD SCIENTIFIC RUN WAS LAUNCHED`

`NO ALFWORLD OR WEBSHOP SCIENTIFIC RUN WAS LAUNCHED`

`FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED`
