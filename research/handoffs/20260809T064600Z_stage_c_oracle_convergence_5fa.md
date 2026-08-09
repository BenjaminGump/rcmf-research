# Handoff: Milestone 5F-A / EXP-016A Convergence-Corrected Oracle

Date: 2026-08-09

Branch: `workflow/research-loop`

Source commit: `451b7a763dd3ca0a08ff7cf430d2d2e5b16396c8`

Final record commit: pending at handoff creation time.

## Objective

Reassess the K=4 `last_user_k` direct input-embedding DeltaE channel with
adequate, explicit updates per pair and a sequence-level utility objective.

## Scope Status

VERIFIED:

- Qwen3-8B stayed frozen and only teacher-forced target scoring was used.
- No new injection site, memory compiler, signed selector, full-bank model,
  Stage C2, AppWorld generation/evaluation, or end-to-end training was run.
- The student prompt contained no raw memory and accessed no selector payload.

## Source And Command

- Config: `configs/benchmark/stage_c_oracle_convergence_5fa.yaml`.
- Source commit: `451b7a763dd3ca0a08ff7cf430d2d2e5b16396c8`.
- Formal command:

```bash
python scripts/run_stage_c_oracle_convergence_5fa.py \
  --config configs/benchmark/stage_c_oracle_convergence_5fa.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --pair-cache-dir runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache \
  --stage5e-dir runs/stage_c/oracle_capacity_5e_20260808_001 \
  --output-dir runs/stage_c/oracle_convergence_5fa_20260808_001 \
  --pilot-pair-count 64 --full-pair-count 192 \
  --pilot-min-updates 64 --pilot-max-updates 128 \
  --confirmation-updates 64 --batch-size 1 \
  --direct-lr 0.05 --pair-z-lr 0.002 --seed 1 \
  --progress-interval-s 300
```

## Validation

- Pair cache: `1,728 / 1,728` valid, zero errors.
- Teacher utility identity: passed; max error `1.001358e-06`.
- Zero DeltaE equivalence: passed; max utility error `1.192093e-07`.
- Per-pair update accounting: exact at every checkpoint; all rows equal.
- Student prompt contract: passed for all `192` confirmation pairs.
- Relevant tests: local `35 passed`; Lambda `35 passed`.

## Key Results

- The original Stage-5E direct result is preserved and superseded as
  `underoptimized_two_update_result` for capacity interpretation only.
- Pilot selected `sequence_utility_plus_sparse_kl` because it reached the
  documented u64 plateau. Pure sequence utility reached better u128 Huber
  `0.083021` but was still improving.
- Ratio 0.5 confirmation at u64: Spearman `0.955679`, sign `0.992806`,
  sequence Huber `0.103908`, ratio mean `0.495321`; not plateaued.
- Ratio 1.0 confirmation at u64: Spearman `0.976238`, Pearson `0.975828`,
  sign `1.0`, sequence Huber `0.054151`, target-delta correlation `0.820359`,
  sparse KL `0.166327`, ratio mean `0.973289`; not plateaued.
- Ratio 1.0 reduced sequence Huber by `89.4905%` versus zero and reproduced
  positive/negative directions perfectly on the non-neutral pairs.
- Ratio 1.0 remained materially improving from u48 to u64: Huber improved
  `22.5965%`. Therefore the direct gate did not formally pass.
- Matched-random ratio-1.0 control: Spearman `-0.001699`, sign `0.532374`,
  Huber `0.516598`.

## Decision

Reached branch: `oracle_not_converged_extend_updates`.

- The old Stage-5E `direct_delta_fails` interpretation is superseded.
- The Stage-5E sparse-objective mismatch remains verified.
- Immediate bottleneck: `convergence_not_reached`.
- Do not redesign the injection site yet.
- Do not start the 128D pair-z check, memory compiler work, Stage C2, or
  AppWorld evaluation until the direct oracle reaches a documented plateau.

## Next Recommended Milestone

EXP-016B should resume the ratio-1.0 selected-objective checkpoint from u64 and
extend to u128 or beyond with fixed 16-update reports and a predetermined
train-only optimization-stabilization rule. Stop if it is still improving.
After a plateau passes the direct gate, request review before starting a
properly optimized 128D injector/decoder capacity audit.

## Outputs

- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001`.
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/summary.json`.
- Detailed GitHub-safe report:
  `research/results/stage_c_oracle_convergence_5fa_20260808_001.md`.
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c_oracle_convergence_5fa_20260808_001.log`.
- Best confirmation checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt`.

## Runtime And Status

- Runtime: `83,929.064 s`, approximately `23.3136 H100 hours`.
- Formal process exited with status `0`.
- No tmux server or active Stage-5F-A process remains.
- GPU status after completion: `0 MiB / 0%`.
- Safe to terminate: yes.
