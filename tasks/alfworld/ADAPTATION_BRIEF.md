# ALFWorld Adaptation Brief

- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`
- Portable V2.1 source: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Final RCMF Harness V1 integration source:
  `4e56702f467635bda120d118a5367c58e593ecef`
- Integration archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Final Harness source: `827ed6f394804834e93444c9bb02c435e9e238a3`
- ALFWorld readiness source/records: `87cf79d4ee47dfc0f74a799605630f9fbbae0f15` /
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`
- Current decision: `STOP_ALFWORLD_SPLIT_LEAKAGE`
- Last verified UTC: `2026-09-09T09:58:34Z`

Start from the final RCMF Harness V1 integration records branch in a dedicated
`adapt/alfworld-v1` worktree. Read `AGENTS.md`, `docs/PIPELINE.md`,
`docs/ADAPTER_CONTRACT.md`, `tasks/alfworld/STATE.md`, and the exact readiness
records before editing. Preserve the Portable V2.1 core and Final Harness lock.

Environment/data/task/expert-replay readiness is already engineering-verified:
the official ALFWorld 0.5.0/TextWorld corpus has 3,553 train, 200 valid-train,
140 valid-seen, and 134 valid-unseen games. Six official training expert plans
replayed successfully and two deterministic repeats matched exactly. These are
readiness facts, not a scientific RCMF result.

The blocking fact is exact: pinned ReAct demonstration `pick_two_obj:0`
matches evaluation task `alfworld:trial_T20190907_201917_045715`. Do not build
an adapter or benchmark lock around this profile. The next bounded task is a
separate fairness decision between a provenance-clean prompt and an explicitly
justified evaluation-split revision, followed by a complete demonstration
leakage audit. Do not decide that policy in this shared integration base.

After that gate passes, implement only the ALFWorld adapter, dataset profile,
prompt assets, task records, and tests. Keep official expert training trajectories
`OFFICIAL_EXPERT`; validation splits stay evaluation-only. Stop for review
before changing prompt/split semantics, freezing a benchmark lock, running
Qwen/training, or any execution plausibly exceeding 18 hours.
