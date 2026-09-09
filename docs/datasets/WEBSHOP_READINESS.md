# WebShop Readiness

Status: `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`; no WebShop RCMF result exists.

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
