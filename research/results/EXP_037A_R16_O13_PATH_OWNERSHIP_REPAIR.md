# EXP-037A-R16 — O13 Path-Ownership Repair and 14m Freeze

## Decision

`READY_FOR_14M_AUTHORIZATION`

The independently verified 14l failure classification is:

`VERIFIED_CONTINUATION_STATE_CACHE_PATH_OWNERSHIP_MISMATCH`

The repair is limited to path/provenance resolution. It changes no scientific
formula, prompt, task population, checkpoint, model, selector, training rule,
field algebra, or evaluation rule. No long formal run was launched.

## Frozen identities

- Starting records SHA: `d15bfe586cee5980009acad7475ad6aaf7e3d686`
- 14l launch source: `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- 14l failure-record SHA: `b1b6e84696c5942c1897bb53eade4d66829d3eed`
- Production repair implementation SHA: `b9a5db2ceaaf5cc766c9cb312d35f17fcf3e9216`
- Frozen 14m launch source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Frozen archive: `archive/exp037a-r16-launch-source-a8cd3b6`
- Working branch: `research/v6-rcmf-exp037a-o13-path-ownership-repair`

## Exact 14l failure

The sealed 14l run terminated at `O13_heldout_full_trajectory`, attempt
`O13_heldout_full_trajectory-1788777706792999587-r1`, with child exit code
65. The traceback from frozen source `95b355a` is:

1. `scripts/run_rcmf_reproducible_stage_14b.py:154`
2. `rcmf/benchmarks/appworld/reproducible_stages_14b.py:2343`
3. `_heldout_full_trajectories()` at line 1618
4. `_heldout_query_overrides()` at line 1581
5. `torch.load()` raised `FileNotFoundError`

The missing path was:

`14l_root/arms/1d/representation_cache/multiview/state_multiview.pt`

That continuation-local file is absent by design because 14l did not rerun
O00. The resolved 14l arm config instead owns the input through
`stage_c_9a.prompt_dependent_inputs.state_cache`, pointing to the sealed 14k
O00 cache. That parent cache exists, is included in the 922-file closure, and
has SHA256
`73943bc85c2a1f4cf1c85a7c8c40dac4b5e954e63520530d91595be205c9a4fa`.

O13 failed before the first trajectory, backward pass, or optimizer step. Its
directory contains only failure/completion/process evidence and empty process
logs; it has no output manifest or valid scientific output.

### Alternatives checked

- O12 field artifacts missing/corrupt: disproved; O12 is strict-valid.
- Epoch checkpoint corruption: disproved; both checkpoint hashes recomputed.
- Heldout task population mismatch: disproved; all eight task IDs map exactly.
- State/query identity mismatch: disproved by deterministic tensor hashes.
- Failure after trajectory generation: disproved; trajectory count is zero.
- Parent closure corruption: disproved; 922/922 files and closure hash pass.
- AppWorld/runtime failure: disproved; failure occurs before AppWorld startup.

## Valid sealed 14l evidence

Strict validation was rerun read-only for O09-O12:

| Stage | Output manifest SHA256 | Result |
|---|---|---|
| O09 | `c2e502f80017a577eb4ef7fccfc784906ae49447695f36c61e71f6c4000adb9f` | PASS |
| O10 | `8216c838048b03675ccddbc83ed5581c616362d4712fd20ffc2a9f4ef5058c33` | PASS |
| O11 | `a2f0b3736bd186f9628e7750b152732471c58cec291962375519d8a2608b3607` | PASS |
| O12 | `3801b9d663b47ba3194e3e587a7c7d3f57df3b4a8d1bbbfb090a05e15021842d` | PASS |

- Epoch-1 checkpoint SHA256:
  `f354a2a66e871d50a798597e7eab311e627133a5e6797e11076fd8c2adae4213`
- Epoch-2 checkpoint SHA256:
  `c4a9a604ec287785f966f85fa26a88e2d9c2618254af0dce8de8b54326722c21`
- Completed units/backward passes: `1032/1032`
- O13 backward/optimizer count: `0/0`

These artifacts remain sealed evidence and diagnostic fixtures only. None is a
formal 14m scientific input.

## Repair

The old `_heldout_query_overrides(target, task_ids)` constructed the state
cache path as a continuation-local path under `target`.

The repaired path is:

1. Load the resolved arm config.
2. Require `stage_c_9a.prompt_dependent_inputs` to be a mapping.
3. Require a non-empty configured `state_cache` path.
4. Canonicalize it and require the configured file to exist.
5. Pass the verified path explicitly into `_heldout_query_overrides()`.

There is no historical-run search, local fallback, or cache copy. A missing or
unknown configured input fails closed. Fresh full-run behavior is unchanged
because a full-run arm config already resolves the same field to its local O00
output.

The only production file changed is
`rcmf/benchmarks/appworld/reproducible_stages_14b.py`. A stale R12B static
source predicate was updated separately to recognize the equivalent local
`config.benchmark.prompt_profile` binding introduced by this repair.

## Downstream ownership audit

All reachable input paths from O13 through F03 were classified. Results:

- Repaired defects: 1
- Additional reachable same-class defects: 0
- Ambiguous ownership: 0
- Unresolved defects: 0
- Parent O08 partials: excluded

O13-O19 training/evaluation products are continuation-owned. F00 reads the
sealed parent three-demo summaries through `pipeline.continuation.parent_root`
and reads one-demo summaries from the continuation root. F02/F03 use the
explicit parent manifest/root. The earlier local state-cache reference in O04
is not reachable in the 14m continuation graph and is correct for a fresh
full-run O00→O04 path.

Machine record:
`research/results/exp037a_r16_o13_path_ownership_repair/path_ownership_audit.json`

## Bounded diagnostics

### No-generation path diagnostic

- Root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r16_o13_path_20260907_002`
- Wall time: 2.57 seconds
- Heldout tasks mapped: 8/8
- Repeat tensor identity: exact
- All tensors finite: yes
- Parent cache unchanged: yes
- Trajectories/generations/backward/optimizer: `0/0/0/0`
- Result SHA256:
  `847e9c1e93cdd1eb0644f93e06752be77d8b673bba01c817bc5d86833af5a031`

