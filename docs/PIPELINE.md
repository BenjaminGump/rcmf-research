# RCMF Portable Canonical Pipeline V2

Status: `ENGINEERING_VERIFIED_PORTABLE_CANONICAL_BASE` after the release gates
listed here pass. This is not cross-dataset scientific validation.

Canonical source SHA: `PORTABLE_CANONICAL_SOURCE_SHA` (bound in the canonical
version manifest after source freeze). Historical scientific evidence remains
at formal 14n source `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`
and R19 diagnostic source `2a7f371f378eab42e42629a4fd5275e18b2814ce`.

## Architecture

The authoritative memory is a ledger of complete transitions. For memory
`i`, the frozen addressing decomposition emits key `k_i` and the feed-forward
writer emits an eight-slot payload `v_i`. With preregistered scalar weight
`rho_i` and optional intercept coefficient `mu_i`, the reversible field is:

```text
A = sum_i rho_i * outer(k_i, v_i)       shape [K, S, P]
B = sum_i rho_i * mu_i * v_i            shape [S, P]
read(q) = rms_norm(B + einsum(q, A))     shape [S, P]
```

The current AppWorld-derived dimensions are `K=960`, `S=8`, and `P=256`.
They are implementation-profile values, not dataset counts. A memory add is
one outer-product accumulation; remove applies its exact negative. Both are
independent of current bank size. The core read touches fixed-shape `A/B` only,
so its output and computation do not depend on memory count. The standard
reader injects the fixed eight slots at configured frozen-model layers.

Authoritative implementation anchors:

- Writer/field/read: `rcmf/training/rcmf_joint_full_bank_9a.py`
- Portable records: `rcmf/pipeline/portable_v2/schemas.py`
- Adapter protocol: `rcmf/pipeline/portable_v2/adapter.py`
- Registry/capabilities: `rcmf/pipeline/portable_v2/registry.py`
- Artifact ownership: `rcmf/pipeline/portable_v2/artifacts.py`
- Semantic DAG: `rcmf/pipeline/portable_v2/dag.py`
- Terminal checkpoint: `rcmf/pipeline/portable_v2/checkpoint_policy.py`
- Manifest-only conformance: `rcmf/pipeline/portable_v2/conformance.py`
- AppWorld wrapper: `rcmf/benchmarks/appworld/portable_adapter_v2.py`

Legacy EXP-037A modules remain intact for historical reproduction. New ports
must use portable v2 rather than copying those benchmark-specific stages.

## Canonical Data Flow

| Phase | Inputs and owner | Outputs | Validation and restart |
|---|---|---|---|
| P00 environment/data provenance | adapter identity, dataset profile, source/prompt/split manifests | immutable provenance closure | exact versions, licenses, hashes, capabilities; atomic rerun if invalid |
| P01 successful trajectory corpus | adapter trajectory providers | canonical successful replay-validated trajectories | provenance admitted, training split only, typed replay failures |
| P02 memory transition ledger | P01 trajectories | complete goal/state/action/observation records | stable IDs, ordered complete steps, lineage and dependency hash |
| P03 representations | P02 ledger and decision states | complete semantic representations | model/tokenizer identity, no token subsampling, finite shapes |
| P04 selector supervision | P03 plus adapter compatibility evidence | split-bound training/fold manifests | no leakage, counts derived, adapter capability required |
| P05 paired causal outcomes | frozen selector, exact replay, prompt profile | bare/conditioned rows and typed missingness | runtime-equivalent rendering, no outcome-led substitution |
| P06 teacher/training units | P05 and transition source manifests | teacher cache, zero cache, deterministic units | exact upstream state IDs, finite rows, no imputation |
| P07 writer/reader training | pristine initialization and P06 units | immutable per-epoch checkpoints | one configured seed, atomic checkpoint/resume, Qwen and selector frozen |
| P08 per-epoch diagnostics | completed epoch checkpoints | teacher-forced/one-step/trajectory reports | diagnostic only; cannot choose deployment epoch |
| P09 terminal checkpoint validation | final configured epoch record | one deployment checkpoint reference | complete, finite, hash/identity/unit valid; no metric selection/fallback |
| P10 deployment field | P09 checkpoint and permitted ledger | training field, feed-forward additions, matched controls | exact IDs, finite fixed shapes, reversible add/remove and permutation checks |
| P11 official evaluation/reporting | adapter official split, P10 fields, fixed generation config | per-task audit, aggregate result, final provenance | exact task order/count from manifest; no post-hoc tuning |

Each stage writes an immutable typed manifest whose dependency hashes bind its
inputs. A downstream consumer recomputes paths/counts/IDs from that strict-valid
manifest. Historical observed values are never promoted into generic success
conditions.

## Training And Checkpoints

Training uses one predeclared seed and an explicit epoch count. Checkpoints
contain writer, reader, optimizer, unit-order, epoch/unit counters, Python RNG,
CPU torch RNG, CUDA RNG, source hashes, and format identity. Same-stage resume
may use a hash-valid intermediate checkpoint. A later phase may use only the
complete epoch-boundary artifact its contract names.

Portable deployment uses `terminal_completed_epoch`: if configured epochs are
`N`, deployment must use epoch `N`. Missing, incomplete, nonfinite, stale,
wrong-identity, wrong-unit-count, or hash-invalid epoch `N` fails closed, even
if an earlier checkpoint is valid. Per-epoch diagnostics remain visible but
are not selectors.

## Core Versus Adapter

The core owns schemas, provenance classes, capability validation, semantic
stages, manifest chaining, artifact ownership, terminal-checkpoint semantics,
and writer/field/read invariants. An adapter owns dataset/runtime versions,
task and split identities, trajectory providers, replay, opaque actions,
prompt assets/rendering/token counting, reward/success semantics, causal
comparison, official evaluation, and secret redaction.

Normal adaptation is limited to `rcmf/benchmarks/<dataset>/`,
`configs/datasets/`, `assets/prompts/<dataset>/`, `tasks/<dataset>/`, and
dataset tests/entrypoints. The portable core cannot import benchmark packages
and has no fallback adapter.

## Provenance And Relocation

Artifacts resolve by logical name, explicit owner (`CURRENT_RUN`,
`SEALED_UPSTREAM`, or `SHARED_IMMUTABLE_EXTERNAL`), producer-manifest hash, and
content hash. There is no directory search. Full runs and continuations are
semantic modes; a continuation declares a phase boundary and validates a
sealed upstream closure without rewriting upstream completions.

Runs use unique Git worktrees, branches, run UUIDs, roots, and process
namespaces. Completion is atomic and attempts are append-only. A moved fixture
or run remains valid only when its declared paths and hashes resolve under its
new explicit manifest, never through an old absolute path.

## Safe Extensions

Safe extensions add an exact adapter, a versioned dataset profile, pinned
prompt/source manifests, typed trajectory provider, and tests. They may vary
task/split/memory counts, action grammar, prompt examples, reward type,
environment reset semantics, and official metric. They may not vary the
writer/field/read mathematics, introduce retrieval, put raw memories in the
query, tune on evaluation, or silently reinterpret missing provenance.

## Known Limitations

- Portable v2 has bounded conformance evidence, not ALFWorld/WebShop science.
- ALFWorld and WebShop runtime/data installations still require task-specific
  verification.
- Their Qwen tokenizer counts and environment replay must be sealed before a
  scientific preflight.
- The formal AppWorld 14n result used the legacy heldout checkpoint selector;
  portable v2's terminal policy is prospective and scientifically distinct.
- The matched-shuffle anomaly is deferred in
  `docs/deferred/SHUFFLE_ANOMALY_POST_SUBMISSION.md`.

