# Handoff: Stage C1 Signed Program Distillation

Date UTC: 2026-08-06T15:30:00Z

## Scope

Milestone 5 / Stage C1 was completed as a teacher-forced diagnostic only.

Not run:

- AppWorld environment interaction
- generated ReAct trajectories
- full `test_normal` evaluation
- Qwen fine-tuning
- signed selector fine-tuning
- joint selector/program/injector training
- Stage C2 or end-to-end RCMF training

## Source State

- Branch: `workflow/research-loop`
- Stage-C1 implementation commits:
  - `a5b1b9272d907d201786f1f1e078865a6f3f77d2`
  - `77173e24b12647abbdf7ed463e7c0c2b4506ac3d`
  - `e17002258ddb52bce3fa86117a33ed872df2fa5c`
  - `9f16010e7dddbcb99ccf5b404347cadacc44a6c8`
- Training source commit: `e17002258ddb52bce3fa86117a33ed872df2fa5c`
- Corrected evaluation source commit:
  `9f16010e7dddbcb99ccf5b404347cadacc44a6c8`
- Lambda project path: `/lambda/nfs/rcmf-persist/project`
- Lambda virtual environment: `/home/ubuntu/venvs/rcmf-py311`

## Commands

Response cache:

```bash
python scripts/build_stage_c1_response_cache.py \
  --config configs/benchmark/stage_c1_signed_program.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --output-dir runs/stage_c1/response_cache_20260806_001
```

Signed-program pilot:

```bash
python scripts/run_stage_c1_signed_program.py \
  --config configs/benchmark/stage_c1_signed_program.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --response-cache-dir runs/stage_c1/response_cache_20260806_001 \
  --signed-field-dir runs/stage_b/signed_field_4c_20260806_002 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --output-dir runs/stage_c1/signed_program_c1_20260806_002 \
  --seeds 1 2 3 \
  --epochs 3 \
  --batch-size 1 \
  --eval-batch-size 1 \
  --lr 0.0002 \
  --patience 2 \
  --device cuda
```

Corrected eval-only control recomputation:

```bash
python scripts/run_stage_c1_signed_program.py \
  --config configs/benchmark/stage_c1_signed_program.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --teacher-cache-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --labels-dir runs/stage_b/student_labels_20260806_002 \
  --response-cache-dir runs/stage_c1/response_cache_20260806_001 \
  --signed-field-dir runs/stage_b/signed_field_4c_20260806_002 \
  --representation-cache-dir runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache \
  --output-dir runs/stage_c1/signed_program_c1_20260806_002 \
  --seeds 1 2 3 \
  --epochs 3 \
  --batch-size 1 \
  --eval-batch-size 1 \
  --lr 0.0002 \
  --patience 2 \
  --eval-only-existing \
  --recompute-controls shuffled_state mean_state \
  --device cuda
```

## Artifacts

- Response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001`
- Signed-program pilot:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002`
- Checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/checkpoints`
- Final summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/summary.json`
- Final report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/report.md`
- Eval-fix log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c1_evalfix_20260806_002.log`

## Results

- Response cache validation: passed, 638 states, 0 errors.
- Response cache condition counts: 523 positive-teacher, 107
  baseline-teacher, 8 all-missing.
- Field algebra/reversibility: passed.
- Zero-delta equivalence: passed, max NLL delta `0.0`.
- Tiny overfit: passed.
- Full training runtime: `21,652.25` seconds, about `6.01` H100 hours.
- Corrected eval-only runtime: `486.21` seconds.
- Three-seed validation target NLL: `0.196607/0.012709`.
- Three-seed validation sparse teacher KL: `0.125854/0.011371`.
- Three-seed validation `L0 - student`: `0.335801/0.012709`.
- Improved validation fraction: `0.817746/0.006783`.
- No-positive validation degradation relative to bare Qwen: `0.028565`.
- Correct-minus-free-ID target-NLL delta: `+0.007893/0.012818`, so free-ID
  was better on average.
- Leave-one-out teacher-best removal effect: `0.0` on all 16 audited positive
  states.
- Decision branch:
  `signed_program_channel_not_behaviorally_useful_or_content_not_distinct`.
- Stage-C1 gate: failed.

## Deviations and Fixes

- Response cache first failed validation due to overly strict probability
  bucket tolerance after the rows were generated. Commit `77173e2` fixed the
  numeric validation and reran validation without changing the teacher
  definition or rescoring.
- Signed-program run `_001` exposed sparse teacher-KL instability at near-one
  union probability. Commit `e170022` fixed the numerical path.
- Initial `_002` summary had invalid shuffled/mean state controls for
  `eval_batch_size=1`. Commit `9f16010` fixed the controls and recomputed the
  affected metrics from existing checkpoints without retraining.

## Interpretation

VERIFIED:

- The program/injector gradient path is live.
- The correct content-derived field improves target NLL relative to bare Qwen.
- The result fails key content-specific controls and no-positive preservation.
- Removing the teacher-best memory has no measured effect in the current
  leave-one-out audit.

INFERENCES:

- The current program/injector path likely learns a generic state-control or
  injector shortcut rather than memory-content-specific behavior.
- The large injector delta ratios may be overwhelming memory-specific
  differences.

UNVERIFIED:

- Whether explicit per-memory behavioral supervision can repair Stage C.
- Whether any Stage-C variant improves generated AppWorld trajectories.

## Next Step

Do not launch Stage C2 or AppWorld evaluation. First diagnose the program
channel:

- per-memory contribution and z leave-one-out magnitude;
- content-derived versus free-ID program behavior at equal capacity;
- no-positive preservation under smaller injector perturbations;
- an explicit leave-one-out or teacher-delta objective.

## Safe Status

- Lambda tmux: no server running.
- Matching Python process: none.
- GPU: `0 MiB / 0%`.
- Safe to terminate the Lambda instance.
