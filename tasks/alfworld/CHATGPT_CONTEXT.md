# ALFWorld ChatGPT Context

Current status (2026-09-11): action-dialect-v3 Track R is complete and
eligible. Exact full bare and RCMF-C runs both completed all 134 tasks in frozen
manifest order with 45 official successes and zero typed failures. Strict
paired analysis found 40 both-correct, 84 both-wrong, five gains, five losses,
absolute accuracy delta 0, paired bootstrap 95% CI [-0.0447761194,
0.0447761194], and exact McNemar p=1.0. Complete action/evaluator audits found
zero violations, and regenerated P00-P11 evidence passed 12/12. Decision:
`COMPLETE_ELIGIBLE_TRACK_R_PAIRED_RESULT_NO_OBSERVED_RCMF_IMPROVEMENT`. Do not
retune or rerun from this validation result.

- Document role: compact conversation bootstrap; sealed artifacts and source
  remain authoritative.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Last verified UTC: `2026-09-11T18:54:00Z`.
- Latest relevant handoff: `research/handoffs/20260911T190000Z_alfworld_action_dialect_v3_track_r_final.md`.
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
result exists. That sentence applies only to the frozen Portable V2.1 base
milestone; it is retained verbatim for the generic release-document contract
and is superseded on this later dataset branch by the eligible v3 Track R
result documented above.

Historical invalid attempts remain preserved and ineligible: the first pair
used sorted task-ID order, the action-boundary-v2 bare run retained an upstream
action-dialect mismatch, and neither result is used scientifically. The v3
repair was fixed prospectively from pinned ReAct/ALFWorld source plus TRAIN
same-state probes, then validated by the first-30 gate before either eligible
full arm.

The eligible bare UUID `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47` and matched
RCMF-C UUID `fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23` each completed all 134
official `valid_unseen` tasks in exact physical manifest order. Both used
source `37e3ad1`, Qwen revision `b968826d...`, generation/action identity
`80400891...`, v3 lock identity `f6103813...`, batch size 16, and the unchanged
official terminal evaluator. RCMF alone used the previously trained terminal
checkpoint SHA-256 `6e03514d...`; it was not retrained.

Bare and RCMF each achieved 45/134 with zero typed failures. The strict paired
result has 40 both-correct, 84 both-wrong, five gains and five losses. Absolute
accuracy delta is zero; the 100,000-replicate seed-25,101 paired bootstrap 95%
CI is [-0.0447761194, 0.0447761194], and exact two-sided McNemar p=1.0. This is
no observed RCMF improvement, while the uncertainty interval does not prove
equivalence.

The RCMF CPU audit reconstructed all 5,483 steps, including raw output, first
decoded line, parsed model action, exact translated/pass-through environment
command, returned observation, terminal done/won, episode hash, and evaluator.
It found zero violations and zero matched ReAct `put ... in/on ...` commands
sent literally. Descriptive diagnostics include 2,967 think actions, 713
consecutive repeated commands, 3,644 `Nothing happens.` responses, and 1,839
nonempty non-rejection observations; these selected no setting. Strict paired
analysis and the regenerated P00-P11 graph also passed.

The final TRAIN source remains 3,545 replay-validated `OFFICIAL_EXPERT`
trajectories and 21,259 complete transition memories, with eight typed reset
timeouts excluded. No raw memory entered prompts, no runtime retrieval
occurred, and Qwen remained frozen. This corrective milestone is closed; a
future experiment requires a separate prospective charter and cannot tune from
this Track R outcome.

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
The v3 source, both eligible full arms, complete RCMF action/evaluator audit,
strict paired analysis, and P00-P11 closure pass. No further ALFWorld run is
authorized by this milestone. The exposed valid and invalid outcomes cannot
change any scientific setting; the terminal checkpoint remains unchanged.
