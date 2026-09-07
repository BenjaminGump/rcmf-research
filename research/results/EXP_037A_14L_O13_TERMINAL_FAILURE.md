# EXP-037A 14l O13 Terminal Failure

## Decision

`VERIFIED_CONTINUATION_STATE_CACHE_PATH_OWNERSHIP_MISMATCH`

This is an executable path/provenance failure, not a scientific result. The
14l continuation is terminal and remains immutable. Complete one-demo heldout
full-trajectory evaluation and all later stages are `NOT_EVALUATED`.

## Verified Failure

- Run UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Source: `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- Failed stage: `O13_heldout_full_trajectory`
- Attempt: `O13_heldout_full_trajectory-1788777706792999587-r1`
- Exit: 65, fatal and nonrecoverable
- Failure UTC: `2026-09-07T10:41:49.174202+00:00`
- Exception: `FileNotFoundError`
- No O13 trajectory task or condition executed. O13 produced only its process,
  failure, completion, and empty stdout/stderr records.
- O13 performed zero backward passes and zero optimizer steps.

The traceback enters `_heldout_full_trajectories()` and then
`_heldout_query_overrides()` in
`rcmf/benchmarks/appworld/reproducible_stages_14b.py`. At frozen-source line
1581, the helper directly loads:

`$CONTINUATION_ROOT/arms/1d/representation_cache/multiview/state_multiview.pt`

That continuation-local path is absent by design because 14l did not rerun
O00. The resolved 14l arm configuration instead owns the input through
`stage_c_9a.prompt_dependent_inputs.state_cache` and points to the sealed 14k
parent artifact.

## Parent Evidence

The configured parent state cache exists and independently hashes to:

`73943bc85c2a1f4cf1c85a7c8c40dac4b5e954e63520530d91595be205c9a4fa`

It is a read-only member of the sealed 922-artifact parent manifest. The full
parent boundary validation passed with closure SHA256:

`f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`

Parent D22 remained exact PASS, O06/O07 population validation passed, parent
O08 remained failed with no output manifest, and prohibited parent O08
partials remained excluded.

## Preserved 14l Evidence

Strict validation passed for O09, O10, O11, and O12, including all run/source/
config/contract/dependency/output hashes. Training completed 1032 units and
1032 backward passes. Checkpoints:

- epoch 1: `f354a2a66e871d50a798597e7eab311e627133a5e6797e11076fd8c2adae4213`
- epoch 2: `c4a9a604ec287785f966f85fa26a88e2d9c2618254af0dce8de8b54326722c21`

These artifacts remain sealed evidence. They are not authorized as formal
inputs to a replacement continuation.

## First Divergence

1. The 14l resolved config correctly identifies the sealed parent O00 state
   cache as the authoritative prompt-dependent input.
2. O13 bypasses that resolved input and constructs a current-arm local path.
3. The local file is absent because O00 is outside the continuation DAG.
4. O13 fails during query-override setup before trajectory execution.

Alternatives checked and excluded by direct evidence: corrupt/missing O12
outputs, invalid epoch checkpoint, parent closure corruption, heldout task
execution failure, and a failure after partial O13 generation.

## Evidence

Raw Lambda evidence remains under:

`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`

Exact evidence hashes are in the accompanying machine-readable summary and
artifact index. The formal parent process/tmux and scheduler lock are absent;
the H100 is idle.

## Next Action

Repair only authoritative path ownership, validate O13 input preparation and a
bounded integration trajectory in an isolated diagnostic root, then prepare a
fresh continuation from the sealed 14k O07 boundary. Do not resume 14l and do
not import 14l checkpoints into formal replacement science.

