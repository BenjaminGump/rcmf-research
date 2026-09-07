# EXP-037A-R16 O13 Path-Ownership Repair Handoff

## Decision

`READY_FOR_14M_AUTHORIZATION`

No long formal run was launched. The new 14m package is preflight-only and
`NOT_AUTHORIZED`.

## Git identity

- Starting records branch: `research/v6-rcmf-exp037a-o08-count-ownership-continuation`
- Starting records SHA: `d15bfe586cee5980009acad7475ad6aaf7e3d686`
- R16 branch: `research/v6-rcmf-exp037a-o13-path-ownership-repair`
- 14l failure-record SHA: `b1b6e84696c5942c1897bb53eade4d66829d3eed`
- Repair implementation SHA: `b9a5db2ceaaf5cc766c9cb312d35f17fcf3e9216`
- Frozen 14m launch source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Frozen archive: `archive/exp037a-r16-launch-source-a8cd3b6`
- Records SHA: the records-only commit containing this handoff; formal
  execution must not use it.

## Verified 14l failure

- 14l source: `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- Run UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Failed stage: `O13_heldout_full_trajectory`
- Failed attempt: `O13_heldout_full_trajectory-1788777706792999587-r1`
- Child exit code: `65`
- Failed UTC: `2026-09-07T10:41:49.174202+00:00`
- Runtime before failure: approximately 2.785 seconds
- Trajectory executions: `0`
- Backward passes: `0`
- Optimizer steps: `0`

Traceback chain in frozen 14l source:

1. `scripts/run_rcmf_reproducible_stage_14b.py:154`
2. `rcmf/benchmarks/appworld/reproducible_stages_14b.py:2343`
3. `_heldout_full_trajectories()` at line 1618
4. `_heldout_query_overrides()` at line 1581
5. `torch.load()` raised `FileNotFoundError`

The missing file was the continuation-local path
`arms/1d/representation_cache/multiview/state_multiview.pt`. It is absent by
design because the continuation does not rerun O00. The resolved 14l arm
configuration already identified the authoritative sealed 14k O00 cache under
`stage_c_9a.prompt_dependent_inputs.state_cache`.

Classification:

`VERIFIED_CONTINUATION_STATE_CACHE_PATH_OWNERSHIP_MISMATCH`

The configured parent cache exists, is part of the validated 922-artifact
parent closure, and has SHA256
`73943bc85c2a1f4cf1c85a7c8c40dac4b5e954e63520530d91595be205c9a4fa`.
Alternative causes involving O12 field/checkpoint corruption, heldout task
identity, query identity, AppWorld execution, or parent-closure corruption were
checked and rejected by direct evidence. Failure occurred before AppWorld
trajectory execution.

Evidence SHA256 values:

- `failure.json`: `45882591f3248a97fd44e691e81eb006be8c36b6539fa23d5a12f941b7ba7d15`
- `completion.json`: `b59761bb2748e6d1a1799adacfb6e8cd8d65c9802d7a9733bf932f99ec027da1`
- `process.json`: `9a05f08e82ac47ed150822b75c7853256e09f208813c539239bcfbf0bfcfa1ef`
- `orchestrator_result.json`: `e011a108fff025ea335cffa958c9d8fc02cf36abc5122da4d6a86eea252fa846`
- Resolved arm config: `f1164bcf6050c0a46e84c31ca70153dc2b64eef23bad1e0bbbdeb3d02971a087`

## Sealed upstream validity

O09 through O12 remain strict-valid. Their output-manifest SHA256 values are:

- O09: `c2e502f80017a577eb4ef7fccfc784906ae49447695f36c61e71f6c4000adb9f`
- O10: `8216c838048b03675ccddbc83ed5581c616362d4712fd20ffc2a9f4ef5058c33`
- O11: `a2f0b3736bd186f9628e7750b152732471c58cec291962375519d8a2608b3607`
- O12: `3801b9d663b47ba3194e3e587a7c7d3f57df3b4a8d1bbbfb090a05e15021842d`

Epoch checkpoints remain sealed evidence only:

- Epoch 1: `f354a2a66e871d50a798597e7eab311e627133a5e6797e11076fd8c2adae4213`
- Epoch 2: `c4a9a604ec287785f966f85fa26a88e2d9c2618254af0dce8de8b54326722c21`
- Completed units/backward passes: `1032/1032`

The 14k parent closure revalidated at 922 files with closure SHA256
`f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
Neither the sealed 14k parent nor the failed 14l formal root was modified.

## Repair

`rcmf/benchmarks/appworld/reproducible_stages_14b.py` now resolves the state
cache through the explicit arm configuration and passes the canonical path to
the heldout query-override builder. The resolver:

