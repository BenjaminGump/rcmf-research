# ALFWorld State

- Base canonical SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`
- Adapter version: `alfworld:portable-v1-pending` against
  `rcmf_reproducible_benchmark_adapter_v2`
- Branch/worktree: future `adapt/alfworld-v1` in a dedicated worktree
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`
- Objective: adapt portable RCMF to the official ALFWorld TextWorld interface.
- Verified: upstream repository commit
  `aaba6870f86c5be6a08a491f32a50b906227bc3e`; ReAct prompt commit
  `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`; exact prompt asset/hash and
  task-family renderer tests.
- Unverified: installed package/data version, game inventory, official expert
  replay API, TextWorld observations, split hashes, tokenizer counts,
  deterministic replay, scientific behavior.
- Current blockers: no sealed environment/data/trajectory manifest.
- Source manifests: `configs/datasets/alfworld_v1.yaml` and
  `assets/prompts/source_manifests/react_alfworld.json`.
- Prompt profile: `react_task_type_two_demo_v1`, exact task-family examples `_1`
  then `_0`; no scientific selection.
- Trajectory provider: planned `ALFWorldOfficialExpertTrajectoryProvider`,
  `OFFICIAL_EXPERT`, training games only.
- Split/evaluation: train supplies memory/training; valid seen and valid unseen
  remain evaluation-only.
- Completed checks: prompt provenance/hash/action-delimiter renderer; portable
  schema and ALFWorld-like reset/replay conformance.
- Unresolved checks: environment install, official expert textual replay,
  successful corpus, leakage, runtime token equivalence, bare smoke, runtime.
- Next action: bounded environment/data inspection and replay of a few training
  games across task types; no Qwen or training.
- Latest handoff: portable-v2 canonical handoff in `research/handoffs/`.
- Approval stop: any scientific generation/training, large data installation,
  or run plausibly over 18 hours needs review/explicit authorization.
- Prohibited: THOR low actions treated as text without replay, evaluation data
  in corpus, unknown provenance, copied AppWorld stages, shared mutable roots.
- Dirty/uncommitted status: verify independently before creating the worktree.
