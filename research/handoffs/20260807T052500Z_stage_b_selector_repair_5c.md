# Handoff: Milestone 5C Selector Repair

## Status

- Completed.
- Branch: `workflow/research-loop`
- Source commit: `5e5c74c43b43dff9a8c2f3d5a054917849b33e29`
- Final record commit: pending at handoff creation time
- Decision branch: `selector_capacity_or_representation_tradeoff`
- Stage C2 allowed: no
- AppWorld generation/evaluation allowed from this result: no

## Hard Scope Observed

- Retrained and diagnosed only the signed Stage-B selector.
- Did not train Stage-C program heads.
- Did not train or evaluate the additive-token injector as a new model.
- Did not use Stage-C1 behavioral checkpoints as selector training targets.
- Did not call Qwen forward passes during selector training.
- Did not run AppWorld generation/evaluation.
- Did not start Stage C2 or end-to-end RCMF training.
- Ran the required eval-only Stage-C1 alignment projection using existing
  content program/injector checkpoints.

## Commands

Source smoke/tests:

```bash
python -m py_compile rcmf/training/selector_repair_5c.py scripts/run_stage_b_5c_selector_repair.py
python -m pytest -q
```

Lambda run:

```bash
python scripts/run_stage_b_5c_selector_repair.py \
  --config configs/benchmark/stage_b_5c_selector_repair.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --response-cache-dir runs/stage_c1/response_cache_20260806_001 \
  --stage-c1-dir runs/stage_c1/signed_program_c1_20260806_002 \
  --output-dir runs/stage_b/selector_repair_5c_20260807_001 \
  --seeds 1 2 3 \
  --cv-epochs 80 \
  --batch-size 64 \
  --patience 12 \
  --projection-batch-size 1 \
  --device cuda
```

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/report.md`
- CV:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/cross_validation_summary.json`
- Continuity:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/continuity_summary.json`
- Stage-C1 projection:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/stage_c1_projection/stage_c1_projection_summary.json`
- Checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001/continuity/checkpoints`
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_b_5c_selector_repair_20260807_001.log`

## Main Results

- Selected config: `C_top_listwise_temp0p03`
- Runtime: `3,687.92` seconds
- CV gate: failed
- Continuity gate: failed

CV selected-config metrics:

- Recall@4: `0.387077`
- Recall@8: `0.616991`
- NDCG@4: `0.523613`
- NDCG@4 improvement over global: `0.083399`
- Correct-minus-shuffled NDCG@4: `0.131188`
- Utility-score Spearman: `0.119677`
- Teacher-best negative-score fraction: `0.203096`

Continuity metrics:

- Recall@1/2/4/8:
  `0.179710 / 0.266667 / 0.359420 / 0.582609`
- Median teacher-best rank: `7`
- Teacher-best negative-score fraction: `0.176812`
- Utility-score Spearman: `0.174524`
- NDCG@4: `0.581587`
- Correct-minus-shuffled NDCG@4: `0.206391`

Geometry:

- Interaction variance: `1.506124`
- q effective rank: `36.530959`
- k effective rank: `17.653024`

Eval-only Stage-C1 projection:

- Teacher-best LOO effect mean: `0.010726`, CI `[0.003277, 0.019094]`
- Selector-top LOO effect mean: `0.027432`, CI `[0.017361, 0.039065]`
- Teacher-best minus selector-top mean: `-0.016706`
- Raw utility vs analytic delta-z Spearman: `0.047117`

## Interpretation

VERIFIED:

- Top-listwise selector repair can improve Recall@8 and preserve
  state-dependent NDCG relative to the original Stage-4C loss.
- It does not satisfy the top-utility alignment gate: Recall@4, median rank,
  negative-score rate, and Spearman remain insufficient.
- The field geometry did not collapse, so failure is not a trivial zero-signal
  collapse.
- Replacing only the selector in old Stage-C1 increases teacher-best LOO
  magnitude, but selector-top still has larger behavioral effect and raw
  utility still barely correlates with analytic delta-z.

INFERENCES:

- The current state/memory representation and loss can move coarse top-8
  alignment, but not enough fine-grained top-utility calibration.
- Repeating full-bank Stage-C1 training with this selector is unlikely to prove
  memory-content causality without pair-level or single-memory behavioral
  grounding.

UNVERIFIED:

- Whether a calibrated selector loss can reach both Spearman and Recall@4
  gates.
- Whether pair-level/single-memory behavioral grounding can make program
  vectors causally memory-specific.

## Next Step

Do not start Stage C2. The next approved research direction should be a
pair-level or single-memory behavioral grounding pilot that directly supervises
memory-specific effects before any full-bank Stage-C1 retry.