- requires `stage_c_9a.prompt_dependent_inputs.state_cache`;
- canonicalizes and validates the configured path;
- fails closed when the value or file is missing;
- performs no historical-root search or implicit fallback;
- does not copy, link, or write the parent cache.

Fresh full-run behavior is unchanged because its resolved arm configuration
owns the corresponding local O00 output through the same field. The state
tensor, query construction, shuffle mapping, task set, prompts, checkpoints,
model, losses, and evaluator are unchanged.

The O13-F03 path-ownership audit found one repaired defect, zero additional
reachable same-class defects, zero ambiguous ownership sites, and zero
unresolved sites. Parent O08 partials remain prohibited.

## Bounded diagnostics

No-generation path diagnostic:

- Root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r16_o13_path_20260907_002`
- Result SHA256: `847e9c1e93cdd1eb0644f93e06752be77d8b673bba01c817bc5d86833af5a031`
- Runtime: 2.57 seconds
- Heldout tasks: 8/8 mapped
- Finite tensors: PASS
- Repeat hash: exact
- AppWorld generations/backward/optimizer: `0/0/0`

Bounded integration smoke:

- Root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r16_o13_smoke_20260907_003`
- Result SHA256: `d45f34761afbda893b58f77596faee5cfd8255b8692e597c6a3cedcd916f4d95`
- Task: `76f2c72_1`
- Condition: epoch-2 state-query shuffle under the one-demo prompt
- Runtime: 75.67 seconds; trajectory runtime 72.019 seconds
- Steps: 19
- Query override used: every step
- Infrastructure exceptions: 0
- Backward/optimizer: `0/0`
- Task success: false, which is not an engineering failure and is not used as
  scientific evidence.

The first two smoke wrapper attempts exposed diagnostic-only wrapper defects
(wrong result directory, then reused AppWorld attempt identity). The fresh
`_003` attempt corrected those wrapper issues and passed; no formal or
scientific output was reused.

## Tests

All commands used process-start `PYTHONHASHSEED=25101`.

- Local focused: `26 passed in 6.24s`
- Local full: `996 passed, 3 skipped in 76.94s`
- Lambda focused: `26 passed in 2.70s`
- Lambda full/CUDA environment: `999 passed in 37.68s`

An earlier local full run had one failure in an R12B static source-string
predicate after the new explicit config resolver was added. The predicate was
updated to inspect the actual runtime ownership contract; the final full runs
above passed.

## Frozen 14m package

- UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Launch source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Config: `configs/pipeline/rcmf_appworld_continuation_14m.yaml`
- Config SHA256: `c8b5af07f72cd64472fa47fabd3f339d640f80ddb30cbd2fd0c8db41bee6f9f0`
- Contract SHA256: `e00df7b19d525bf8a317cbd9332f08bdacf4a4e1aa3ef47243d839f6402849be`
- Stage-scope SHA256: `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`
- Parent-manifest SHA256: `95e10d58f4f30df71cf050cffa62040de47b98c2a36dde3febcf08b5dd7e1d60`
- Parent closure SHA256: `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Artifact-index SHA256: `c972f782b532182f909694c855cbbb6de5038be79efa7dd191d1bf7fdcc1b5b6`
- Authorization-request SHA256: `82eb6b4c6a75ba094020aeb0e4318925be28a346b4c20bbfa763785efc11db6e`
- Authorization: `NOT_AUTHORIZED`

The 14m root contains preflight and resolved configuration only. It has no
runtime authorization, stages, attempts, scientific outputs, 14l checkpoint,
or 14l completion. Its stage scope is C00, C01, O08-O19, and F00-F03, using
the sealed 14k O07 boundary. O08-F03 will be freshly generated under one 14m
source.

## Runtime and next action

- Measured 14l C00-C01/O08-O12 wall: 2.6734 hours
- Expected 14m wall: 9.5 hours
- Conservative wall: 20.0 hours
- Expected/conservative H100-active: 9.3/19.5 hours
- Additional storage: 5/10 GiB expected/conservative
- Proposed anomaly cap: 32 hours
- Monetary cost: `MONETARY_COST_NOT_AVAILABLE`

The conservative estimate exceeds 18 hours. The only safe next action is user
review followed, if approved, by a fresh exact run-bound authorization for the
frozen 14m package. Formal execution must check out
`a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`, not the records commit.

No 14l checkpoint, stage completion, authorization, or diagnostic artifact is
a formal 14m scientific input.

`NO LONG FORMAL RUN WAS LAUNCHED`

`READY_FOR_14M_AUTHORIZATION`
