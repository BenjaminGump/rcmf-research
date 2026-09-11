# ALFWorld Action-Boundary Pre-Model Gate

## Status

`CPU_PRE_MODEL_GATE_COMPLETE_WAITING_FOR_EXPLICIT_EMPTY_H100_HANDOFF`

No corrected model execution has started. WebShop has not been modified,
stopped, or preempted. The next authorized model operation is only the matched
six-family TRAIN sanity declared in
`research/plans/alfworld_track_r_run_identities_v2.json`.

## Verified source and identities

- Branch: `adapt/alfworld-v1`.
- Order repair: `12d4b1ac0ff8d1afb374010dc1a65404ef7ff269`.
- Action-boundary preregistration:
  `87337b28cfb16bd585c11d31d44ff87ebadf9e01`.
- Action-boundary executable source:
  `91598b6d734cc5a5b3d1ccc1d21a7f2fb1bb2741`.
- Source archive: `archive/rcmf-alfworld-action-boundary-correction-91598b6`.
- Harness v2 source/archive:
  `503493f28b7448396c677fa976c9ab3b4be6a36e` /
  `archive/alfworld-track-r-action-boundary-correction-503493f`.
- Harness v2 lock identity: `5cb89b2d602162044daf8bd14c03b822f405086b09bf77de8c06c2b09ae9a72f`.
- Corrected generation identity: `c86fad4fad8e7fc562a130d72fb58b96b18b7099152293bf776f057ebff5c59f`.
- Unchanged model revision: `b968826d9c46dd6066d109eabc6255188de91218`.
- Unchanged checkpoint: `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.

## TRAIN diagnosis

The preserved bare and RCMF logs each contain 294 steps over the same six
TRAIN tasks. Every first decoded line and actual environment command retained
the ReAct transcript marker, and every environment observation was `Nothing
happens.`. The non-empty validator therefore did not establish semantic
validity. Output/repetition/category counts and representative raw evidence are
sealed in `research/results/alfworld/train_sanity_action_boundary_audit.json`.

On an identical reset, `> go to fridge 1` was inadmissible and returned
`Nothing happens.`, while `go to fridge 1` was admissible and returned the real
closed-fridge observation. Replaying only the already-generated first lines
after one-marker normalization recovered substantive feedback on 53/62 bare
and 50/62 RCMF non-think steps. Those replay rows are not model results and do
not support any performance claim.

## Real 134-task order and identity closure

CPU validation used the actual 134-task manifest, not a fixture. The correct
set plus `c49e3fab...` order passed; the same set in wrong order failed; reversed
real adapter input sorted exactly to `c49e3fab...`; and wrong embedded order,
lock file, or lock identity failed. See
`research/results/alfworld/real_track_r_order_closure.json`.

## Test-source accounting

- Historical executable `e9c2f3f`: 1,111 passed, three skipped.
- Order repair `12d4b1a`: 1,113 passed, three skipped; 22 focused passed locally
  and on Lambda.
- Later action-boundary source `91598b6`: 1,116 passed, three skipped; 25
  focused passed locally and on Lambda.

The first `91598b6` full-suite attempt omitted process-start
`PYTHONHASHSEED=25101` and was rejected by the existing AppWorld 13c guard at
collection. The unchanged suite passed after the environment was set; no
failure was waived.

## Next execution gate

1. Verify both repositories are pushed/clean and the Lambda source is exact.
2. Inspect GPU processes read-only. Do not kill or change WebShop.
3. Wait for explicit empty-H100 handoff if WebShop still owns the GPU.
4. Run the two preregistered TRAIN sanity identities over the same fixed six
   tasks, with no training and no frozen-identity change.
5. Audit raw text, decoded line, parsed/executed action, observation, progress,
   done/won/evaluator, and result identity.
6. If structurally valid, run both complete 134-task arms under the v2 lock.
   Do not tune from `valid_unseen` outcomes.

## Negative statements

- No unaffected model was retrained.
- No corrected full evaluation was started.
- No prompt/model/checkpoint identity changed.
- No WebShop process was modified.
- No scientific result is claimed from the invalid or TRAIN diagnostic rows.
