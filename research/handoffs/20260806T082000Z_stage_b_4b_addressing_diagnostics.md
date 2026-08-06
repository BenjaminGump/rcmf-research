# Codex Session Handoff

## Session Metadata

- Date: 2026-08-06.
- Milestone: 4B, Diagnose and Recover State-Conditioned Addressing.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user:
  `aad33d2913301869209054526c943b3e26d34e6a`.
- Final source commit used by corrected Lambda run:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- Corrected artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Final record commit: created after this handoff is written.

## 1. Scope

The user requested forensic diagnosis of the failed Stage-B addressing pilot,
teacher utility decomposition, lightweight scorer ablations, and minimal dense
address repair attempts. The hard scope was preserved:

- no Stage C;
- no program-head training;
- no injector construction or training;
- no Qwen action loss;
- no AppWorld agent evaluation;
- no end-to-end RCMF training.

## 2. Source Changes

VERIFIED:

- Added `rcmf/training/addressing_4b.py`.
- Added `scripts/run_stage_b_4b_diagnostics.py`.
- Added `tests/test_addressing_4b.py`.
- Added unit coverage for hard-top-k disjoint-support zero-gradient trapping
  and overlapping-support nonzero-gradient control.
- Local tests passed: `60 passed`.
- Lambda tests at final source commit passed: `60 passed`.

## 3. Lambda Commands

Corrected formal run:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_stage_b_4b_diagnostics.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --previous-pilot-dir runs/stage_b/addressing_only_pilot_20260806_003 \
  --output-dir runs/stage_b/addressing_4b_20260806_002 \
  --seeds 1 2 3 \
  --epochs 80 \
  --batch-size 64 \
  --patience 12
```

Runtime: `122.98` seconds.

## 4. Forensic Results

VERIFIED:

- Hard-top-k disjoint-support test:
  supports `[0,1,2,3]` and `[60,61,62,63]`, `q=0.0`,
  state grad norm `0.0`, memory grad norm `0.0`.
- Overlap control:
  `q=0.596093`, state grad norm `0.127603`, memory grad norm `0.128555`.
- Best checkpoint seed 1:
  zero support overlap `1.0`, zero raw dot `1.0`, all inspected gradient norms
  `0.0`.
- Best checkpoint seed 2:
  zero support overlap `0.0`; every pair had support intersection 1; state and
  alpha top-1 load were both `1.0`.
- Best checkpoint seed 3:
  zero support overlap `1.0`, zero raw dot `1.0`, all inspected gradient norms
  `0.0`.

Conclusion:

- A. disjoint-support zero-gradient trapping: verified and affects seeds 1/3.
- B. shared-basis collapse: verified for all best checkpoints.
- C. rho/global-prior domination: not the primary verified cause.

## 5. Utility Decomposition

VERIFIED:

- Memory main-effect variance explained: `0.017852`.
- Train residual variance: `0.109839`.
- Train memory-mean variance: `0.002016`.
- Train state-mean variance: `0.057149`.
- Train residual std: `0.331419`.
- Validation residual mean/std: `-0.029875/0.324027`.
- Utility and residual imputed train matrix effective rank:
  `26.145799`.

## 6. Scorer Ladder

Validation mean/std across seeds 1/2/3:

- Global prior:
  NDCG@4 `0.453376/0.304515`, positive mass@4 `0.141993/0.128717`.
- State-only residual:
  NDCG@4 `0.571722/0.015435`, positive mass@4 `0.214541/0.006305`,
  correct-minus-shuffled NDCG@4 `0.189911/0.024032`.
- Signed two-tower:
  NDCG@4 `0.547162/0.026890`, positive mass@4 `0.204190/0.003365`,
  correct-minus-shuffled NDCG@4 `0.144968/0.042046`.
- Current hard-top-k control:
  NDCG@4 `0.386161/0.042185`, positive mass@4 `0.147272/0.010647`,
  correct-minus-shuffled NDCG@4 `0.0`.
- Dense separate-head:
  NDCG@4 `0.453376`, positive mass@4 `0.141993`,
  correct-minus-shuffled NDCG@4 `0.0`.
- Dense shared-head:
  NDCG@4 `0.453376`, positive mass@4 `0.141993`,
  correct-minus-shuffled NDCG@4 `0.0`.

Decision-tree branch:

- `dense_rcmf_address_failed`.

Interpretation:

- The state representation and teacher residual labels do contain
  held-out-task signal.
- Frozen memory representations can participate in a state-conditioned scorer.
- The current RCMF address parameterization is the bottleneck.

## 7. Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/summary.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/report.md`.
- Forensics:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/forensic_diagnostics.json`.
- Utility decomposition:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/utility_decomposition.json`.
- Scorer ablation:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/scorer_ablation_summary.json`.
- Checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002/checkpoints`.

## 8. Next Step

Do not proceed to Stage C. The next experiment should redesign Stage-B
addressing around a signed residual state-memory interaction with frozen
`mu_i` as global prior and a separate activation gate. Hard top-k should remain
disabled until a continuous/signed design passes the validation gate.

## 9. Final Lambda Status

VERIFIED:

- No `run_stage_b_4b_diagnostics.py` process remains.
- No tmux server is running.
- GPU status after run: `0 MiB`, `0%`.