### One-task O13 integration smoke

- Root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r16_o13_smoke_20260907_003`
- Predeclared task: `76f2c72_1`
- Condition: epoch-2 state-query shuffle
- Prompt: `full_demo_first_only`
- Wall time: 75.67 seconds; measured trajectory: 72.02 seconds
- Steps: 19
- Infrastructure/execution exceptions: 0
- Query override used on every step: yes
- Fixture hashes unchanged: yes
- Backward/optimizer: `0/0`
- Task success: false (not an engineering failure and not scientific evidence)
- Result SHA256:
  `d45f34761afbda893b58f77596faee5cfd8255b8692e597c6a3cedcd916f4d95`

The `_001` smoke completed the production trajectory but its new wrapper
looked in the wrong diagnostic subdirectory. `_002` then collided with the
same diagnostic AppWorld attempt name. Both issues were confined to the new
diagnostic wrapper. `_003` used a corrected path and unique attempt identity
and passed from a fresh output root.

## Tests

- Local focused: `26 passed` in 6.24 seconds.
- Local full: `996 passed, 3 skipped` in 78.86 seconds.
- Lambda focused: `26 passed` in 3.66 seconds.
- Lambda full/CUDA environment: `999 passed` in 38.91 seconds.
- Process-start `PYTHONHASHSEED=25101` was used throughout.

An earlier local full attempt produced `995 passed, 3 skipped, 1 failed`
because the R12B static source predicate did not recognize the equivalent
local config binding. The predicate was updated without changing runtime
behavior, and all final suites pass.

## 14m package

- UUID:
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Launch source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Config: `configs/pipeline/rcmf_appworld_continuation_14m.yaml`
- Config SHA256:
  `c8b5af07f72cd64472fa47fabd3f339d640f80ddb30cbd2fd0c8db41bee6f9f0`
- Contract SHA256:
  `e00df7b19d525bf8a317cbd9332f08bdacf4a4e1aa3ef47243d839f6402849be`
- Stage-scope SHA256:
  `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`
- Parent-manifest SHA256:
  `95e10d58f4f30df71cf050cffa62040de47b98c2a36dde3febcf08b5dd7e1d60`
- Parent closure SHA256:
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Artifact-index SHA256:
  `c972f782b532182f909694c855cbbb6de5038be79efa7dd191d1bf7fdcc1b5b6`
- Authorization-request SHA256:
  `82eb6b4c6a75ba094020aeb0e4318925be28a346b4c20bbfa763785efc11db6e`
- Authorization: `NOT_AUTHORIZED`

The root contains preflight and resolved config only. It has no runtime
authorization, stages, attempts, scientific outputs, 14l checkpoint, or 14l
stage completion.

## Runtime and deadline

Measured 14l C00-C01/O08-O12 time was 2.6734 hours. Sealed 14k anchors were
1.5316 hours for D13, 2.0447 hours for correct dev, and 1.9754 hours for
shuffled dev. The bounded repaired O13 trajectory took 72.02 seconds.

- Expected 14m wall: 9.5 hours
- Conservative wall: 20.0 hours
- Expected H100-active: 9.3 hours
- Conservative H100-active: 19.5 hours
- Additional storage: 5 GiB expected / 10 GiB conservative
- Proposed anomaly cap: 32 hours
- Monetary cost: `MONETARY_COST_NOT_AVAILABLE`

The 32-hour proposal exceeds `max(2 × 9.5, 1.25 × 20)` after practical upward
rounding. Because conservative runtime exceeds 18 hours, a new exact run-bound
authorization is required. The September 25 deadline does not relax that gate.

## Deviations

The Windows sandbox helper repeatedly failed to read existing files for
`apply_patch`. Existing-file edits were therefore performed with guarded,
exact-match PowerShell replacements after `apply_patch` failed; new files used
`apply_patch`. No scientific or implementation-scope deviation occurred.

`NO LONG FORMAL RUN WAS LAUNCHED`

`READY_FOR_14M_AUTHORIZATION`
