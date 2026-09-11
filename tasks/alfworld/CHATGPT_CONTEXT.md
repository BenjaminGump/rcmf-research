# ALFWorld ChatGPT Context

- Document role: compact conversation bootstrap; sealed artifacts and source
  remain authoritative.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Last verified UTC: `2026-09-10T22:05:03Z`.
- Latest relevant handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`.
- Branch: `adapt/alfworld-v1`.
- Starting records: `3560ec75f96575ce87fd06a8eb5c4b4d596615c8`.
- Charter/preregistration/executable source:
  `154fd80748b977811d473ff3aa0990bad951283a` /
  `efe9f8a0e9002c852804faabb303f45ef14fc876` /
  `e9c2f3faf59fec243589eb5e9ba8424067fa677e`.
- Source archive: `archive/rcmf-alfworld-track-r-full-134-e9c2f3f`.
- Harness Track R source/archive:
  `4fa9274eaf55d066eb85bf828f76cea4f40c3dbb` /
  `archive/alfworld-track-r-execution-lock-v1-4fa9274`.
- Track/role/population:
  `alfworld_upstream_react_valid_unseen_reference_v1` /
  `UPSTREAM_PROTOCOL_REFERENCE` / all 134 official `valid_unseen` tasks.
- Frozen lock identity:
  `055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
- Model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Prompt/chat/generation identities:
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` /
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8` /
  `6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9`.

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
outcome-independent repair must enforce the original order, fail closed in the
runner and analyzer, and rerun both complete arms under new identities.

The final TRAIN source admitted 3,545 replay-validated `OFFICIAL_EXPERT`
trajectories and 21,259 complete transition memories; eight typed environment
reset timeouts were excluded. The terminal checkpoint SHA-256 is
`6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
Qwen remained frozen; no raw memory entered prompts and no runtime retrieval
occurred. Training/checkpoint evidence remains valid and does not need rerun.

## Authority routing

Read, in order:

1. `research/plans/ALFWORLD_TRACK_R_ORDER_CORRECTION_PREREGISTRATION.md`.
2. `research/results/alfworld/invalid_sorted_order_attempt.json`.
3. `tasks/alfworld/STATE.md`.

The preserved Lambda root is
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
Only the two complete corrective arms and their records are authorized. The
exposed invalid outcomes cannot change any scientific setting.
