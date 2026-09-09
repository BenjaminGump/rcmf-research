# ALFWorld ChatGPT Context

- Document role: ALFWorld conversation bootstrap cache; not a source of truth.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Generated-from commit: `4e56702f467635bda120d118a5367c58e593ecef`.
- Canonical source SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- RCMF Harness V1 integration source SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Final Harness source SHA: `827ed6f394804834e93444c9bb02c435e9e238a3`.
- Last verified UTC: `2026-09-09T09:58:34Z`.
- Readiness branch/source/records: `dataset/alfworld-readiness-v1` /
  `87cf79d4ee47dfc0f74a799605630f9fbbae0f15` /
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`.
- Adapter version: `alfworld:portable-v2_1-pending` under
  `rcmf_reproducible_benchmark_adapter_v2`.
- Prompt source/profile: ReAct `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9` /
  `react_task_type_two_demo_v1`.
- Trajectory source/provenance: official training-game `AlfredExpert` planner
  replay / `OFFICIAL_EXPERT`.
- Split/evaluation contract: train is memory/training; valid-seen and
  valid-unseen are evaluation-only.
- Branch/worktree/run namespace: future `adapt/alfworld-v1`, dedicated
  worktree, `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`.
- Latest relevant handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`.

Independently verify the latest pushed GitHub state, archive, Final Harness
lock, readiness records, and task state before treating this cache as current.

## Authority Order

1. Sealed primary artifacts and source code at the specified commit.
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
- `docs/datasets/ALFWORLD_READINESS.md`
- `tasks/alfworld/STATE.md`
- `tasks/alfworld/ADAPTATION_BRIEF.md`

## State

Verified: Final Harness V1 and the RCMF integration lock pass; ALFWorld
environment/data inventory and official expert replay are engineering-verified;
six train tasks replayed successfully and two repeated exactly. The pinned
ReAct profile `react_task_type_two_demo_v1` contains one exact evaluation-task
collision, so readiness is `STOP_ALFWORLD_SPLIT_LEAKAGE`.

Unverified: an approved leakage-free prompt/split contract, final adapter,
runtime token closure, benchmark lock, and all scientific results. No scientific
ALFWorld RCMF result exists.

Current blocker/next decision: separately review either a provenance-clean base
prompt or a revised evaluation split; re-audit the exact demonstrations before
any adapter or scientific preflight. Stop before changing prompt/split science,
freezing a lock, model generation, training, or long execution.

## Copy-Ready First Message

```text
Independently verify the latest pushed RCMF integration branch, Final Harness
lock, and ALFWorld readiness records. Read AGENTS.md, docs/PIPELINE.md,
docs/SCIENTIFIC_STATUS.md, tasks/alfworld/STATE.md, and
tasks/alfworld/ADAPTATION_BRIEF.md. Address only the recorded
STOP_ALFWORLD_SPLIT_LEAKAGE decision: compare a provenance-clean prompt versus
an explicitly justified split policy without running model science or freezing
a benchmark lock. Report VERIFIED/UNVERIFIED facts and stop on ambiguity.
```
