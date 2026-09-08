# ALFWorld ChatGPT Context

- Document role: ALFWorld conversation bootstrap cache; not a source of truth.
- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Generated-from commit: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical source SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Last verified UTC: `2026-09-08T17:33:33Z`.
- Base canonical SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Adapter version: `alfworld:portable-v2_1-pending` / protocol
  `rcmf_reproducible_benchmark_adapter_v2`.
- Branch/worktree/run namespace: future `adapt/alfworld-v2_1`, dedicated
  worktree, `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`.
- Latest relevant handoff: `research/handoffs/20260908T173333Z_rcmf_portable_v2_1_m1.md`.

Independently verify the latest pushed GitHub state, archive, canonical
manifest, and task state before treating this cache as current.

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

Verified: source repo commit
`aaba6870f86c5be6a08a491f32a50b906227bc3e`; exact ReAct profile
`react_task_type_two_demo_v1` from commit
`6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`; renderer and portable mock
conformance. Portable V2.1's real AppWorld pilot also passed all shared
executor boundaries, but this does not validate ALFWorld. Prompt source/profile is
`assets/prompts/source_manifests/react_alfworld.json` and exact task-family
two-demo JSON. Planned trajectory source is official training-game expert
plans replayed through the TextWorld interface with `OFFICIAL_EXPERT`
provenance. Train is memory/training; valid seen/unseen are evaluation-only.

Unverified: deployed ALFWorld package/data version, official expert textual
replay, stable task/split/leakage manifests, Qwen token contract, environment
determinism, runtime, and every scientific result. No scientific ALFWorld RCMF
result exists.

Current blocker/next decision: inspect/install the isolated environment and
data, then replay a few official training games across task families without
Qwen or training. Stop for user approval before large installation, scientific
GPU work, any run plausibly over 18 hours, core/scientific changes, or unclear
provenance.

## Copy-Ready First Message

```text
Independently verify the latest pushed portable-v2 source. Read AGENTS.md,
docs/PIPELINE.md, docs/ADAPTER_CONTRACT.md,
docs/datasets/ALFWORLD_READINESS.md, tasks/alfworld/STATE.md, and
tasks/alfworld/ADAPTATION_BRIEF.md. Begin only the bounded ALFWorld environment,
data, and official-expert TextWorld replay inspection. Do not run Qwen, train
RCMF, use validation trajectories for memory, or modify portable core without a
verified defect and review. Report VERIFIED/UNVERIFIED facts and update STATE.
```
