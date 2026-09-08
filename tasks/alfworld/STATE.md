# ALFWorld State

- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`
- Bootstrap generated at SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Last verified UTC: `2026-09-08T17:33:33Z`
- Base canonical SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Adapter version: `alfworld:portable-v2_1-pending` against
  `rcmf_reproducible_benchmark_adapter_v2`
- Branch/worktree: future `adapt/alfworld-v2_1` in a dedicated worktree
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
- Latest handoff: `research/handoffs/20260908T173333Z_rcmf_portable_v2_1_m1.md`.
- Approval stop: any scientific generation/training, large data installation,
  or run plausibly over 18 hours needs review/explicit authorization.
- Prohibited: THOR low actions treated as text without replay, evaluation data
  in corpus, unknown provenance, copied AppWorld stages, shared mutable roots.
- Dirty/uncommitted status: verify independently before creating the worktree.
