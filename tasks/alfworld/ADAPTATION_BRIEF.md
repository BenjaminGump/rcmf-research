# ALFWorld Adaptation Brief

The ALFWorld Track R adaptation and training are complete, but the first
134-task matched execution is invalid: it used sorted task-ID order rather than
the manifest order frozen in the execution lock. The exact 134 tasks and all
other identities matched; both arms must be rerun under the outcome-independent
order repair. Source `e9c2f3faf59fec243589eb5e9ba8424067fa677e`
and archive `archive/rcmf-alfworld-track-r-full-134-e9c2f3f` remain preserved as
the pre-repair source.

The exact Harness authority is source
`4fa9274eaf55d066eb85bf828f76cea4f40c3dbb` on
`dataset/alfworld-track-r-execution-lock-v1`, archive
`archive/alfworld-track-r-execution-lock-v1-4fa9274`, and execution-lock
identity `055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
Track R remains `UPSTREAM_PROTOCOL_REFERENCE`; it is not Track S.

The final TRAIN corpus contains 3,545 successful replay-validated
`OFFICIAL_EXPERT` trajectories and 21,259 complete transition memories. Eight
training games remain typed reset timeouts and were not admitted. The compact
RCMF field and checkpoint pass independent-write, reversibility, fixed-shape,
frozen-Qwen, no-retrieval, no-raw-memory-prompt, and TRAIN-only checks.

The invalid first attempts were:

- Bare frozen Qwen: `0/134` official successes, zero typed failures.
- RCMF: `0/134` official successes, zero typed failures.
- Paired delta: `0.0`; gains `0`, losses `0`, both correct `0`, both wrong
  `134`; paired bootstrap 95% CI `[0.0, 0.0]`; exact McNemar `p=1.0`.
- Classification: `INVALID_EXECUTION_ORDER_MISMATCH`; no scientific conclusion.

All 134 tasks ran in each invalid arm. No task was removed, no evaluation
outcome is permitted to tune the repair or method, Qwen stayed frozen, and no
runtime memory retrieval or raw-memory prompt text was used. Training and
checkpoint validation remain valid.

Corrective review starts at
`research/plans/ALFWORLD_TRACK_R_ORDER_CORRECTION_PREREGISTRATION.md`. Large
invalid-attempt and training artifacts remain at
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`
and are bound by paths, counts, and hashes in the Git records.

Do not retune using the exposed invalid outcomes. Rerun only the unchanged bare
and RCMF arms in the originally frozen manifest order, then regenerate paired
analysis and P00-P11 closure.
