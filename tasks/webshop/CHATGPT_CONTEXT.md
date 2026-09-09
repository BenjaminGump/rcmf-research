# WebShop ChatGPT Context

- Document role: WebShop conversation bootstrap cache; not a source of truth.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Generated-from commit: `4e56702f467635bda120d118a5367c58e593ecef`.
- Canonical source SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- RCMF Harness V1 integration source SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Final Harness source SHA: `827ed6f394804834e93444c9bb02c435e9e238a3`.
- Last verified UTC: `2026-09-09T09:58:34Z`.
- Readiness branch/source/records: `dataset/webshop-readiness-v1` /
  `2072ae59b79171facabb2b580cda0c2ae460cd8e` /
  `40d6318892e03d7e775ed83704dae89917f7b324`.
- Adapter version: `webshop:portable-v2_1-pending` under
  `rcmf_reproducible_benchmark_adapter_v2`.
- Prompt source/profile: ReAct `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9` /
  `react_official_one_demo_v1`.
- Trajectory source/provenance: setup human sample unresolved; larger archive
  inspection-only; IL source `UNKNOWN_PROHIBITED`.
- Split/evaluation contract: source-defined seed-233 task split; only verified
  training instructions may supply memory.
- Branch/worktree/run namespace: future `adapt/webshop-v1`, dedicated worktree,
  `/lambda/nfs/rcmf-persist/project/runs/webshop/<uuid>`, unique server port.
- Latest relevant handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`.

Independently verify the latest pushed GitHub state, archive, Final Harness
lock, readiness records, and task state before treating this cache as current.

## Authority Order

1. Sealed primary artifacts and source code at the specified commit.
2. The canonical version manifest and `docs/PIPELINE.md`.
3. `tasks/webshop/STATE.md`.
4. `docs/HISTORY.md` and `docs/FAILURE_MODES.md`.
5. Conversation memory.

## Documents To Read

- `AGENTS.md`
- `docs/CHATGPT_ENTRYPOINT.md`
- `docs/PIPELINE.md`
- `docs/ADAPTER_CONTRACT.md`
- `docs/SCIENTIFIC_STATUS.md`
- `docs/datasets/WEBSHOP_READINESS.md`
- `tasks/webshop/STATE.md`
- `tasks/webshop/ADAPTATION_BRIEF.md`

## State

Verified: Final Harness V1 and the RCMF integration lock pass; WebShop source,
prompt, action/reward semantics, split algorithm, and the inspection-only
1,643-session archive identity are recorded. Readiness remains
`STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`.

Unverified: product/instruction/index/setup-sample identities and terms, live
server/replay, exact task manifest, final adapter, benchmark lock, and all
scientific results. No scientific WebShop RCMF result exists.

Current blocker/next decision: obtain a primary or maintainer-signed exact data
manifest for products, instructions, Lucene index, and setup sample before any replay
population is selected. Stop before guessing provenance, generating an oracle,
freezing a lock, model generation, training, or long execution.

## Copy-Ready First Message

```text
Independently verify the latest pushed RCMF integration branch, Final Harness
lock, and WebShop readiness records. Read AGENTS.md, docs/PIPELINE.md,
docs/SCIENTIFIC_STATUS.md, tasks/webshop/STATE.md, and
tasks/webshop/ADAPTATION_BRIEF.md. Address only
STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED by obtaining or validating exact primary
data/index/setup-sample identities and terms. Do not guess provenance, run
model science, or freeze a benchmark lock. Stop on ambiguity.
```
