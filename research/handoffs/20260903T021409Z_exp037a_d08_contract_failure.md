# EXP-037A D08 Contract-Failure Handoff

## Scope

Publish the fatal `_001` failure as Git-safe review evidence only. No repair,
resume, new run, or scientific evaluation was authorized or performed.

## Identities

- Branch: `research/v6-rcmf-reproducible-3d-gated-pipeline`
- Publication parent: `7cc63c0a430153b8e6be2a81536fbd1bbaa1d365`
- Scientific source: `02ef94726ea0fe566f7eea4fa137fb91da92977f`
- Run: `rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`
- Raw root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`

The raw root remained read-only. Lambda remained clean at the scientific source
and was not switched to the GitHub records HEAD.

## Result

- Decision: `INFRASTRUCTURE_IMPLEMENTATION_FAILURE`
- Scientific result: `NOT_EVALUATED`
- Failed stage: `D08_zero_cache_and_training_units`
- Three-demo reproduction gate: `NOT_REACHED`
- One-demo arm: `NOT_LAUNCHED`

`scripts/prepare_rcmf_joint_full_bank_9a.py::_section_contract` line 204
attempted to read `source_task_goal_tokens`. The first transition row,
`001629b3-5715-5e40-b42a-b7d00dc82b7d`, lacks all four required per-section
token fields. Its manifest SHA256 is
`f1484d419cb475efcf11231d0fa0548ee402322eba7c96db4301679255f829b3`.

The fresh producer adds aggregate `teacher_section_tokens`; the compatibility
adapter copies it unchanged; the historical 9a consumer requires four
per-section counts. D08 produced no scientific output, changed no checkpoint or
config, and ran zero writer/reader optimizer updates.

## Preserved Evidence

- Human report: `research/results/EXP_037A_FAILED_D08_CONTRACT.md`
- Summary: `research/results/exp037a_reproducible_pipeline_failure/summary.json`
- Stage table: `research/results/exp037a_reproducible_pipeline_failure/stage_status.json`
- Contract diff: `research/results/exp037a_reproducible_pipeline_failure/d08_contract_diff.json`
- Artifact index: `research/results/exp037a_reproducible_pipeline_failure/artifact_index.json`
- Attempts: `research/results/exp037a_reproducible_pipeline_failure/attempts_summary.json`

Artifact-index SHA256:
`370b46e3aca0f99ade20e334a208de6ff94e83c74e164b5c80716aa297c546af`.

D00-D07 are `8/8` content-address validated and may be used only as read-only
diagnostic fixtures. They are not automatically authorized as scientific input
to a future run.

## Process State

The final watchdog snapshot was captured at `2026-09-03T02:13:39Z`, after
which the explicitly authorized orphaned watchdog was stopped. At
`2026-09-03T02:14:09Z`, parent/watchdog tmux sessions and EXP-037A processes
were absent; H100 utilization and memory use were `0%/0 MiB`. NFS remained
mounted. Lambda was not terminated.

## Review Boundary

VERIFIED: this is a deterministic producer/consumer contract failure before
scientific model training or evaluation.

INFERENCE: an unchanged-source restart would repeat the failure.

UNVERIFIED: the correct repair layer. User and ChatGPT review must choose it
before any source change or resume decision.
