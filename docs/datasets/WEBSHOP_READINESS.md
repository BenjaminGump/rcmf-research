# WebShop Readiness

Status: `AGENTBENCH_FC_WEBSHOP_STANDARD200_TERMINAL_COMPLETE`.

The active AgentBench-FC runtime, benchmark, RCMF adapter, train construction,
method freeze, validation, and exact `[0,200)` formal evaluation are complete.
All 600 B0/RCMF-C/RCMF-S trajectories passed final artifact audit. The
scientific result is `RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`;
details are in
`research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md`.
The older Princeton archive readiness stop below remains historical provenance
and does not describe the active AgentBench-FC benchmark.

The active end-to-end charter supersedes the earlier Princeton archive
readiness stop. The primary formal population is now AgentBench-FC
`webshop-std` indices `[0,200)`, exactly 200 tasks. The candidate validation
population is `[500,1500)` and construction is restricted to `[1500,12000)`.
Indices `[200,500)` are excluded from this milestone. Standard-200 outcomes
cannot be inspected during construction, method selection, or debugging.

The Lambda-derived `WEBSHOP_REPRO_RUNTIME_2026` preserves AgentBench commit
`d1e4a10db08c87075c78972e48ecc182be03e2d5`, Princeton commit
`64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`, immutable data image
`sha256:e8bd3b120fe57653ecd902a6629d11a7d68f738e15f56a7e0aefdc1139f38b09`,
and the original WebShop search/action/reward implementation. Its derived
worker image is
`sha256:af342a9b210bc8bde14eccb3c858beefc79de7203b63d3286beb327b099d0cb9`.
Two fresh containers produced byte-identical reset/search fixtures. The
complete task manifest contains 11,700 frozen rows and the Lucene manifest
contains 19 files totaling 3,601,981,280 bytes. These are engineering/runtime
facts, not a scientific result.

The active prompt profile is `agentbench_fc_webshop_v1`, extracted
programmatically from pinned AgentBench source with `content_changed=false`.
Its system-prompt SHA256 is
`4d2c361799681a200b69c21229a3ea07a79b7c1e74fd0cb308defd0c85d3ce11`;
the tool-schema SHA256 is
`be6b939ad34d95f55759f679df37f167bda2585d8eea128bea6f2e6a0e7af7c3`.
The adapter uses structured `search_action(keywords)` and
`click_action(value)` calls, retains continuous reward, and defines full
success only as exact reward `1.0`.

Construction provenance is `AGENT_GENERATED`: frozen Qwen3-8B trajectories
will be generated only on the train split, and only complete exact-1.0,
replay-validated paths may enter the authoritative transition ledger. No
standard-200 outcome or solution may influence corpus construction.

The historical readiness record below remains provenance history, not the
active execution decision.

---

Historical status: `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`.

The completed readiness work is on `dataset/webshop-readiness-v1`, source
`2072ae59b79171facabb2b580cda0c2ae460cd8e`, records
`40d6318892e03d7e775ed83704dae89917f7b324`. Source/prompt/action/reward/split
semantics and an inspection-only 1,643-session archive were recorded. Product,
instruction, Lucene-index, and setup-sample identities and terms remain
unsealed, so no replay population or benchmark lock may be frozen.

Primary source is `princeton-nlp/WebShop` pinned at
`64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd` (MIT). Product corpus,
instruction data, search index, observation mode, server/runtime, and archive
licenses/hashes remain to be sealed. No corpus or index is vendored here.

## Trajectory Sources

- Official setup sample: approximately 50 MTurk human trajectories,
  `OFFICIAL_HUMAN_SAMPLE`.
- Optional larger official human demonstration archive, `OFFICIAL_HUMAN` once
  archive identity and terms are verified.
- Finalized imitation-learning archive referenced by baseline workflow:
  `UNKNOWN_PROHIBITED` until documentation proves whether rows are human or
  model/IL. Do not guess.
- Fallback design only: `WebShopTrainingMetadataOracleTrajectoryProvider`,
  `ORACLE_GENERATED_FROM_TRAIN_METADATA`.

For every accepted trajectory, reset the exact training session/instruction,
replay `search[...]` and `click[...]`, record exact selected-interface
observations and continuous reward, and admit a fully correct memory only when
official final reward is exactly `1.0`. Partial-reward paths remain visible but
cannot enter the fully-correct ledger. Evaluation instructions cannot generate
or filter training memories.

The oracle fallback may use only official training instruction/product/option
metadata, must navigate through the official environment, reach reward 1.0,
replay-validate, and remain separate from human data. It is not implemented or
authorized here.

The prompt candidate is exact ReAct `react_official_one_demo_v1`; see
`docs/PROMPT_SOURCES.md`. The next bounded task is package/data/license
inspection, source-archive provenance classification, server/session smoke,
and replay of the small official sample. No scientific Qwen run or product
corpus commit is permitted.

The preceding historical plan remains useful only after an exact primary or
maintainer-signed data/terms manifest passes the readiness probe.
