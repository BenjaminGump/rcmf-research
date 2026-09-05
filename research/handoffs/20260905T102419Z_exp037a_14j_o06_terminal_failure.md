# EXP-037A 14j O06 Terminal Failure Handoff

Date UTC: 2026-09-05T10:24:19Z

## Decision

- Project decision: `INFRASTRUCTURE_IMPLEMENTATION_FAILURE`
- Root cause: `DETERMINISTIC_LIVE_CONTEXT_PREFLIGHT_MISMATCH`
- Three-demo result: `THREE_DEMO_REPRODUCTION_PASS`
- One-demo result: `NOT_EVALUATED_COMPLETE`
- Cross-arm result: `NOT_REACHED`

## Identity

- Executed source: `0e4015547da45802cc7b6ff3a9b92adce73077fc`
- Source archive: `archive/exp037a-r10-launch-source-0e40155`
- Records base: `5e57a92951812a23d052629eb7a7a5d30985cd78`
- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Config SHA: `470f7183adb8804540c46e146e2db9e359f09f67b43eb0d3e0fab153bedf41a9`
- Contract SHA: `2ff7810700abe99c53c298978e0c6f14d56f6cd0465d2c8065d3569e59abdafb`

## What Completed

The scheduler validated 42 stages. All shared and 3D stages completed. D06B
exactly reproduced 366 train, 98 heldout, and label counts 129/300/35. D08B
passed. D22 returned `THREE_DEMO_REPRODUCTION_PASS`. Fresh 3D dev counts were
bare/correct/shuffle `12/17/11` on 57 tasks, compared with historical
`12/17/12`.

The conditional one-demo arm completed O00-O05. O06 did not complete. No O07
or later one-demo stage and no final stage ran.

## Failure

Attempt `O06_paired_causal_outcomes-1788596760065108009-r1` failed at
`appworld:trace:229360a_3:step:27:line:382`, selected transition
`60716ec9-9ca6-5011-9efc-15b0db10b2f0`. O05's stored-state renderer counted
the base/raw prompts as 18,957/38,075 and admitted the condition. O06's live
replay renderer counted the bare prompt as 23,809 and failed closed when its
raw prompt exceeded 40,960. The exact live raw count was not persisted.

This is a deterministic preflight/runtime message-source mismatch, not a
transient GPU or process failure. Completion says `recoverable=false`; no
output manifest exists.

## Partial Evidence

- 178 complete paired states, 356 paired condition outputs.
- One additional bare output for state 179, total 357 atomic outputs.
- Last complete label counts: harmful 13, neutral 103, positive 62.
- Three replay-missing diagnostics.
- Partial O06 data is not a sealed scientific panel and cannot be reported as
  one-demo performance.

## Runtime State

- Formal wall time: 19.666 hours.
- Attempts: 43 total, 42 complete, 1 failed, 0 open.
- Formal tmux and parent are absent.
- H100 is idle with no formal process.
- The read-only 14j status bridge remained present at final inspection.
- The Lambda instance is safe to terminate with respect to 14j, but was not
  terminated.

## Preservation And Next Action

The raw root, frozen source, partial outputs, authorizations, and all completed
stages remain unchanged. Raw observations with secrets remain Lambda-only.
No source/config repair, retry, resume, new UUID, or new scientific run was
performed.

The next safe action requires ChatGPT/user review: align preflight scoreability
with exact live replay message semantics or explicitly revise the admission
contract, test it, freeze a new source/package, and obtain new authorization.

## Records

- Main report: `research/results/EXP_037A_14J_O06_TERMINAL_FAILURE.md`
- Machine summary: `research/results/exp037a_14j_o06_terminal_failure/summary.json`
- Artifact index: `research/results/exp037a_14j_o06_terminal_failure/artifact_index.json`
- Audit index: `research/audits/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001/index.json`
