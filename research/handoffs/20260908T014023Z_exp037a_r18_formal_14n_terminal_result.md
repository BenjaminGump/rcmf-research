# EXP-037A-R18 Formal 14n Terminal Handoff

## Status

- Decision: `FORMAL_EXP037A_14N_CONTINUATION_COMPLETE_NEGATIVE_ONE_DEMO_RESULT`.
- Launch source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`.
- Archive: `archive/exp037a-r18-launch-source-98f917d`.
- Run UUID:
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Config/contract SHA256:
  `6e4be2be11e608436f5b5ebfdeee45d94a61f2831c841be72cd97e8050107e01` /
  `e479889fba498401e50ff7f309668629c3745d8ea5d91d84d8600c62986e61c7`.

## Execution

- R18 standing approval produced a new exact 14n authorization; no old
  authorization was reused. Explicit/runtime authorization SHA256 values are
  `dd8ccaab2be4bd33ad88f9d1746f758060fa6dd44ef71feb16214d28a6e747f8` /
  `3039410968805ed1e671db21363300256733232e827c372b2a8103a2ae9854f1`.
- Formal time: `2026-09-07T16:18:08.983597Z` through
  `2026-09-08T01:26:13.547861Z`, `9.134601 h` wall.
- All 18 authorized stages passed strict validation on their first attempt.
  Attempts are `18 complete / 0 open / 0 failed`.
- Parent closure remains `922/922`, SHA256
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
- Formal tmux exited normally after F03; H100 is idle; no follow-on started.

## Science

- One-demo O08 population: `324 train / 83 heldout / 407 total`, labels
  `120/247/40` POSITIVE/NEUTRAL/HARMFUL.
- Training: `1,032` units/backward passes, two epochs, seed `25101`; epoch 1
  selected. Selected checkpoint SHA256:
  `c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c`.
- Validated memory fields: 401-memory
  `bec6ae119ffd858a07eeafafbb65f55e42b39a0f71d224c3dd128afae0cf22af`;
  499-memory
  `3bda71b91f7e1f5147a8399c5fdcb41207869ffbd5f0236bfbdecaa88d4c4110`.
- Shared bare / 3D correct / 3D shuffle / 1D correct / 1D shuffle:
  `12 / 17 / 11 / 8 / 18` successes out of 57.
- One-demo correct minus bare is `-4/57`; one-demo correct minus matched
  shuffle is `-10/57`. The latter has `2` correct-only versus `12`
  shuffle-only successes, exact descriptive McNemar `p=0.012939453125`.
- Interpretation: clearly negative one-demo memory-specificity evidence. It is
  cross-source provenance-validated continuation evidence, not a single-source
  paper-level confirmation.

## Repair history

- Failed 14m root `_002` remains immutable. It failed before C00/science due
  to `CONTINUATION_SCHEMA_VERSION_DISPATCH_MISMATCH`.
- Repair source `98f917d...` made continuation dispatch semantic and
  fail-closed. Production C00/C01 smoke and all local/Lambda suites passed.
- Replacement 14n root `_003` started again from the sealed 14k O07 boundary
  and freshly produced O08-F03. No 14l/14m scientific output became input.

## Evidence map

- Report: `research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md`.
- Terminal summary:
  `research/results/exp037a_r18_continuation_self_healing/formal_terminal/terminal_summary.json`.
- Paired effects:
  `research/results/exp037a_r18_continuation_self_healing/formal_terminal/paired_effects.json`.
- Raw final record SHA256:
  `531ba157c1f42d64c40161124d70121e9ea18e35922d01c26b2b86e8b8d89054`.
- Raw paired analysis SHA256:
  `fb8a6d79b48bb80fc1719efeb30e9a753a7bcbab4e04e07469d8f3d3ed0cbc92`.
- Raw orchestrator SHA256:
  `81b2946c4bc8325c45142058f4c3a05ccd2a9ef01e42f72104467b0d6e477dc0`.

## Next action

Stop for scientific review. Do not launch a single-source confirmation,
optimization, ablation, or any other experiment automatically.
