# RCMF History Index

This is a routing index, not a replacement for sealed reports or
`research/experiments.jsonl`.

| Milestone | Question | Outcome/status | Authoritative result | Handoff | Source/run |
|---|---|---|---|---|---|
| EXP-031A | Can the complete 499-memory bank improve frozen-model behavior through one reversible field? | positive development evidence; historical method | `research/results/EXP_031A_RCMF_JOINT_FULL_BANK.md` | search `research/handoffs/` for EXP-031A | see result manifest |
| EXP-033A | How does the frozen historical field behave under one demo? | evaluation-only development result | `research/results/EXP_033A_RCMF_ONE_DEMO_DEV.md` | `research/handoffs/20260829T094000Z_exp033a_one_demo_dev.md` | sealed report |
| EXP-034A/B | Does one-demo-consistent retraining/selector retraining restore behavior? | mixed/negative development evidence | `research/results/EXP_034A_RCMF_ONE_DEMO_RETRAIN.md`; `research/results/EXP_034B_RCMF_ONE_DEMO_SELECTOR_RETRAIN.md` | dated handoffs | sealed reports |
| EXP-036C | Frozen Test-Normal evaluation | complete development benchmark, not untouched confirmatory evidence | `research/results/EXP_036C_APPWORLD_TESTNORMAL_FINAL.md` | `research/handoffs/20260902T133940Z_exp036c_appworld_testnormal_final.md` | sealed report |
| EXP-037A R2-R12 | Reproduce historical three-demo path and harden execution | selector/panel repaired; D06B/D22 positive control passed; several infrastructure defects diagnosed/fixed | `research/results/EXP_037A_R2_FIRST_DIVERGENCE_AUDIT.md` through R12 records | matching structured handoffs | historical branches/roots |
| EXP-037A 14n / R18 | Complete provenance-validated one-demo continuation | 18/18 stages; formal epoch 1; negative one-demo specificity | `research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md` | `research/handoffs/20260908T014023Z_exp037a_r18_formal_14n_terminal_result.md` | source `98f917d...`; run `...14k_o08_20260907_003` |
| EXP-037A R19 | Does forced epoch 2 rescue the direction? | post-hoc `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`; no training | `research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md` | `research/handoffs/20260908T071524Z_exp037a_r19_epoch2_sensitivity.md` | source `2a7f371...`; diagnostic root recorded in report |
| Portable V2 M1 | Can the mechanism be made a stable multi-dataset engineering base? | prospective engineering milestone; no dataset science | `docs/PIPELINE.md` and canonical version manifest | current portable-v2 handoff | source bound at release freeze |
| WebShop standard-200 | Does frozen RCMF improve AgentBench-FC WebShop over bare and matched shuffle? | 600/600 complete; small positive point estimate, paired intervals cross zero, correct equals shuffle on exact success; `INCONCLUSIVE_NULL_RESULT` | `research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md` | `research/handoffs/20260911T023157Z_webshop_agentbench_fc_standard200_final.md` | method `be711705...`; evaluator `77508bd8...`; run `webshop_rcmf_v1_d7853aba-...` |

Thematic failure prevention is in `docs/FAILURE_MODES.md`. Exact historical
chronology and every stopped/failed run remain in `research/experiments.jsonl`.
Do not load every old report by default.
