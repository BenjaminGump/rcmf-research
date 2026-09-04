# EXP-037A-R10 Whole-Pipeline Audit Handoff

## Decision

`READY_FOR_REAUTHORIZATION`

Scientific result: `NOT_EVALUATED`.

## Git

- Starting records SHA: `aee326eccc43256352a5ff773b8d1f49a8d124c4`
- Working branch: `research/v6-rcmf-exp037a-whole-pipeline-audit`
- Implementation commits: `34b7bbd`, `aeb9421`, `0e40155`
- Frozen launch source: `0e4015547da45802cc7b6ff3a9b92adce73077fc`
- Archive ref: `archive/exp037a-r10-launch-source-0e40155`
- Records commit: the Git commit containing this handoff
- Formal execution must use the frozen launch source, not the records commit.

## Launch Package

- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Config: `configs/pipeline/rcmf_appworld_repro_14j.yaml`
- Config SHA256: `470f7183adb8804540c46e146e2db9e359f09f67b43eb0d3e0fab153bedf41a9`
- Contract SHA256: `2ff7810700abe99c53c298978e0c6f14d56f6cd0465d2c8065d3569e59abdafb`
- Artifact-index SHA256: `8c0af8a6c1dbd4e1a95dfeba60a334924dcf4b453db6a633afbd46fadd98f46b`
- Authorization: `NOT_AUTHORIZED`; no runtime authorization exists.
- Root contents: preflight only; no stages, attempts, checkpoints, or science.

## Audit Outcome

Five reachable execution/provenance defects were fixed without scientific
changes: actual stage-artifact sealing; checkpoint pointer SHA/root/boundary
validation before load; post-restore module hash checks; full D06B/D22 prior
stage identity; and exact 401/499 field/provenance checks.

All 60 formal stages have an explicit production artifact resolver and strict
synthetic contract coverage. The frozen-source real S00-S04 production path
passed 5/5 in 15.944 seconds and the second run skipped 5/5 with zero child
executions. The sealed 14h audit found 23 passed stages and one attempted
failure (D10); every declared artifact for the passed set exists and is
content-addressed.

## Tests

All commands used process-start `PYTHONHASHSEED=25101`.

- Local focused R10: 16 passed in 39.76 s.
- Local full: 931 passed, 3 skipped in 78.34 s.
- Lambda focused R10/CUDA: 16 passed in 15.83 s.
- Lambda full/CUDA: 934 passed in 33.27 s.

Diagnostic roots:

- Pipeline audit: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r10_pipeline_audit_20260904_003`
- Real path: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r10_s00_s04_14j_20260904_002`
- Test/runtime inputs: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r10_preflight_inputs_20260904_001`

The failed audit-only `_002` root is preserved. It exposed that a
`passed=false` completion must not be counted as completed; the audit tool was
corrected and retested. No formal/scientific artifact was affected.

## Residual Risk

D11-D22 and O00-O19 have not all executed in a complete fresh formal run.
Their producer/consumer contracts, branch handling, and strict manifests are
statically mapped and synthetically tested, but only the authorized long run
can exercise all expensive behavior. An abrupt host loss can also leave a
fail-closed scheduler lock requiring operator triage.

## Runtime State

- H100: 0% utilization, 0 MiB used of 81559 MiB at final preflight capture.
- Active EXP-037A/14j formal processes: 0.
- Formal tmux: absent; only read-only 14h watchdog/status bridge remain.
- NFS mounted; approximately 3.344 PB reported free by the backing mount.
- Safe to leave idle or terminate Lambda: yes, subject to the user's separate
  preference to retain the instance.

No long run was launched. A new explicit authorization bound to the exact
14j source/UUID/root/config/contract/scope and proposed 120-hour cap is
required before launch.

