# WebShop Readiness

Status: engineering plan only; no WebShop RCMF result exists.

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

Unresolved: archive provenance, redistribution, product/instruction/index
hashes, observation mode, stable train/evaluation split, server port/process
ownership, reward/evaluator determinism, tokenizer counts, and runtime. A
read-only Lambda inspection at `2026-09-08T09:43:34Z` found no importable
`webshop` or `gym` package in `/home/ubuntu/venvs/rcmf-py311`; no installation
or data download was attempted.
