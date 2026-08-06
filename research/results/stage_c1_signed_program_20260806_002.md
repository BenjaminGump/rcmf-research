# Stage C1 Signed Program Distillation

Date UTC: 2026-08-06

## VERIFIED

- Scope respected: no AppWorld generation/evaluation, no Qwen fine-tuning, no
  signed-selector fine-tuning, no Stage C2, and no end-to-end RCMF training.
- Response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001`.
- Signed-program artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002`.
- Response cache version: `stage_c1_best_raw_memory_response_cache_v1`.
- Response scoring definition:
  `best_raw_memory_or_bare_qwen_target_top64_v1`.
- Response cache validation passed: 638 states, 0 errors.
- Response cache conditions: 523 positive-teacher, 107 baseline-teacher, 8
  all-missing. Train split had 491 valid Stage-C rows; validation had 139.
- Response cache file size: 115,276,788 bytes.
- Training source commit: `e17002258ddb52bce3fa86117a33ed872df2fa5c`.
- Corrected evaluation source commit:
  `9f16010e7dddbcb99ccf5b404347cadacc44a6c8`.
- Full training runtime: 21,652.25 seconds, about 6.01 H100 hours.
- Eval-only corrected-control recomputation runtime: 486.21 seconds.

## Trainable and Frozen Modules

- Frozen: Qwen3-8B, signed state query, memory key, signed temperature,
  empirical train-derived `mu_i`, and activation gate.
- Trainable: content-derived program head and additive-token injector.
- Injector: `additive_token`, position `last_user_k`, K=4.
- Student prompt contained no raw memory text.

## Validation Metrics

Three-seed mean/std for the correct content-derived field:

- target NLL: `0.196607/0.012709`
- sparse teacher KL: `0.125854/0.011371`
- `L0 - student`: `0.335801/0.012709`
- improved fraction: `0.817746/0.006783`
- worsened by >0.01: `0.071942/0.020348`
- worsened by >0.10: `0.007194/0.0`
- worsened by >0.50: `0.004796/0.003391`

Control deltas are correct minus control. Negative target-NLL deltas mean the
correct field has lower NLL than the control:

- bare Qwen / zero field: `-0.335801/0.012709`
- fixed random programs: `-0.310052/0.014674`
- shuffled programs: `-0.092314/0.029326`
- shuffled states: `-0.053176/0.022210`
- mean programs: `-0.103837/0.051612`
- global-prior-only coefficients: `-0.021917/0.022167`
- free-ID programs: `+0.007893/0.012818`

The content-derived program did not beat the free-ID program control.

## Geometry and Injection

- Program centered effective rank: `10.774106/1.763191`.
- z centered effective rank: `3.572149/0.234162`.
- Program pairwise cosine means by seed: `0.556960`, `0.744982`,
  `0.563268`.
- z pairwise cosine means by seed: `0.621175`, `0.397926`, `0.590379`.
- Trained injector mean delta norm by seed: `27.442460`, `27.420821`,
  `24.831388`.
- Trained injector mean delta ratio by seed: `8.140082`, `8.110864`,
  `7.344189`.
- Zero-delta equivalence passed with max target-NLL delta `0.0`.
- Selected injection token IDs/text:
  `[40537, 8017, 6733, 13]` /
  `[" Spotify", " album", " library", "."]`.

## Leave-One-Out Audit

- Audited 16 positive validation states.
- Teacher-best removal mean NLL delta: `0.0`.
- Teacher-best-hurts-more fraction: `0.0`.
- Teacher utility versus compiled leave-one-out effect correlation: undefined.

This is the clearest negative result: the trained program/injector path
improves teacher-forced NLL, but the behavior is not tied to removing the
specific teacher-best memory.

## Decision

- Stage-C1 gate: failed.
- Decision branch:
  `signed_program_channel_not_behaviorally_useful_or_content_not_distinct`.
- Stage C2 allowed: false.

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/report.md`
- Checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002/checkpoints`
- Response cache summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001/summary.json`
- Response cache rows:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001/response_cache.jsonl`
- Eval-fix log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c1_evalfix_20260806_002.log`

## INFERENCES

- The program/injector path is live and can reduce target loss, but current
  training likely permits a generic state-control shortcut.
- The large injector delta ratios and zero leave-one-out effects suggest that
  the aggregate z or injector is not expressing memory-specific program
  semantics strongly enough.

## UNVERIFIED

- Whether a repair with explicit per-memory behavioral supervision can make
  leave-one-out effects track teacher utility.
- Whether any Stage-C variant improves generated AppWorld trajectories. No
  generation or environment interaction was run in this milestone.
