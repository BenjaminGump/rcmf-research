# EXP-037A-R6 Formal 14g Launch Failure

## Status

- Decision: `STOP_EXECUTABLE_IDENTITY_CONTRACT_FAILURE`
- Scientific result: `NOT_EVALUATED`
- Formal run status: `TERMINAL_BEFORE_SCIENCE`
- Failed stage: `S00_environment_manifest`
- Source: `31c9e98ab408d4768c83d2a45bb1b21ae565b4be`
- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`

## Verified Launch Chain

The explicit authorization was written separately from the immutable
`NOT_AUTHORIZED` request and passed every check in the frozen explicit
authorization validator. The launcher then created a runtime authorization,
which passed every check in the frozen runtime validator.

- Explicit authorization SHA256:
  `2540e7da598c37bbfb03eb3ba2fea082b2b231182abd868c20b57c34a504010b`
- Runtime authorization SHA256:
  `5da0dd48206ffe4487095431f1c9c0047d0d1091fe06b196cae4ecbacd07545e`
- Immutable request SHA256, unchanged:
  `c69378137bfeb0cdbd60ac145f69ad6c7882e56d883a93367e2e8841ab426151`
- Approved cap: 120 wall-clock hours
- Approved scope:
  `complete_fresh_3d_then_conditional_fresh_1d_and_final_reporting`
- Previous 200-hour authorization inherited: false

## Exact Failure

The stage process completed its environment collection with exit code 0 and
wrote a hash-valid `stage_result.json`. Strict post-stage validation then
failed before any downstream or scientific stage was eligible.

The frozen producer `write_stage_manifest()` in
`rcmf/benchmarks/appworld/reproducible_stages_14b.py:1964` writes
`source_commit`, `stage_id`, output hashes, and stage metadata, but does not
write these four run-bound fields:

- `run_uuid`
- `run_root`
- `pipeline_config_sha256`
- `contract_sha256`

The frozen strict consumer `validate_stage_completion()` in
`rcmf/pipeline/validators.py:317` requires those fields when invoked by the
14g scheduler. Its checks were:

```json
{
  "contract_sha256": false,
  "output_hashes": true,
  "pipeline_config_sha256": false,
  "run_root": false,
  "run_uuid": false,
  "source_commit": true,
  "stage_id": true
}
```

This is a producer/validator identity-schema defect in the frozen executable
package. It is not an authorization failure, environment failure, scientific
result, or recoverable stage interruption. The completion is marked
`passed=false` and `recoverable=false`.

## Run Outcome

- Attempts opened: 1
- Attempts closed: 1
- Open attempts: 0
- Hash-valid completed stages: 0
- Scientific stages started: 0
- Optimizer steps: 0
- Qwen/AppWorld scientific generations: 0
- D06B reached: no
- D07/D08/D08B/D09 reached: no
- D22 reached: no
- 1D arm launched: no
- H100 scientific active time: 0

The controlling `exp037a_14g_formal` tmux session exited with the parent.
There is no active EXP-037A process and no GPU compute process. The Lambda
checkout remains clean at the frozen launch source.

## Contract Handling

No manifest was patched, no stage was retried, and no source or scientific
configuration was changed. The R6 authorization cannot be reused after an
executable-source repair because it is bound to the failed source SHA and
contract. A reviewed source fix and a new run-bound approval are required.

VERIFIED facts are recorded above. There is no scientific inference because
no scientific stage ran. The only unverified item is behavior after a future
source-level schema repair, which was not attempted here.
