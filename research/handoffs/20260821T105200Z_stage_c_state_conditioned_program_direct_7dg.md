# Structured Handoff: EXP-025D-Direct

## Identity

- Run UUID: `state_conditioned_program_direct_7dg_20260821_001`
- Global seed: `25101`
- Starting SHA: `343c0a3b91df9b810edeac65736dc954ea7960b1`
- Archive: `archive/v4-latent-program-distillation-failed`
- Working branch: `research/v4-direct-behavior-program`
- Execution source SHA: `673b254d1429bdd427d1b44597741d8130121c00`
- Final record SHA: commit containing this handoff
- Artifact: `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_direct_7dg_20260821_001`

## Recovery and attempts

The append-only scientific ledger contains four attempts: successful
`exp025dg-preflight-002`; `exp025dg-direct-001`, which completed the 970-row
teacher cache and then stopped before training on a tokenized-row
`transition_id` lookup bug; resumed `exp025dg-direct-002`, which validated and
reused the exact teacher cache and completed the scientific branch; and
successful CPU `exp025dg-finalize-001`. An earlier config-hash typo was rejected
before the attempt ledger and is preserved by the immutable manifest
supersession record. No scientific parameter, row, seed, or update count
changed during recovery.

## Exact contract

- A/B/C/D/E scoreable pairs: `607/135/112/112/135`.
- A train/validation: `479/128` pairs, `29/8` disjoint query tasks.
- Teacher rows: `970` unique, `132` reused, `838` new.
- Frozen selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`.
- Clean decoder SHA256:
  `71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.
- Qwen frozen, no raw transition in student prompt, K=4, unchanged demos.
- GPU-attempt/wall-span time: `8.7561/8.8217` hours.
- Artifact size before finalization: `1,894,481,569` bytes.

## Verified science

The old latent PairMLP validation cosine was `0.214108` with only `0.000180`
MSE reduction versus zero; no implementation/split bug was found.

Direct PairMLP selected u8 and passed. A/B/C/D/E Spearman was
`0.570923/0.386353/0.478483/0.406490/0.395259`; Huber reduction versus zero
was `33.52%/23.62%/28.70%/27.04%/22.67%`. Required state/transition shuffle
gaps were positive.

Direct factorized selected u16. A/B/C/D/E Spearman was
`0.407772/0.294679/0.441162/0.418466/0.298059`, but Huber reduction was
`+3.27%/-16.67%/-10.77%/-27.72%/-22.57%`. B and E therefore failed the
positive-Huber gates; E also failed the memory-swap contrast. Ratio,
observation-invariance, and selector-integrity checks passed.

## Decision and boundary

Decision branch: `direct_behavior_factorized_program_failed`.

Direct behavior repairs the PairMLP upper bound, so the old result was not a
general representation-information failure. The current field-compatible r16
factorization nonetheless fails calibrated held-out behavior. No H1-H4
generation or AppWorld execution was run, and compiled-program behavior was
not validated.

p(s,m_transition), full-bank/program-compiler work, injector training,
Stage C2, end-to-end RCMF, full AppWorld evaluation, and V4 tagging remain
blocked. The recommended next action is immediate project-scope review: either
authorize one narrow factorization/calibration repair under the deadline, or
freeze the validated selector/raw-transition causal contribution and report
compiled-program amortization as a bounded negative result.

At handoff there is no `exp025dg` tmux session, no experiment Python process,
and GPU utilization/memory are zero. The run is finalized and safe to
terminate.
