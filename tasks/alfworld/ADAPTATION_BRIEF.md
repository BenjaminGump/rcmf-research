# ALFWorld Adaptation Brief

ALFWorld action-dialect-v3 Track R is complete and scientifically eligible.
The matched full bare and RCMF-C arms each ran all 134 official `valid_unseen`
tasks in the frozen physical manifest order. Both produced 45 official
successes and zero typed failures. Strict paired analysis found 40
both-correct, 84 both-wrong, five gains, five losses, absolute accuracy delta
0, a 100,000-replicate paired bootstrap 95% CI of [-0.0447761194,
0.0447761194], and exact two-sided McNemar p=1.0.

Decision:
`COMPLETE_ELIGIBLE_TRACK_R_PAIRED_RESULT_NO_OBSERVED_RCMF_IMPROVEMENT`.
The point estimate shows no RCMF advantage; the interval does not establish
equivalence. No validation outcome selected a prompt, parser, model, checkpoint,
task, threshold, or other execution setting.

The exact source is `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`,
archived at `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
Harness source is `ef31ccba6092e9b7dc9f0a9c5f5310c6e20ccc74`, archived
at `archive/alfworld-action-dialect-correction-ef31ccb`. Frozen identities are:

- Track/role: `alfworld_upstream_react_valid_unseen_reference_v1` /
  `UPSTREAM_PROTOCOL_REFERENCE`.
- Task set/order SHA-256: `2410f2c2...` / `c49e3fab...`.
- Qwen revision: `b968826d9c46dd6066d109eabc6255188de91218`.
- V3 lock file/identity SHA-256: `8917fa5356...` / `f6103813cd...`.
- Generation/action SHA-256: `80400891d4...`.
- Bridge: `react_put_in_on_to_alfworld_move_to_v1`.
- RCMF checkpoint SHA-256: `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.

The bare UUID is `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47`; RCMF-C UUID is
`fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23`; paired/P00-P11 UUID is
`2a1acdf5-7841-43e9-9f27-52f7f8b4a6d5`. Bare and RCMF action/evaluator audits
checked 5,472 and 5,483 steps with zero violations. RCMF recorded 33 exact
bridge activations and zero matched ReAct puts sent literally. Regenerated
P00-P11 evidence passed 12/12.

The final TRAIN corpus remains 3,545 replay-validated `OFFICIAL_EXPERT`
trajectories and 21,259 complete transition memories; eight typed reset
timeouts were excluded. Qwen remained frozen. No raw memory entered prompts,
no runtime retrieval occurred, and the existing checkpoint was not retrained.

The original sorted-order attempts and action-boundary-v2 result remain
preserved as invalid historical evidence. They did not influence v3 settings.
Large raw episode files remain on Lambda and are bound by path, size, and hash
in `research/results/alfworld/action_dialect_v3_final_artifact_index.json`.
Compact summaries, the full per-task paired result, audits, P00-P11 closure,
and the structured handoff are committed in Git.

This milestone authorizes no further ALFWorld run or post-outcome tuning. Any
future method or diagnostic must begin with a separate prospective charter.
