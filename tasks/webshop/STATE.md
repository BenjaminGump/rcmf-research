# WebShop State

- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`
- Bootstrap generated at SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Last verified UTC: `2026-09-08T17:33:33Z`
- Base canonical SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Adapter version: `webshop:portable-v2_1-pending` against
  `rcmf_reproducible_benchmark_adapter_v2`
- Branch/worktree: future `adapt/webshop-v2_1` in a dedicated worktree
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/webshop/<uuid>`
- Objective: adapt portable RCMF to pinned WebShop text sessions/rewards.
- Verified: upstream repository commit
  `64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`; ReAct prompt commit
  `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`; exact AST-extracted prompt1
  hash and search/think/click renderer tests.
- Unverified: installed package, product/instruction/search-index versions,
  server/observation mode, human/IL archive provenance, split, replay,
  tokenizer counts, scientific behavior.
- Current blockers: no sealed environment/data/archive-provenance manifest.
- Source manifests: `configs/datasets/webshop_v1.yaml` and
  `assets/prompts/source_manifests/react_webshop.json`.
- Prompt profile: `react_official_one_demo_v1`; no scientific selection.
- Trajectory providers: official setup sample `OFFICIAL_HUMAN_SAMPLE`; full
  human archive `OFFICIAL_HUMAN` after verification; IL archive remains
  `UNKNOWN_PROHIBITED`; metadata oracle is fallback design only.
- Split/evaluation: only official training instructions may supply memories;
  official evaluation split must be sealed by the adapter.
- Completed checks: prompt provenance/hash/action grammar; portable continuous
  reward and WebShop-like session conformance.
- Unresolved checks: environment/server setup, licenses, archive provenance,
  reward-1.0 replay, index identity, leakage, token counts, bare smoke, runtime.
- Next action: bounded source/archive/license and environment inspection, then
  replay the small official sample without model generation.
- Latest handoff: `research/handoffs/20260908T173333Z_rcmf_portable_v2_1_m1.md`.
- Approval stop: large corpus acquisition, scientific generation/training, or
  a run plausibly over 18 hours requires review/explicit authorization.
- Prohibited: partial reward as full success, evaluation leakage, guessed IL
  provenance, result-tuned oracle, shared server port/root, copied AppWorld stages.
- Dirty/uncommitted status: verify independently before creating the worktree.
