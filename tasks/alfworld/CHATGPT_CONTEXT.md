# ALFWorld ChatGPT Context

Current status (2026-09-11): action-dialect-v3 executable source `37e3ad1` is
archived and its TRAIN/CPU pre-model gates pass. The actual RCMF adapter and an
independent Harness provider both translate only exact upstream ReAct `put ...
in/on ...` to deployed ALFWorld `move ... to ...`; all five fixed placement
TRAIN cases match official action/observation/reward/done/won and terminate in
success. The real 134-task order/embedded-v3 identity closure also passes.
RCMF-C has not started and must not start. The next model run is only the exact
first 30 frozen-order bare tasks under UUID
`de0e3118-e852-4709-8887-304620c27cc6`. Require the preregistered structural
closure, >13/30 total success, and >=1/12 `pick_and_place` success before a new
full-134 bare run. Do not retrain or performance-search.

- Document role: compact conversation bootstrap; sealed artifacts and source
  remain authoritative.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Last verified UTC: `2026-09-11T02:14:06Z`.
- Latest relevant handoff: `research/handoffs/20260911T113200Z_alfworld_action_dialect_v3_pre_first30_gate.md`.
- Branch: `adapt/alfworld-v1`.
- Starting records: `3560ec75f96575ce87fd06a8eb5c4b4d596615c8`.
- Charter/original preregistration/invalid-attempt executable source:
  `154fd80748b977811d473ff3aa0990bad951283a` /
  `efe9f8a0e9002c852804faabb303f45ef14fc876` /
  `e9c2f3faf59fec243589eb5e9ba8424067fa677e`.
- Source archive: `archive/rcmf-alfworld-track-r-full-134-e9c2f3f`.
- Order correction source/archive:
  `12d4b1ac0ff8d1afb374010dc1a65404ef7ff269` /
  `archive/rcmf-alfworld-track-r-order-correction-12d4b1a`.
- Action-boundary preregistration/source/archive:
  `87337b28cfb16bd585c11d31d44ff87ebadf9e01` /
  `91598b6d734cc5a5b3d1ccc1d21a7f2fb1bb2741` /
  `archive/rcmf-alfworld-action-boundary-correction-91598b6`.
