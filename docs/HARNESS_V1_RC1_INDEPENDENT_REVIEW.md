# Harness V1 RC1 Independent Review

Status: `STOP_FAIRNESS_CONTRACT_INCOMPLETE`

Verified UTC: `2026-09-09T03:29:06Z`

## Authority And Scope

- RCMF development base: `fbc9bd205d63f4b6e5d46f275a3c2278935a8359`.
- RCMF canonical V2.1 source: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- RCMF review source: `ca93ed71a747c5c1ba0cac3d2659636ca936f092`.
- RCMF review archive: `archive/rcmf-neutral-harness-v1-rc1-review-ca93ed7`.
- Harness source/records: `ec953717d233f3958d8c6b3180b0b4a785312ad8` /
  `185da8e4354df3403e08dc4a88309f60235c2c7b`.
- This review used only isolated clones from the verified export. It did not
  alter the source export or the four original repositories.
- No model, benchmark environment, training loop, or scientific evaluation
  was run.

## Export Verification

The ZIP SHA256 is
`4827530c0c89aa3e825f10390981e966e048f6503b5d1cdc891c4648ec445809`.
The 49-entry exported manifest is exact and its content SHA256 is
`79c1ea6723706a97b758376d94c6ebc1e4479197aac5ef0562784e3a37784429`.
All four bundles passed `git bundle verify` and matched their declared SHA256:

| Bundle | SHA256 |
|---|---|
| harness | `5066157e1c21d94f9a621f3f25ce6cd5ed61e897a9171f2e3d22a0c11f412008` |
| ReMe | `069c5094fdf3fdf244cd7dcd84e7b23adc8e3e61a78660da19aad8538c53d73a` |
| MemGen | `bde01e93dc22d7b20975d41e4dc93807c79a490d3216b53f08393d6121bcf499` |
| delta-Mem | `10d64f1cca9ef0e3c980bd5cbec048e7c884031a782d67603818accb462dd625` |

The export contains no model checkpoint, cache, private credential, or raw
protected runtime observation. One literal-export qualification is required:
complete upstream Git histories include public research data already tracked
upstream, including ReMe paper JSONL and delta-Mem `data/locomo10.json`.
Therefore the export is not literally dataset-free. No newly added harness
dataset was found. MemGen and delta-Mem remain private/internal because their
root source-code licenses are unresolved.

## Baseline Identities

| Method | Adaptation head | Upstream pin | Disposition |
|---|---|---|---|
| ReMe | `cb52f1f636029d9ceb2d984e15dd9d8acd68fcc2` | `2f37a159b72a04ac1885a7db7f1a663a833e7791` | documentation-only; Apache-2.0 |
| MemGen | `e8097fee206ee2c38704f671743a19babe492017` | `970cc95af99b5008610e6b281619d181bc9b5ab9` | documentation-only; private/internal |
| delta-Mem | `5b4b86c3dce11e35ac9c27f16ef6988790d011d0` | `5cd5d9153c7f408764728d953565201e198c39e2` | documentation-only; private/internal |

Every adaptation is exactly three commits ahead of its pinned upstream and
has a clean checkout. No functional method adapter or standardized benchmark
support is claimed.

## Method Neutrality

The protocol is method-neutral at RC1's declared lifecycle level. Its
capability-scoped hooks represent all six required shapes without requiring a
shared memory algorithm:

| Method | Represented behavior | Ownership |
|---|---|---|
| ReAct | no-memory lifecycle | `METHOD_OWNED` no-op |
| TTR | content-hashed raw-transition prompt slot | `METHOD_OWNED` |
| RCMF | offline preparation/compile and model-forward reader binding | `METHOD_OWNED` |
| ReMe | retrieval message augmentation and external services | `METHOD_OWNED` |
| MemGen | training preparation and latent model-forward hook | `METHOD_OWNED` |
| delta-Mem | training, model hook, online state, snapshot/reset | `METHOD_OWNED` |

The shared task loop, base prompt, model/generation identity, environment,
reward, evaluator, task audit, and comparison gate are `HARNESS_OWNED`.
Task conversion, action parsing, reward interpretation, and environment bridge
are `DATASET_ADAPTER_OWNED`. Run UUID, task outputs, resource telemetry, and
terminal status are `RUN_DERIVED`. Published model-specific reproduction
scripts, prompts, checkpoints, and paper metrics are
`UPSTREAM_REPRODUCTION_ONLY` unless separately retargeted and locked.

RC1 lifecycle mocks execute meaningful state transitions: TTR/ReMe append
separately hashed method slots, MemGen returns a changed model-forward wrapper,
delta-Mem writes/resets/snapshots state, and RCMF binds an exact checkpoint and
field through the new RCMF-side plugin. Generic harness code imports no RCMF
implementation.

## Fairness Contract

Verified safe:

- benchmark and method lock schemas bind data, task, split, trajectory,
  prompt, model, generation, action, reward, evaluator, method, service, and
  checkpoint identities;
- comparison rejects different harness, benchmark-lock, task, prompt, model,
  generation, and method provenance identities;
