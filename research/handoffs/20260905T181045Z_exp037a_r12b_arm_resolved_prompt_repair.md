# EXP-037A-R12B Structured Handoff

## Status

- Decision: `READY_FOR_14K_REAUTHORIZATION`.
- Scientific result: `NOT_EVALUATED_NEW_FORMAL_RUN`.
- Authorization: `NOT_AUTHORIZED`.
- Starting SHA: `b1145984db901de7ce90f8169a1bbe7f5ce4dea7`.
- Core repair commit: `ab2960e63d5768642ff30e2c228a01ce6a61dd69`.
- Frozen launch source: `004f866647cfabb38a141b88e6d83821df88c403`.
- Frozen archive: `archive/exp037a-r12b-launch-source-004f866`.
- Records SHA: the commit containing this handoff.

## Formal package

- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Config SHA: `f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69`.
- Contract SHA: `eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6`.
- Artifact-index SHA:
  `d94770dc7e8114b63973d74cd14a40f721d92c2c25a5011a1ccbd92b947af448`.
- Root contents: preflight only; no runtime authorization, scientific stages,
  or formal attempts.

Formal execution must check out the frozen launch source, not the later
records commit. A future user authorization must bind the exact UUID, root,
source, config SHA, contract SHA, scope, and approved hard cap.

## Verified repair

- Legacy paired-causal settings remain the shared baseline.
- 3D execution diff is empty and legacy/effective generation hashes match.
- 1D execution diff contains only `prompt_profile=full_demo_first_only`.
- Artifact provenance records and validates arm-resolved, legacy, and effective
  profiles before expensive generation.
- Static counting now matches runtime `enable_thinking=False` semantics. The
  exhaustive 499-by-2 audit found zero discrete selection changes.

## Bounded diagnostics

- Known-target live counts: 42,927 full-demo / 38,078 one-demo.
- Six-state check: 6/6 wrong-profile over context; 6/6 corrected feasible.
- Actual paired-condition smoke: 2/2 generated under one-demo, no optimizer or
  backward.
- Fresh isolated O06: 407 pairs, 814 newly generated conditions, 324/83
  train/heldout, labels 40/247/120 harmful/neutral/positive, 11 static-over-
  context, 10 replay-missing, 5,679.38 seconds, strict PASS.
- O07 consumer smoke: one-demo profile, zero generation/optimizer/backward.
- D06 compatibility: 928/928 prompt hashes and token counts exact.
- Downstream prompt consumer audit: 9 rows, 0 formal mismatches.
- Real S00-S04 path: 5/5 PASS; second run skipped 5/5; stopped before S05.

The O06 output is diagnostic-only and must never be promoted into a scientific
14k stage or checkpoint.

## Tests and runtime

- Local focused: 32 passed.
- Local full: 952 passed, 3 skipped.
- Lambda focused: 32 passed.
- Lambda full: 955 passed.
- GPU at final snapshot: H100 0%, 0 MiB; no EXP-037A formal process.
- Scientific H100 active time in R12B: 0.
- Future 3D-fail: 18h expected, 36h conservative, 16 H100h.
- Future full branch: 32h expected, 64h conservative, 29 H100h.
- Storage: 46/90 GiB expected/conservative.
- Proposed cap: 80h, not approved.

## Evidence roots

- Main R12B diagnostics:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r12b_prompt_profile_repair_20260905_001`.
- Final source-bound S00-S04 smoke:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r12b_s00_s04_14k_20260906_004`.
- Final source-bound whole-pipeline audit:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r12b_whole_pipeline_audit_14k_20260906_005`.
- Final disposable preflight proof:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r12b_14k_preflight_dryrun_20260906_005`.

All raw/large artifacts remain on Lambda. Git contains only redacted,
reconstructible summaries and content-addressed evidence.

## Stop condition

Do not launch 14k without a new exact run-bound user authorization. Do not
resume or migrate 14j artifacts. No follow-on optimization is approved.