- Action-dialect preregistration/source/archive:
  `42698dd` / `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094` /
  `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
- Harness Track R v2 source/archive:
  `503493f28b7448396c677fa976c9ab3b4be6a36e` /
  `archive/alfworld-track-r-action-boundary-correction-503493f`.
- Track/role/population:
  `alfworld_upstream_react_valid_unseen_reference_v1` /
  `UPSTREAM_PROTOCOL_REFERENCE` / all 134 official `valid_unseen` tasks.
- Frozen action-dialect-v3 lock identity:
  `f6103813cd2f4c76d15e2784c2eece9758c250042ef3c06d01ccde89daa4ba05`.
- Model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Prompt/chat/generation identities:
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` /
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8` /
  `80400891d46bc0395961b978449d03ae709d50d93d39e8d665ce9560a0224705`.

Independently verify the latest pushed GitHub branch, archive, task state, and
sealed result identities before treating this cache as current.

## Authority Order

1. Sealed primary artifacts and source code.
2. The canonical version manifest and `docs/PIPELINE.md`.
3. `tasks/alfworld/STATE.md`.
4. `docs/HISTORY.md` and `docs/FAILURE_MODES.md`.
5. Conversation memory.

## Documents To Read

- `AGENTS.md`
- `docs/CHATGPT_ENTRYPOINT.md`
- `docs/PIPELINE.md`
- `docs/ADAPTER_CONTRACT.md`
- `docs/SCIENTIFIC_STATUS.md`
- `tasks/alfworld/STATE.md`
- `tasks/alfworld/ADAPTATION_BRIEF.md`

## Current corrective state

Historical Portable V2.1 bootstrap marker: No scientific ALFWorld RCMF
result exists. That sentence applies to the frozen base milestone; the current
branch has subsequently run invalidated attempts but still has no eligible
ALFWorld scientific result pending the corrective rerun.

The first matched 134+134 outputs are invalid because the runtime used sorted
task-ID order (`2410f2c2...`) rather than the frozen manifest order
(`c49e3fab...`). They completed without typed failures and are preserved, but
their `0/134` versus `0/134` outcome is not a scientific result. The
outcome-independent order repair enforces the original order and fails closed
in the runner and analyzer.

A mandatory pre-rerun TRAIN audit then proved an independent action-boundary
defect: all 588 preserved sanity commands retained a leading ReAct transcript
marker, all 588 environment replies were `Nothing happens.`, and the existing
`action_valid=true` flag established only non-emptiness. A same-reset TRAIN
probe made `> go to fridge 1` fail and `go to fridge 1` produce the real
closed-fridge observation. The v2 contract removes at most one leading marker,
rejects empty output, preserves the remainder opaquely, and evaluates `think:`
after normalization. No prompt, model, checkpoint, or evaluation outcome
selected this correction.

The real 134-task CPU closure passes correct order, wrong-order rejection,
reversed-input restoration to `c49e3fab...`, and embedded order/lock identity
rejection. WebShop explicitly returned an empty H100. The preregistered matched
six-family TRAIN sanity then completed 6/6 tasks and 294 steps per condition
with zero typed failures. Every step applied the exact one-marker boundary, no
executed command retained the marker, and all six families produced substantive
state observations in both conditions. The 0/6 terminal outcome in each sanity
is not a defect threshold and changed no setting. The complete bare 134-task v2
arm later completed with 16/134 and zero typed failures, but its action audit
proved that every one of 549 ReAct `put ... in/on ...` commands was submitted
literally to an ALFWorld grammar that accepts `move ... to ...`; all 549
returned `Nothing happens.`. This v2 arm is preserved as
`INVALID_ACTION_DIALECT_MISMATCH`.

A user-directed safety gate was added during the bare run. On completion, do
not automatically start RCMF-C: audit the complete input/output/action/
environment/evaluator chain first. If all 134 tasks are wrong or a clear basic
interaction, parsing, or evaluator fault is found, diagnose and repair that
fault without changing frozen identities or retraining. The next corrective
bare retest must then use only the first 30 tasks in the unchanged frozen
manifest order; complete all 134 and consider RCMF-C only after the diagnosed
fault is repaired and the short retest improves. Thirteen official successes
already existed in the first 18 rows when this rule was recorded, so the
all-wrong branch cannot trigger for the active arm; the full structural audit
is still mandatory. No unspecified performance threshold or valid-unseen
prompt/model/config search is authorized. Authority:
`research/plans/alfworld_bare_postrun_safety_gate.json`.

Pinned ReAct source, pinned ALFWorld grammar, and fresh-reset same-state TRAIN
probes independently established the action-dialect defect without selecting a
repair from validation outcomes. Action-dialect-v3 implements only the exact
anchored `put <object> <id> in/on <receptacle> <id>` to `move <object> <id> to
<receptacle> <id>` bridge. The actual RCMF adapter and the Harness-owned provider
both pass all five fixed placement TRAIN cases with exact official outcomes.
The real 134-task CPU order/embedded-v3 closure and source tests also pass.
Run identities are sealed before new model output in
`research/plans/alfworld_track_r_run_identities_v3.json`. The next run is only
the first 30 bare tasks; full 134 and RCMF-C remain conditionally blocked.

The final TRAIN source admitted 3,545 replay-validated `OFFICIAL_EXPERT`
trajectories and 21,259 complete transition memories; eight typed environment
reset timeouts were excluded. The terminal checkpoint SHA-256 is
`6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
Qwen remained frozen; no raw memory entered prompts and no runtime retrieval
occurred. Training/checkpoint evidence remains valid and does not need rerun.

## Authority routing

Read, in order:

1. `research/plans/ALFWORLD_TRACK_R_ACTION_DIALECT_CORRECTION_PREREGISTRATION.md`.
2. `research/plans/alfworld_track_r_run_identities_v3.json`.
3. `research/results/alfworld/action_dialect_v3_test_report.json`.
4. `research/results/alfworld/action_dialect_v3_rcmf_adapter_train_probe.json`.
5. `research/results/alfworld/action_dialect_v3_real_track_r_order_closure.json`.
6. `research/results/alfworld/bare_postrun_audit_action_v2.json`.
7. `research/results/alfworld/react_put_move_train_probe.json`.
8. `research/plans/alfworld_bare_postrun_safety_gate.json`.
9. `tasks/alfworld/STATE.md`.

The preserved Lambda root is
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
The v3 source, real TRAIN adapter, independent Harness TRAIN provider, and real
134-task CPU identity/order gates pass. Only the preregistered first-30 bare arm
may start. RCMF-C remains blocked until the first-30 improvement gate and later
full-134 bare structural audit both pass. The exposed invalid outcomes cannot
change any scientific setting. The terminal checkpoint is unchanged and must
not be retrained.
