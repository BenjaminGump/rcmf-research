# WebShop Portable-V2 Adaptation Brief

Start by reading `AGENTS.md`, `docs/PIPELINE.md`,
`docs/ADAPTER_CONTRACT.md`, `docs/datasets/WEBSHOP_READINESS.md`, and
`tasks/webshop/STATE.md`. Base the worktree on
`ea152c7393056d9f8502bdef87b0b0c34d1f1d89`; independently verify the archive/commit before
editing.

Implement only under `rcmf/benchmarks/webshop/`,
`configs/datasets/webshop_v1.yaml`, `assets/prompts/webshop/`,
`tasks/webshop/`, and WebShop tests/entrypoints unless a proven core defect
requires review. Use branch `adapt/webshop-v1`, a separate worktree,
environment/process namespace, unique WebShop server port, and NFS root
`/lambda/nfs/rcmf-persist/project/runs/webshop/<uuid>`.

Primary source: `princeton-nlp/WebShop` commit
`64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd` (MIT). Prompt source: ReAct commit
`6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`, AST-extracted profile
`react_official_one_demo_v1`, manifest
`assets/prompts/source_manifests/react_webshop.json`. Preserve the exact one-demo
header/example and search/think/click grammar.

Pin environment code, products, instructions, search index, observation mode,
and server/session identity. Separate the approximately 50 setup human samples,
full human archive, IL/model archive, and optional train-metadata oracle by
provenance. Do not admit the IL archive until its source class is verified.
Replay source `search[...]`/`click[...]` actions and record actual observations
and continuous reward. Only exact official reward `1.0` is fully successful.

First bounded task: verify installation/data licenses and archive provenance,
start an isolated engineering server if cheap, and replay the small official
sample with no Qwen generation. Record deterministic reset/session behavior,
action/observation schemas, index identity, rewards, and typed failures.

Then proceed through adapter conformance, train/evaluation leakage manifest,
prompt/tokenizer equality, bare smoke, RCMF module diagnostics, small
preregistered integration, and runtime preflight. Stop for approval before a
large corpus download, scientific GPU work, any run plausibly over 18 hours,
any result-tuned oracle, core/scientific-method change, or unresolved license/
provenance. Never share a mutable server port or output root with ALFWorld.
