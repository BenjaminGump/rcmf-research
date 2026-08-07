# Handoff: Milestone 5D Pair-Level Grounding

## Status

- Completed.
- Branch: `workflow/research-loop`
- Source commit:
  `f8cc37547ec6c3e404f84c726efa01e4c8ccb9f9`
- Final record commit: pending at handoff creation time
- Decision branch: `program_injector_behavioral_channel_insufficient`
- Pair-level memory grounding passed: no
- Stage C2 allowed: no
- AppWorld generation/evaluation allowed from this result: no

## Hard Scope Observed

- Built and validated a pair-level teacher-response cache.
- Trained/evaluated single-memory program/injector diagnostics only.
- Bypassed the signed selector on purpose with `z(s,i)=p_i`.
- Did not train the selector.
- Did not use selector scores, selector gates, empirical `mu_i`, or full-bank
  aggregation in the primary model.
- Kept Qwen3-8B frozen.
- Used teacher-forced target scoring only.
- Did not retrain the previous Stage-C1 full-bank model.
- Did not run AppWorld generation/evaluation.
- Did not start Stage C2 or end-to-end RCMF training.

## Commands

Source tests:

```bash
python -m pytest -q tests/test_pair_grounding_5d.py
python -m pytest -q tests/test_stage_c1.py tests/test_pair_grounding_5d.py
```

Lambda run:

```bash
python scripts/run_stage_c_pair_grounding_5d.py \
  --config configs/benchmark/stage_c_pair_grounding_5d.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --stage-c1-response-cache-dir runs/stage_c1/response_cache_20260806_001 \
  --output-dir runs/stage_c/pair_grounding_5d_20260807_001 \
  --epochs 2 \
  --memory-cv-epochs 1 \
  --batch-size 1 \
  --eval-batch-size 1 \
  --seed 1 \
  --progress-interval-s 120
```

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/report.md`
- Pair response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`
- State-held-out checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/state_heldout`
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c_pair_grounding_5d_20260807_001.log`

## Pair Cache

- Version: `stage_c_pair_response_cache_5d_v1`
- Scoring definition: `single_raw_memory_pair_target_top64_delta_v1`
- Selected pairs: `1,728`
- Train pairs: `1,152`
- State-held-out validation pairs: `576`
- Reused compatible Stage-C1 rows: `88`
- Newly scored rows: `1,640`
- Validation passed: yes
- Missing category slots: `0`
- Category coverage:
  train `288/288/288/288`, validation `144/144/144/144` for
  positive/neutral/negative/random.

## Main Results

- Perturbation target selected from train-only smoke: `1.0`
- Zero-program equivalence: passed
- Tiny overfit: passed

State-held-out content metrics:

- Target NLL: `0.665915`
- Sparse teacher KL: `0.318875`
- Behavioral-delta Huber: `2.207829`
- u_text/u_program Spearman: `-0.293472`
- Positive/negative sign agreement: `0.403382`
- Improved fraction: `0.447917`
- Mean delta ratio: `1.054877`

Controls:

- Content minus shuffled-program target NLL: `-0.000166`
- Content minus memory-swap target NLL: `-0.000081`
- Content minus random-program sparse KL: `+0.010575`
- Content minus zero-field target NLL: `+0.031912`

Memory-held-out CV:

- Content target NLL mean/std: `0.757031/0.035778`
- Content sparse KL mean/std: `0.363415/0.015975`
- Content behavioral-delta Huber mean/std: `2.323665/0.086253`
- Content u_text/u_program Spearman mean/std: `-0.189175/0.052868`
- Positive Spearman folds: `0/5`

Program geometry:

- Content effective rank: `12.712268`
- Content pairwise cosine mean: `0.998634`
- Content norm mean: `11.313701`

## Decision

- Decision branch:
  `program_injector_behavioral_channel_insufficient`.
- Pair-level memory grounding did not pass.
- Stage C2 remains blocked.

Interpretation:

- The implementation has a live optimization path because zero-equivalence and
  tiny overfit passed.
- The learned content-derived program did not preserve memory identity enough:
  it did not beat shuffled or memory-swap controls meaningfully, correlated
  negatively with raw teacher utility, and failed held-out-memory
  generalization.
- Fixed-random and free-ID controls are competitive enough that the current
  result should not be treated as memory-content compilation.

## Next Step

- Run an oracle pair-latent injector capacity diagnostic before any renewed
  memory compiler or full-bank Stage-C training.
- If oracle per-pair `z` cannot reconstruct teacher deltas, redesign the
  injector or behavioral-delta loss.
- If oracle per-pair `z` succeeds, focus the next repair on memory
  representation and program compiler capacity.

## Runtime Status

- The `stage5d_exp014` tmux session is no longer active.
- GPU reported `0 MiB / 0%` after completion.
- Safe to terminate the Lambda process.

## Notes

- The tmux log ended with `EXIT:True` because the local PowerShell SSH wrapper
  expanded `$?` before the remote shell wrote the footer. The run itself
  completed normally and produced a validated summary/report.
