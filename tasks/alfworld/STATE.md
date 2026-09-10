# ALFWorld End-to-End Adaptation State

- Status: `ACTIVE_END_TO_END_ADAPTATION`
- Effective UTC date: `2026-09-10`
- Branch: `adapt/alfworld-v1`
- Development base records SHA:
  `3560ec75f96575ce87fd06a8eb5c4b4d596615c8`
- Direct executable parent:
  `4e56702f467635bda120d118a5367c58e593ecef`
- Canonical Portable V2.1 ancestor:
  `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Harness branch/source/records:
  `dataset/alfworld-model-context-resolution-v1` /
  `a516de0a6f223732f6b1203c05de4ca82a744f2d` /
  `88b6d1de6d8df787a769d8eb55cc10bd69493d9e`
- Harness decision: `READY_FOR_BOUNDED_BARE_AGENT_SANITY_DESIGN`; the new
  end-to-end authorization removes that conversational stop boundary.
- Required endpoint: matched bare frozen-Qwen and RCMF evaluation on exact
  Track R `alfworld_upstream_react_valid_unseen_reference_v1`, all 134 official
  `valid_unseen` tasks, with no task filtering or outcome-led tuning.
- Frozen model: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`, bfloat16, frozen backbone,
  thinking disabled.
- Frozen chat-template SHA-256:
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`.
- Frozen generation identity:
  `6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9`;
  effective context 40,960, greedy, 512 new-token reserve, first decoded line,
  and 49-action cap.
- Track R tokenizer audit: 134/134 passed with no truncation, overflow, task
  change, or failure.
- Environment/data inventory remains 3,553 train, 200 `valid_train`, 140
  `valid_seen`, and 134 `valid_unseen`, with task-manifest SHA-256
  `ff7a9f5ea60028a608470c6fa088a5e82768f64033fb2f74804849744f3cf6f9`.
- Trajectory provenance remains official ALFWorld planner replay through the
  TextWorld deployment interface, `OFFICIAL_EXPERT`; final corpus admits TRAIN
  successes only.
- Local editing/static-test worktree:
  `C:/gbz/worktrees/rcmf-alfworld-v1`.
- Required Lambda worktree:
  `/lambda/nfs/rcmf-persist/project-worktrees/alfworld-v1`.
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`.
- Diagnostic/run UUID:
  `f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
- Task-owned Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
- Lambda preflight verified host `192-222-53-194`, one idle NVIDIA H100 80GB,
  Python 3.11.15, torch 2.11.0+cu128, transformers 4.57.6, tokenizers 0.22.2,
  CUDA runtime 12.8, and the exact complete frozen Qwen snapshot in the
  existing read-only Hugging Face cache.
- Exact sealed ALFWorld 2.1.1 archives were copied (not redownloaded) into the
  task root, re-hashed, extracted, and independently rebuilt to the exact
  4,027-row manifest identity above. The pinned ALFWorld source commit and
  task-owned Python dependencies are installed without changing system state.
- Current implementation state: real ALFWorld Portable V2.1 adapter boundary,
  TextWorld runtime, official-expert provider, corpus builder, prompt renderer,
  dataset profile, executor binding, and focused tests implemented locally;
  Lambda real-runtime validation and remaining corpus/module/scientific stages
  are in progress.
- Compute gate: request explicit run-bound approval only if a single necessary
  scientific run could plausibly exceed 18 wall-clock hours after measurement.
- Scientific status: `NOT_EVALUATED`.

---

# Historical ALFWorld State Before 2026-09-10 Scope Transition

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
