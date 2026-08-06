# Codex Session Handoff

## Session Metadata

- Date: 2026-08-06.
- User request: Milestone 4 only, implement/validate the student-label
  compiler and run the first RCMF student training only for
  StateEncoder/alpha/rho addressing applicability.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user:
  `f487204869aa1f81039ee64ec82f9a70251c5302`.
- Final source commit used by the Lambda pilot:
  `9f84b77dfb2e42ef3ec32a51567f376379ee352a`.
- Ending branch: `workflow/research-loop`.
- Final record commit: created after this handoff is written.

## 1. Requested Goal

Compile Stage-B student labels from the complete raw-text teacher cache, enforce
the strict inductive memory split, train/evaluate addressing-only
StateEncoder/alpha/rho models for seeds 1/2/3, compare required baselines, run
geometry diagnostics, decide the scientific gate, and stop before Stage C.

## 2. Files Changed

- `rcmf/training/student_labels.py`
- `rcmf/training/addressing_only.py`
- `scripts/compile_student_labels.py`
- `scripts/run_addressing_only_pilot.py`
- `tests/test_student_label_compiler.py`
- `tests/test_addressing_only.py`
- `research/CURRENT_STATE.md`
- `research/DECISIONS.md`
- `research/NEXT_EXPERIMENTS.md`
- `research/experiments.jsonl`
- `research/results/stage_b_addressing_only_pilot_20260806_003.md`
- `research/handoffs/20260806T034500Z_stage_b_addressing_only.md`

## 3. Implementation Summary

VERIFIED:

- Added `stage_b_addressing_student_labels_v1`.
- Added `stage_b_addressing_only_pilot_v1`.
- Label compiler writes one row per state with ordered effective memory IDs,
  valid mask, raw utility vector, L0, positive/neutral/negative masks,
  strong-positive/strong-negative masks, positive gains, no-positive and
  all-missing flags, source hashes, and teacher-cache identity.
- The compiler uses only `valid_for_loss=true` rows and leaves over-context rows
  masked.
- The addressing model computes `q(s, i) = rho_i * dot(b(s), alpha_i)` with
  gradients.
- Program head is frozen; injector is not constructed; Qwen action loss is not
  called.
- Losses include listwise positive utility, pairwise ranking, negative
  suppression, no-positive/off, and positive activation guard.
- Sparse/orthogonal losses are not enabled.
- Evaluation includes global train-utility, rho-only, frozen-Qwen cosine,
  deterministic random, and shuffled-state baselines.
- Geometry diagnostics include address/alpha cosine and effective-rank
  summaries, basis usage/load, rho distribution, and correct-vs-shuffled score
  deltas.

## 4. Validation

Local:

```powershell
python -m py_compile rcmf\training\student_labels.py rcmf\training\addressing_only.py scripts\compile_student_labels.py scripts\run_addressing_only_pilot.py
python -m pytest -q
```

VERIFIED:

- Local full tests: `56 passed`.
- Lambda full tests at final source commit `9f84b77`: `56 passed`.

## 5. Lambda Commands

Label compiler:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/compile_student_labels.py \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --output-dir runs/stage_b/student_labels_20260806_002
```

Final pilot:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_addressing_only_pilot.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --output-dir runs/stage_b/addressing_only_pilot_20260806_003 \
  --seeds 1 2 3 \
  --max-epochs 120 \
  --batch-size 64 \
  --eval-every 5 \
  --patience 20 \
  --lr 0.001 \
  --weight-decay 0.0001 \
  --smoke-epochs 10 \
  --overfit-epochs 80
```

## 6. Results

VERIFIED:

- Effective train-memory bank: 36.
- Excluded memories: 10.
- Train label counts: 499 states, 16,786 valid rows, positive/neutral/negative
  8,230/3,067/5,489, 8 all-missing states.
- Validation label counts: 139 states, 4,930 valid rows,
  positive/neutral/negative 2,412/850/1,668, 0 all-missing states.
- Tiny overfit best NDCG@4: `0.616348`.
- Three-seed learned mean/std:
  NDCG@4 `0.386161/0.042185`,
  positive mass@4 `0.147272/0.010647`,
  MRR `0.165514/0.013510`.
- Baseline NDCG@4:
  global train utility `0.453376`, rho-only `0.370048`,
  frozen-Qwen cosine `0.366233`, deterministic random `0.366264`,
  shuffled-state `0.386161`.
- Program head max absolute delta: `0.0` for all seeds.
- Scientific gate: failed.

## 7. Geometry

VERIFIED:

- State-address pairwise cosine mean: `0.996045`.
- State-address centered effective rank: `2.269852`.
- State-address top-1 basis load fraction: `1.0`.
- Alpha pairwise cosine mean: `0.997041`.
- Alpha centered effective rank: `2.427664`.
- Alpha top-1 basis load fraction: `1.0`.
- Correct-vs-shuffled score absolute delta mean: `0.000113`.

## 8. Deviations and Workarounds

VERIFIED:

- The first label compiler run exposed repeated hashing of the large teacher
  cache; source hashes are now computed once, and labels were regenerated.
- The first pilot summary evaluated the final model, not the best checkpoint;
  the script was fixed and the final `_003` artifact evaluates best
  checkpoints.
- The final `_003` run was executed in the foreground because the tmux wrapper
  did not reliably pass the inner command. It completed successfully and wrote
  the same explicit `run.log`; no process remains.

## 9. Next Gate

- Stop before Stage C.
- Do not train program head or injector until a repaired Stage-B addressing
  path passes the scientific gate.
- Recommended next diagnosis: direct supervised state-memory scorer over
  frozen Qwen representations, then address-normalization and anti-collapse
  ablations only if the direct scorer confirms held-out-task signal exists.

## 10. Artifact References

- Labels:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`.
- Pilot:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003`.
- Pilot summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/summary.json`.
- Pilot report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/report.md`.
- Seed best checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_1/checkpoint_best.pt`,
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_2/checkpoint_best.pt`,
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_3/checkpoint_best.pt`.
