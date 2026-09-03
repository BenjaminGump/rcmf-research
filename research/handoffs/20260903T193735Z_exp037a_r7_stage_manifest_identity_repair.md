# EXP-037A-R7 Stage-Manifest Identity Repair Handoff

## Git And Run Identity

- Starting records SHA: `c003de43307b263b688bb074560c3a05022fb882`
- Repair branch: `research/v6-rcmf-exp037a-stage-manifest-identity-repair`
- New launch source: `272b0e9a1f8b29f898024f1115d1710d41270758`
- Launch archive: `archive/exp037a-r7-launch-source-272b0e9`
- New UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001`
- New root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001`

Formal execution must check out the launch source, not the later records SHA.

## Repair

The successful production manifest now receives a canonical run-bound identity
payload from the stage runner. It includes source commit, UUID, canonical root,
config SHA, contract SHA, stage ID, and attempt ID. The same payload feeds the
post-identity failure path. Strict mode fails before stage execution if any
scheduler identity is missing or inconsistent.

Strict validation additionally verifies dependency completion hashes during
resume. No validator check was weakened. R5's manually synthesized manifest
test was replaced as the primary positive coverage by real writer to real
validator tests over all 60 formal stage IDs.

## Production-Path Evidence

The real S00-S04 scheduler/subprocess diagnostic is preserved at:

`/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r7_stage_manifest_smoke_14h_20260904_002`

All five stages exited zero, passed strict validation, and wrote passing
completions. Their manifest SHA256 values are listed in the main report and
`production_path_smoke.json`. The second scheduler invocation skipped all five
and launched zero subprocesses. The diagnostic stopped before S05.

The preserved `_001` diagnostic root failed before science due to two missing
diagnostic compatibility fixtures. It was not reused or deleted. The fresh
`_002` root is the passing evidence.

## Package And Authorization

- Config SHA256:
  `53b87b0624fe08824b4d976554e745c95753ab59ecbf338a1c060a069259cfd9`
- Contract SHA256:
  `dce77ba06e39b8e38449f10f514e3a0972d0e15ef04e9a5e762720e8d1f0fc29`
- Artifact-index SHA256:
  `0cf9266928330a7a2bec732441b470e8dbbac3c575ebcd7037f3ebcd96b8f9e8`
- Authorization: `NOT_AUTHORIZED`
- Previous 200-hour authorization inherited: false
- Failed R6 120-hour authorization inherited: false
- Proposed cap: 120 hours

The formal root was fresh and now contains preflight only. It has no runtime
authorization, stages, formal attempts, or scientific output. A new explicit
run-bound approval is required.

## Invariance And Tests

Scientific changes are zero. Selector, panel, prompts, D06B, writer/reader,
D08/S05B, D08B, D22, conditional 1D, and evaluation remain frozen.

Local full tests: 900 passed, 2 skipped. Lambda full tests: 902 passed. Real
production-path stages: 5/5 passed; resume skips: 5/5. Process-start
`PYTHONHASHSEED=25101` was used.

No scientific stage, optimizer, AppWorld scientific trajectory, or H100
scientific computation ran. The H100 is idle, there is no active formal
EXP-037A process, and Lambda is safe to leave idle or terminate.

## Decision

`READY_FOR_REAUTHORIZATION`

Do not launch until the user explicitly approves the exact new launch source,
UUID, root, config SHA, contract SHA, 120-hour cap, and full conditional scope.
