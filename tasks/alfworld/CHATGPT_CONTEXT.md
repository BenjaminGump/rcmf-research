# ALFWorld ChatGPT Context

- Document role: compact conversation bootstrap; sealed artifacts and source
  remain authoritative.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Last verified UTC: `2026-09-11T02:14:06Z`.
- Latest relevant handoff:
  `research/handoffs/20260911T021406Z_alfworld_action_boundary_pre_model_gate.md`.
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
- Harness Track R v2 source/archive:
  `503493f28b7448396c677fa976c9ab3b4be6a36e` /
  `archive/alfworld-track-r-action-boundary-correction-503493f`.
- Track/role/population:
  `alfworld_upstream_react_valid_unseen_reference_v1` /
  `UPSTREAM_PROTOCOL_REFERENCE` / all 134 official `valid_unseen` tasks.
- Frozen corrected lock identity:
  `5cb89b2d602162044daf8bd14c03b822f405086b09bf77de8c06c2b09ae9a72f`.
- Model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Prompt/chat/generation identities:
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` /
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8` /
  `c86fad4fad8e7fc562a130d72fb58b96b18b7099152293bf776f057ebff5c59f`.

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
rejection. No corrected model execution has started. Wait for WebShop's
explicit empty-H100 handoff, then run only the preregistered matched six-family
TRAIN sanity. If structurally valid, proceed unchanged to the two complete
134-task v2 arms; do not tune from their outcomes.

The final TRAIN source admitted 3,545 replay-validated `OFFICIAL_EXPERT`
trajectories and 21,259 complete transition memories; eight typed environment
reset timeouts were excluded. The terminal checkpoint SHA-256 is
`6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
Qwen remained frozen; no raw memory entered prompts and no runtime retrieval
occurred. Training/checkpoint evidence remains valid and does not need rerun.

## Authority routing

Read, in order:

1. `research/plans/ALFWORLD_TRACK_R_ACTION_BOUNDARY_CORRECTION_PREREGISTRATION.md`.
2. `research/plans/alfworld_track_r_run_identities_v2.json`.
3. `research/results/alfworld/train_sanity_action_boundary_audit.json`.
4. `research/results/alfworld/real_track_r_order_closure.json`.
5. `research/results/alfworld/order_correction_test_report_12d4b1a.json`.
6. `research/results/alfworld/invalid_sorted_order_attempt.json`.
7. `tasks/alfworld/STATE.md`.

The preserved Lambda root is
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
The matched TRAIN sanity and, conditional on structural validation, the two
complete corrective arms and their records are authorized. The exposed invalid
outcomes cannot change any scientific setting. The terminal checkpoint is
unchanged and must not be retrained.
