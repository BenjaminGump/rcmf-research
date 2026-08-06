# Codex Session Handoff

## Session Metadata

- Date: 2026-08-06.
- Milestone: 4C, RCMF-Compatible Signed Residual Associative Field.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user:
  `6fb52a66747c7bda2fe513bc7713254868dec148`.
- Final source commit used by Lambda run:
  `2fc95e2d41da933810df53e78a0eed62c972ee70`.
- Final artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- Superseded artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_001`.

## Scope

The user approved the first RCMF-compatible signed residual addressing pilot.
The hard scope was preserved:

- no Stage C;
- no program-head training;
- no injector construction or training;
- no Qwen action loss;
- no AppWorld agent evaluation;
- no full-bank end-to-end RCMF training.

## Source Changes

VERIFIED:

- Added `rcmf/training/signed_residual_field.py`.
- Added `scripts/run_stage_b_4c_signed_field.py`.
- Added `configs/benchmark/stage_b_4c_signed_field.yaml`.
- Added `tests/test_signed_residual_field.py`.
- Implemented signed two-tower reference, RCMF-compatible signed core field,
  state-only upper-bound scorer with separate gate, learned-prior ablation,
  deterministic task-grouped CV split helper, train-only `mu_i`, gate metrics,
  signed geometry diagnostics, and signed associative-field algebra utilities.
- Local tests passed: `69 passed`.
- Lambda tests passed: `69 passed`.

## Lambda Commands

Final source sync used a local git bundle because Lambda still has no GitHub
deploy key. The formal run command was:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_stage_b_4c_signed_field.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --previous-4b-dir runs/stage_b/addressing_4b_20260806_002 \
  --output-dir runs/stage_b/signed_field_4c_20260806_002 \
  --seeds 1 2 3 \
  --epochs 80 \
  --cv-epochs 80 \
  --batch-size 64 \
  --patience 12
```

Runtime: `907.20` seconds.

## Results

VERIFIED:

- Reference/core copied-weight reproduction passed with residual, gate, q, and
  k max absolute errors all `0.0`.
- Core signed field r128 continuity metrics:
  NDCG@4 `0.555174/0.018107`, positive mass@4
  `0.202908/0.008782`, MRR `0.234205/0.011910`, Spearman
  `0.246267/0.019878`.
- Global memory prior:
  NDCG@4 `0.453376/0.304515`, positive mass@4
  `0.141993/0.128717`.
- Core correct-minus-shuffled:
  NDCG@4 `0.162368/0.025262`, positive mass@4
  `0.076659/0.005683`.
- Core residual MSE/Huber/correlation:
  `0.313298`, `0.035495`, `0.279242`.
- Core interaction variance:
  `0.291447`.
- Five-fold CV:
  mean NDCG@4 improvement `0.085079/0.065855`,
  mean correct-minus-shuffled NDCG@4 `0.102811/0.070323`,
  positive improvement in `4/5` folds.
- Learned-prior core r128:
  NDCG@4 `0.573485/0.018479`, positive mass@4
  `0.205699/0.011101`.
- Gate for core r128:
  AUROC `0.851812/0.004888`, AUPRC `0.964167/0.001949`,
  balanced accuracy `0.711715/0.021149`, false activation
  `0.472222/0.051967`.
- Field algebra/reversibility passed at rank 128:
  V identity max error `8.53e-14`, G identity error `0.0`, final arbitrary
  add/remove norms `1.05e-13` and `2.27e-13`.
- Decision branch:
  `signed_core_field_passed_recommend_stage_c_pilot`.

## Deviations And Corrections

- The first `_001` run was superseded. It had the same model metrics but
  showed `field_algebra.passed=false` because the validation used float32
  accumulation with an overly strict tolerance. It also exposed a gate AUPRC
  integration bug that produced negative AUPRC.
- Source commit `2fc95e2` fixed both issues:
  algebra validation now uses float64 for the proof, and AUPRC integrates from
  recall 0.
- The corrected formal artifact is `_002`.

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/summary.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/report.md`.
- Continuity summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/continuity_split_summary.json`.
- Cross-validation summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/cross_validation_summary.json`.
- Checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/continuity/checkpoints`.
- CV fold summaries:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002/cross_validation/fold_*/summary.json`.

## Next Step

Do not start Stage C automatically. The next review should decide whether to
launch a signed-program distillation pilot that preserves the signed residual
selection path and introduces program vectors without the additive injector
until the program objective is diagnosed.

## Final Lambda Status

VERIFIED:

- Lambda project HEAD: `2fc95e2d41da933810df53e78a0eed62c972ee70`.
- No tmux server is running.
- GPU status after run: `0 MiB`, `0%`.

