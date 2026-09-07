# EXP-037A 14k O08 Terminal Failure Handoff

## Status

- Decision: `INFRASTRUCTURE_IMPLEMENTATION_FAILURE`.
- Root cause: `VERIFIED_STALE_THREE_DEMO_SCOREABLE_COUNT_CONTRACT`.
- Three-demo: `THREE_DEMO_REPRODUCTION_PASS`.
- Complete one-demo and cross-arm result: `NOT_EVALUATED`.
- Launch source: `004f866647cfabb38a141b88e6d83821df88c403`.
- Records base: `31ffa6ad745f26abbb099dfed1b32fcc08575bd7`.
- Failure-record branch: `research/v6-rcmf-exp037a-14k-o08-failure-records`.
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.

## Failure

O08 failed in `prepare_rcmf_joint_full_bank_9a.py:611`, before its
preflight/smoke/zero-cache phases. The fresh one-demo paired panel was compared
against inherited historical 3D scoreable counts 366/98. Task and memory checks
passed; both scoreable-count checks failed. Exit code was 65 and the attempt is
fatal/nonrecoverable under the frozen contract.

There were 45 attempts: 44 complete, 0 open, 1 failed. O07 is the latest valid
stage. O08 backward and optimizer-step counts are both zero.

## Preserved evidence

D06B, D08B, D09/D10, and D22 passed. Fresh 3D dev remains 12/17/11
bare/correct/shuffle. O00-O07 completed. O08+ and F00-F03 did not complete.
The H100 is idle and the formal parent/tmux are gone. The 14k root remains
immutable.

## Review action

Do not resume 14k. The next reviewed repair should keep exact 366/98 validation
for 3D, but make 1D count ownership derive from its own strict O06 output,
together with split, O06/O07 ID, panel-completion, and fixed 401/98 memory
checks. That requires a new source, package, root, and user authorization.

No production repair, retry, resume, test, training, or new run occurred while
publishing this handoff.
