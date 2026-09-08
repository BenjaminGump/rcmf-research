# WebShop ChatGPT Context

- Document role: WebShop conversation bootstrap cache; not a source of truth.
- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`.
- Canonical executable ancestor SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2-ea152c7`.
- Bootstrap generated at SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`.
- Generated-from commit: `ca799ffa5692678081d69f4994e470267def4ec5`.
- Canonical source SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`.
- Last verified UTC: `2026-09-08T13:44:52Z`.
- Base canonical SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`.
- Adapter version: `webshop:portable-v1-pending` / protocol
  `rcmf_reproducible_benchmark_adapter_v2`.
- Branch/worktree/run namespace: future `adapt/webshop-v1`, dedicated worktree,
  `/lambda/nfs/rcmf-persist/project/runs/webshop/<uuid>`, unique server port.
- Latest relevant handoff: `research/handoffs/20260908T134452Z_rcmf_portable_v2_m1.md`.

Independently verify the latest pushed GitHub state, archive, canonical
manifest, and task state before treating this cache as current.

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

Verified: source repo commit
`64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`; exact ReAct profile
`react_official_one_demo_v1` AST-extracted from commit
`6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`; action-grammar renderer and
portable continuous-reward mock conformance. Prompt source/profile is
`assets/prompts/source_manifests/react_webshop.json`. Planned sources keep the
setup human sample, full human archive, IL/model archive, and training-metadata
oracle separate. Exact reward `1.0` is full success; partial reward is not.

Unverified: environment/data installation, product/instruction/index and
observation-mode identities, archive provenance/license, stable split, session
replay, token counts, runtime, and every scientific result. No scientific
WebShop RCMF result exists.

Current blocker/next decision: inspect source/archive licenses and isolated
server/data setup, classify IL provenance, then replay the small human sample
without Qwen. Stop for approval before large download, scientific GPU work,
any run plausibly over 18 hours, result-tuned oracle, core/scientific changes,
or ambiguous provenance.

## Copy-Ready First Message

```text
Independently verify the latest pushed portable-v2 source. Read AGENTS.md,
docs/PIPELINE.md, docs/ADAPTER_CONTRACT.md,
docs/datasets/WEBSHOP_READINESS.md, tasks/webshop/STATE.md, and
tasks/webshop/ADAPTATION_BRIEF.md. Begin only the bounded WebShop source,
license, environment, archive-provenance, and small-sample replay inspection.
Do not run Qwen, train RCMF, download/commit the full corpus without review, or
guess IL provenance. Report VERIFIED/UNVERIFIED facts and update STATE.
```
