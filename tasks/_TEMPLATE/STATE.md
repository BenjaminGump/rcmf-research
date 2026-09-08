# Dataset Task State

- Base canonical SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`
- Adapter version: `rcmf_reproducible_benchmark_adapter_v2`
- Branch/worktree: set a dedicated `adapt/<dataset>-v1` worktree
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/<dataset>/<uuid>`
- Objective: implement and validate one adapter without changing portable core.
- Verified facts: none until independently checked in the task worktree.
- Unverified facts: package/data/runtime/prompt-token/split/trajectory identities.
- Blockers: source/license/environment/trajectory/split conformance gates.
- Source manifests: list exact versioned manifests here.
- Prompt profile: name and content-addressed source manifest.
- Trajectory provider: implementation and admitted provenance class.
- Next action: smallest bounded environment/data provenance check.
- Latest handoff: portable-v2 canonical handoff in `research/handoffs/`.
- Prohibited: science before preflight/approval, core edits without coordination,
  shared mutable roots, evaluation leakage, unknown provenance, silent fallback.
- Dirty/uncommitted status: verify with `git status --short` before work.
