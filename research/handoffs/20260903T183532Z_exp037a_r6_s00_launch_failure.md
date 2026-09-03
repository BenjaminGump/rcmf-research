# EXP-037A-R6 S00 Launch Failure Handoff

## Identities

- Records branch: `research/v6-rcmf-exp037a-final-launch-freeze`
- Launch source: `31c9e98ab408d4768c83d2a45bb1b21ae565b4be`
- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`

## Verified

- The user-approved explicit authorization and generated runtime authorization
  both passed their actual frozen validators.
- The launcher ran from the clean Lambda archive checkout at the exact launch
  source. No records commit was used for execution.
- `S00_environment_manifest` ran once and its process exited 0.
- Strict validation failed because the output-manifest producer omitted
  `run_uuid`, `run_root`, `pipeline_config_sha256`, and `contract_sha256`, all
  required by the 14g strict validator.
- The attempt closed as failed and non-recoverable. No later stage started.
- No stale completed stage was reused. No optimizer, scientific generation,
  D06-D09, D22, or 1D work ran.
- The parent tmux exited. There is no active EXP-037A process or H100 compute
  process. Lambda remains clean at the launch source.

## Decision

`STOP_EXECUTABLE_IDENTITY_CONTRACT_FAILURE`

The run is scientifically `NOT_EVALUATED`. Do not edit the sealed run root or
retry under this authorization. A minimal reviewed executable fix must make
the producer emit the same run-bound identity schema required by the strict
validator, followed by tests, a new launch source, and a new explicit
run-bound authorization.

## Evidence

- Report: `research/results/EXP_037A_R6_FORMAL_LAUNCH_FAILURE.md`
- Summary: `research/results/exp037a_r6_formal_launch_failure/summary.json`
- Artifact index:
  `research/results/exp037a_r6_formal_launch_failure/artifact_index.json`

No scientific interpretation is possible from this run.
