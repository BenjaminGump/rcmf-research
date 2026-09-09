# ALFWorld State

- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`
- Last verified UTC: `2026-09-09T09:58:34Z`
- RCMF Harness V1 integration source: `4e56702f467635bda120d118a5367c58e593ecef`
- Integration archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Final Harness source: `827ed6f394804834e93444c9bb02c435e9e238a3`
- Readiness branch/source/records: `dataset/alfworld-readiness-v1` /
  `87cf79d4ee47dfc0f74a799605630f9fbbae0f15` /
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`
- Readiness decision: `STOP_ALFWORLD_SPLIT_LEAKAGE`
- Future RCMF branch/worktree: `adapt/alfworld-v1`, based on the final RCMF
  Harness V1 integration records branch in a dedicated worktree
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`
- Adapter protocol: `rcmf_reproducible_benchmark_adapter_v2`; no final ALFWorld
  adapter or scientific benchmark lock exists
- Prompt source/profile: ReAct commit
  `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`,
  `react_task_type_two_demo_v1`
- Trajectory source: official training-game `AlfredExpert` planner replay,
  `OFFICIAL_EXPERT`; six bounded training tasks replayed successfully
- Split/evaluation contract: train only for memory/training; `valid_seen` and
  `valid_unseen` evaluation-only; 4,027 unique task identities were inventoried
- Verified: ALFWorld source/data/environment identity, 3,553 train games,
  140 valid-seen and 134 valid-unseen games, six expert replays, two exact
  deterministic repeats, task/split lineage, prompt provenance
- Unverified: an eligible leakage-free base prompt or approved alternate split
  policy, final adapter, runtime token closure, benchmark lock, scientific run
- Blocker: demonstration `pick_two_obj:0` exactly matches evaluation task
  `alfworld:trial_T20190907_201917_045715`
- Next decision: separately approve a provenance-clean base prompt or revised
  evaluation-split policy, then re-audit demonstrations before adapter work
- Latest handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`
- Approval stop: do not alter prompt/split policy, freeze a benchmark lock, or
  run model generation/training without a separate reviewed task
- Scientific status: `NOT_EVALUATED`