- Qwen2.5-1.5B, Qwen3-4B, and Qwen3-8B cannot compare as identical models;
- `OFFICIAL`, `OFFICIAL_ADAPTED`, `RETARGETED_IMPLEMENTATION`, and
  `UNOFFICIAL_REIMPLEMENTATION` are distinct;
- upstream-reproduction and standardized tracks are distinct;
- `METHOD_FAILURE` task rows require null reward/success and a typed error.

Verified blocking defects in the exported harness source:

1. `result_manifest.per_task_results` has no semantic closure against the
   exact task manifest. Duplicate task IDs, omissions, and reordering can pass
   schema validation while `complete=true`.
2. `validate_result_against_locks()` does not accept a run manifest or task
   rows, so it cannot bind result `run_uuid`, `harness_source_sha`, exact task
   membership/order, task-result hashes, or task statuses to the authorized
   run.
3. A schema-valid result containing `METHOD_FAILURE` can pass lock validation
   and `evaluate_comparison_eligibility()` returns eligible with no reason.
   The typed failure is preserved in the row, but the comparison gate does not
   fail closed and no executable aggregator proves it will not become an
   ordinary unsuccessful task.

Direct reproduction returned:

```text
duplicate_task_ids_schema = ACCEPTED
wrong_harness_source_lock_validation = ACCEPTED
method_failure_result_lock_validation = ACCEPTED
method_failure_comparison_eligible = true
comparison_reasons = []
```

These defects prevent `READY_TO_FREEZE_NEUTRAL_HARNESS_V1`.

## Thread B Patch Proposal

Do not patch the exported bundle in place. In the harness repository:

1. Add one semantic finalization validator taking `run_manifest`,
   `benchmark_lock`, `method_lock`, the exact ordered task manifest, every
   content-addressed task result, and the result manifest.
2. Require exact run UUID and harness source equality; exact unique ordered
   task membership; exact task-result hash/status equality; and no omitted or
   extra task.
3. Define `complete` as closure over the full task manifest, not a producer
   assertion.
4. Make any `METHOD_FAILURE`, `ENVIRONMENT_FAILURE`, `INVALID_OUTPUT`, or
   `TIMEOUT` ineligible for the main performance comparison, with a typed
   reason. Never coerce it to reward zero.
5. Add negative tests for duplicate, omitted, reordered, wrong-run,
   wrong-harness, hash-mismatched, and typed-failure rows.

## RCMF H1-H3 Resolution

### H1 — same-run dependency identity

Confirmed. V2.1 previously checked dependency manifest hash and `passed` only.
The review source now requires exact source, run UUID/root, pipeline config,
dataset profile, and adapter identity for normal dependencies. Different
identities require an explicit `SEALED_UPSTREAM` reference and a separately
hashed closure listing the accepted manifest SHA.

### H2 — actual executor-handler registry

Confirmed. A factory could declare every phase while its instantiated handler
map omitted one. Every executor instance now exposes its bound phase IDs and
the complete selected DAG is proved before work. Missing or unknown handlers
fail before phase execution.

### H3 — dataset semantic identity

Confirmed. Dataset profile action/reward/split/trajectory-source fields were
parsed but not retained and cross-validated. They are now required, retained,
and checked against adapter identity metadata and trajectory sources. The
RCMF-neutral-harness binding derives content-addressed split, trajectory,
action, and reward identities without benchmark-name dispatch and validates
them against the harness benchmark lock.

The RCMF plugin keeps writer/field/reader mathematics on the RCMF side. It
binds exact source, method config, terminal checkpoint, deployment field,
model-forward reader injection, and episode reset. It does not expose raw
memory prompts or runtime retrieval.

## Validation

- Harness RC1: `32 passed`; seven schema positive/negative families included.
- RCMF local focused: `38 passed in 4.05 s`.
- RCMF local full: `1080 passed, 3 skipped in 201.43 s`.
- RCMF Lambda focused: `38 passed in 1.41 s`.
- RCMF Lambda/CUDA full: `1083 passed in 38.99 s`.
- ReMe/MemGen/delta-Mem source compile: `603/37/39` passed.
- ReMe/MemGen/delta-Mem JSON parse: `5/3/6` passed.
- All three baseline `harness_adapter/` RCMF-import scans: passed.
- Actual exported harness lifecycle with the RCMF plugin: passed.
- AppWorld, ALFWorld-like, and WebShop-like semantic-lock fixtures: passed.
- H100 remained idle; no training or benchmark evaluation occurred.

## Decision

`STOP_FAIRNESS_CONTRACT_INCOMPLETE`

The harness is method-neutral and the RCMF binding is ready for review. The
task/result/run closure and typed-failure comparison defects must be fixed and
independently retested in Thread B before final Harness V1 freeze.

NO RCMF OR BASELINE TRAINING WAS RUN.

NO STANDARDIZED BENCHMARK RESULT WAS PRODUCED.

NO FINAL HARNESS V1 WAS FROZEN.

FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED.
