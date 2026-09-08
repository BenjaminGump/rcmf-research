# ALFWorld Portable-V2 Adaptation Brief

- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`
- Canonical executable ancestor SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`
- Canonical archive ref: `archive/rcmf-portable-canonical-v2-ea152c7`
- Bootstrap generated at SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`
- Last verified UTC: `2026-09-08T13:44:52Z`

Start by reading `AGENTS.md`, `docs/PIPELINE.md`,
`docs/ADAPTER_CONTRACT.md`, `docs/datasets/ALFWORLD_READINESS.md`, and
`tasks/alfworld/STATE.md`. Base the worktree on
the current canonical executable ancestor above; independently verify the archive/commit before
editing.

Implement only under `rcmf/benchmarks/alfworld/`,
`configs/datasets/alfworld_v1.yaml`, `assets/prompts/alfworld/`,
`tasks/alfworld/`, and ALFWorld tests/entrypoints unless a proven core defect
requires review. Use branch `adapt/alfworld-v1`, a separate worktree,
environment/process namespace, and NFS root
`/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`.

Primary environment source: `alfworld/alfworld` commit
`aaba6870f86c5be6a08a491f32a50b906227bc3e` (MIT). Prompt source: ReAct commit
`6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`, profile
`react_task_type_two_demo_v1`, manifest
`assets/prompts/source_manifests/react_alfworld.json`. Preserve exact two
task-type examples and text-command action grammar.

The trajectory plan is to replay official training-game expert plans through
the exact TextWorld deployment interface, record returned textual
observations, verify success, and emit `OFFICIAL_EXPERT` portable records linked
to `traj_data.json` and game/plan hashes. Do not treat THOR low actions as text
commands. Train supplies memory/training; valid seen/unseen are evaluation-only.

Expected records use opaque household commands and complete text states. First
bounded task: verify installed package/data identities and expert API, then
replay at most a few training games across task families with no Qwen or RCMF
training. Validate source/license, resets, action/observation schema, reward,
stable IDs, and typed replay failures.

Then proceed through adapter capability/schema conformance, split/leakage
manifest, prompt/tokenizer equality, bare-agent smoke, writer/field/read module
diagnostics, small preregistered integration, and runtime preflight. Stop for
user approval before large installation, scientific GPU work, any run plausibly
over 18 hours, any core/scientific-method change, or unresolved provenance.
