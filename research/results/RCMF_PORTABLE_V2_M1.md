# RCMF Portable V2 M1: Canonical Engineering Base

Date (UTC): 2026-09-08

Decision: `READY_FOR_PARALLEL_DATASET_ADAPTATION`

Status: `ENGINEERING_VERIFIED_PORTABLE_CANONICAL_BASE`

This milestone creates the versioned engineering base for separate ALFWorld
and WebShop adaptations. It is not cross-dataset scientific validation and did
not execute model generation, training, an AppWorld experiment, or a shuffle
experiment.

## Identity

- Starting source: `ca799ffa5692678081d69f4994e470267def4ec5`
- Implementation branch: `platform/rcmf-portable-canonical-v2`
- Canonical source: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`
- Immutable archive: `archive/rcmf-portable-canonical-v2-ea152c7`
- Pipeline config: `configs/pipeline/rcmf_portable_canonical_v2.yaml`
- Config SHA256: `f8ed4d006d19d7de2432a1360d621211a3e8506e63fba6a486f27e3ea130bf19`
- Semantic 12-phase DAG SHA256:
  `6d06c358ecc8ad4f6caa4b1196082fecc0ca5d7de7d4405f3d488950202752a9`
- Canonical manifest: `docs/PORTABLE_CANONICAL_V2.json`

The earlier candidate archive `archive/rcmf-portable-canonical-v2-e726f56`
is preserved but superseded: final preflight found that its semantic DAG named
a nonexistent generic runner. The final DAG requires an explicit
`{portable_phase_executor}` supplied by a reviewed dataset adaptation and
cannot manufacture pass manifests through a no-op fallback.

## Checkpoint Policy

Historical formal 14n remains unchanged: its heldout proxy selected epoch 1.
R19 remains a post-hoc forced-epoch-2 diagnostic. Portable v2 prospectively
uses `terminal_completed_epoch`: configured epoch `N` is the only deployable
epoch, and it must be complete, finite, file-hash-valid, unit-count-valid, and
bound to run/config/data identity. Missing or invalid epoch `N` fails closed;
an earlier valid epoch is never selected as fallback. Heldout/dev metrics are
accepted only as diagnostics and are absent from the deployment decision.

Implementation: `rcmf/pipeline/portable_v2/checkpoint_policy.py`. Eleven
focused policy tests cover one, two, and more-than-two epochs; missing,
nonfinite, wrong-identity, and hash-invalid terminal checkpoints; metric
independence; and no fallback. Historical checkpoint-selection code remains in
the legacy reproducibility path.

## Ownership Audit

The machine inventory has 39 reachable or historically material entries:

| Class | Count |
| --- | ---: |
| CORE_MATHEMATICAL_INVARIANT | 6 |
| CONFIGURED_RUN_POLICY | 5 |
| DATASET_ADAPTER_FACT | 6 |
| UPSTREAM_DERIVED_ARTIFACT_VALUE | 5 |
| REPRODUCTION_PROFILE_ONLY | 2 |
| EXTERNAL_ENVIRONMENT_DEPENDENCY | 3 |
| LEGACY_UNREACHABLE_HISTORY | 3 |
| DEFECT | 9 |

All nine defect rows are repaired and regression-covered. They include the
shared replay prompt override, stale historical count ownership,
continuation-local state-cache ownership, version-string dispatch, wrong-run
monitoring, CUDA-mapped CPU RNG restore, success-manifest identity omission,
stale checkpoint/artifact pointer validation, and attempt/result collision
risks. Unresolved reachable defects: `0`. The two reproduction-only entries
remain accepted only inside historical AppWorld profiles.

Inventory: `docs/audits/PORTABLE_V2_OWNERSHIP_INVENTORY.jsonl`, SHA256
`774657878708f41739692b5d6a8786eb8a8f8d785f30df2dc0eb23ee974f453d`.

## Portable Architecture

`ReproducibleBenchmarkAdapterV2` is the only authoritative new-dataset
protocol. It owns benchmark/data/environment identity, capabilities, arbitrary
split/task/episode identities, typed trajectory provenance, replay, opaque
actions, messages/token counting, causal comparison, reward/success,
evaluation, and redaction. The registry has no default or AppWorld fallback.

The generic core consists of versioned schemas, capabilities, semantic full
and continuation DAGs, owned artifact resolution, terminal checkpoint
validation, prompt-source verification, and manifest-only conformance under
`rcmf/pipeline/portable_v2/`. It has zero direct AppWorld/ALFWorld/WebShop
imports, version-specific continuation dispatch, or historical outcome-count
success conditions. Dataset work is limited to adapters, profiles, prompt
assets, task state, tests, and explicit phase executors.

The writer/field/read mechanism is unchanged. Add/remove remain independent
per-memory outer-product updates; the fixed-shape field and read do not depend
on bank size; raw ledger entries remain authoritative; Qwen remains frozen;
runtime retrieval and raw-memory prompts remain prohibited. Direct tests cover
reversible add/remove and source-level absence of bank loops in production
add/remove/read functions.

## Prompt Package

| Profile | Upstream source | Local artifact SHA256 | Role |
| --- | --- | --- | --- |
| ALFWorld `react_task_type_two_demo_v1` | ReAct `6bdb3a1...`, `prompts/alfworld_3prompts.json`, blob `0e7c204...`, MIT | `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` | default candidate, not scientifically run |
| WebShop `react_official_one_demo_v1` | ReAct `6bdb3a1...`, `WebShop.ipynb`, blob `67cb9f8...`, MIT | `58e4164eb648db4f8f437ee8a7b3ce064d3661d929e4f0fe71155ea6b062c56a` | default candidate, not scientifically run |
| ExpeL references | `e41ec9...`, Apache-2.0 | ALF `27dfa1...`; WebShop `60faa7...` | reference only |
| Reflexion ALFWorld corroboration | `218cf0...`, MIT | same pinned ReAct ALF asset | corroboration only |

The ALFWorld JSON is an exact Git blob. The WebShop prompt is AST-extracted
from its sole `prompt1` assignment without textual correction; upstream line
trailing spaces are intentionally preserved. Qwen3-8B tokenizer-only
validation matched backend/direct chat-template counts for all six ALFWorld
families (`1367, 1137, 1574, 1593, 1378, 1395`) and WebShop (`572`). Model
loading and generation were both false.

## Dataset Readiness

ALFWorld is pinned to `aaba6870f86c5be6a08a491f32a50b906227bc3e`
(MIT). Official expert plans/actions and TextWorld games exist, but exact
deployment-interface text trajectories must be produced by replaying official
training games, validating success, and binding every command/observation to
data/game/package hashes. Train is memory/training; validation-seen/unseen are
evaluation-only. Lambda has no installed `alfworld`; no install was attempted.

WebShop is pinned to `64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`
(MIT). The setup exposes an approximately 50-trajectory human sample. The
larger human archive is `OFFICIAL_HUMAN` only after identity/terms validation;
the IL archive is `UNKNOWN_PROHIBITED` until provenance is proven. A
training-metadata oracle remains a separately labelled fallback design only.
Accepted trajectories must replay to official reward exactly `1.0`; partial
reward is not full success. Product, instruction, search-index, server, split,
and archive identities remain unsealed. Lambda has no installed `webshop` or
`gym`; no install/download was attempted.

## Documentation And Routing

Root `AGENTS.md` is 12,105 bytes and contains the core charter, invariants,
authority order, worktree/run isolation, task-state routing, and authorization
rules. Its prior 35,847-byte content is preserved exactly at
`docs/charters/LEGACY_PROJECT_CHARTERS_THROUGH_EXP037A_R19.md` (SHA256
`5a9b1a2d23a20c0e9b61815615abe618d30ee8f86a419c10238b0cb9cf6709fd`).

Authoritative compact docs now cover pipeline, adapter, onboarding, prompt
sources, history, failure modes, scientific status, dataset readiness, task
states, adaptation briefs, and the deferred shuffle anomaly. Four ChatGPT
bootstrap caches route new conversations without requiring full history.
`docs/CHATGPT_PROJECT_SOURCES.md` lists exactly the five requested project
source files. Their validator checks paths, source SHAs, state/context
agreement, handoff existence, authority order, and absence of premature
ALFWorld/WebShop scientific claims.

## Validation

- Local focused: `33 passed`; local full: `1041 passed, 3 skipped`.
- Lambda focused: `33 passed`; Lambda full/CUDA: `1044 passed`.
- Generic DAG: two relocated variable fixtures, 12/12 phases each.
- AppWorld compatibility: renderer parity and complete 12-phase manifest-only
  fixture pass.
- ALFWorld-like and WebShop-like mocks: action, reward, replay, provenance,
  leakage, capabilities, relocation, and derived-count conformance pass.
- Prompt source extraction/action delimiters/hash provenance: pass.
- Portability gates: all ten required violation counts are `0`.

One preliminary Windows full run hit a temp-directory ACL error in an old
scheduler test; the exact test and final full suite passed in isolated system
temp roots. Lambda also caught two readiness docs omitted by an overly broad
ignore rule; tracking was repaired before source freeze. No test was waived.

## Scientific Status And Next Tasks

Formal 14n remains epoch-1 correct/shuffle `8/57` and `18/57`; R19 remains
post-hoc epoch-2 correct/shuffle `16/57` and `19/57`, mixed/inconclusive. The
single-permutation shuffle anomaly is explicitly deferred until after the
2026-09-25 submission.

ALFWorld first bounded task: inspect/install the isolated environment and data,
seal versions/licenses/splits, then replay a few official training expert games
through TextWorld without Qwen or training.

WebShop first bounded task: inspect source/archive licenses and isolated server
setup, seal product/index/instruction identity, classify trajectory archives,
then replay the small human sample without Qwen or training.

Neither dataset has a scientific result or launch authorization.

## Decision

`READY_FOR_PARALLEL_DATASET_ADAPTATION`

`NO LONG SCIENTIFIC RUN WAS LAUNCHED`

`NO SHUFFLE EXPERIMENT WAS LAUNCHED`

`FORMAL_14N_AND_R19_RESULTS_REMAIN_UNCHANGED`
