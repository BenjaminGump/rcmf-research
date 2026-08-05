# Milestone 3C Complete All-Legal Raw-Text Teacher Cache

Date: 2026-08-05.

## VERIFIED

- Branch: `workflow/research-loop`.
- Source implementation commit:
  `80bebb05d97ec7d156b87850a7f1fd2811874d8a`.
- Lambda artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- Cache version: `raw_text_memory_teacher_full_cache_v1`.
- Scoring definition:
  `frozen_qwen_full_demo_raw_memory_mean_target_nll_v1`.
- Dataset:
  `/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`.
- No RCMF student training or full AppWorld evaluation was launched.
- No prompt, target, or raw memory text was truncated.

## Command

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_raw_text_teacher_full_cache.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --pilot-dir runs/teacher/raw_text_pilot_20260805_001 \
  --audit3b-dir runs/teacher/raw_text_audit3b_20260805_001 \
  --output-dir runs/teacher/raw_text_full_cache_20260805_001 \
  --progress-interval-s 300
```

The command ran inside tmux session `raw_teacher_full_cache_20260805` and
wrote stdout/stderr to
`/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/run.log`.

## Counts and Runtime

- States: 638.
- Memory records: 46.
- Exact legal pairs: 28,710.
- Scoreable pairs: 27,054.
- Over-context pairs: 1,656.
- Cached compatible rows reused: 1,080.
- Newly scored pairs: 26,002.
- Newly generated over-context rows: 1,628.
- Retried pairs: 0.
- Failed pairs: 0.
- Runtime: `36,949.37` seconds.
- Actual H100 hours: `10.26`.

Cache reuse validation:

- Candidate cached rows seen: 1,340.
- Unique compatible pairs: 1,080.
- Duplicate compatible rows: 260.
- Duplicate inconsistent rows: 0.
- Rejected rows: 0.

## Validation

- `validation.json` passed.
- Duplicate state-memory keys: 0.
- Missing legal keys: 0.
- Unexpected legal keys: 0.
- Count errors: 0.
- All scoreable rows had finite L0, Lj_text, and utility.
- All over-context rows had null utility and `valid_for_loss=false`.
- Scoreable plus over-context rows equaled exact legal pairs.
- Representative inspection selected 30 rows and found 0 obvious issues.
- Deterministic reproducibility check passed for positive, neutral, and
  negative rows with all repeated L0/Lj/utility diffs equal to `0.0`.

## Utility Summary

- Positive rows: 13,426 (`0.496267`).
- Neutral rows: 4,861 (`0.179678`).
- Negative rows: 8,767 (`0.324056`).
- Mean/std: `0.085425` / `0.335507`.
- Percentiles: p05 `-0.352794`, p25 `-0.056140`, p50 `0.009323`,
  p75 `0.179088`, p95 `0.750906`.
- Min/max: `-1.996295` / `2.333721`.

Selected stratified means:

| stratum | rows | mean utility |
| --- | ---: | ---: |
| early step | 10,018 | `0.186201` |
| middle step | 8,346 | `0.052984` |
| later step | 8,690 | `0.000404` |
| short prompt | 9,377 | `0.195474` |
| medium prompt | 9,374 | `0.042738` |
| long prompt | 8,303 | `0.009332` |
| short memory | 9,871 | `0.069703` |
| medium memory | 9,078 | `0.081424` |
| long memory | 8,105 | `0.109052` |

## Missingness

- States with no positive valid memory: 113.
- States with no negative valid memory: 94.
- State valid-memory count min/median/max: `0/44/45`.
- State over-context memory count min/median/max: `0/1/45`.
- Memory valid-state count min/median/max: `0/607/623`.
- Memory over-context state count min/median/max: `8/21/602`.
- Over-context pairs by memory length bucket:
  long 1,178, medium 311, short 167.
- Largest missing-memory contributor:
  `076f5673-6565-5f20-aada-6f16a0f8d4b0`, over-context for 602 legal states,
  valid for 0 states.
- Top over-context states were late `afc0fce_1` steps with 45 over-context
  memories and 0 valid memories.

## Overlap Diagnostics

- Utility vs shared API count correlation: `0.133437`.
- Utility vs shared state API count correlation: `-0.043177`.
- Utility vs normalized target exact-substring-in-memory correlation:
  `0.074342`.
- Utility vs shared code-token count correlation: `0.158156`.
- Utility vs code-token Jaccard correlation: `0.138547`.
- Utility vs memory code-token count correlation: `0.068035`.
- Utility vs target code-token count correlation: `0.048961`.
- Same-app stratum was all rows under the current AppWorld data/app parsing,
  so same-app correlation was null.

Rows where the normalized target appeared exactly in raw memory had higher mean
utility (`0.125319`) than rows without the exact substring (`0.069831`), but
high-overlap rows still included low and negative utility examples. This is
useful signal, not a replacement for target-loss teacher labels.

## Audit3B Comparison

- Full-cache sign proportions positive/neutral/negative:
  `0.496267/0.179678/0.324056`.
- Audit3B sign proportions positive/neutral/negative:
  `0.346008/0.115970/0.538023`.
- Full mean utility minus audit3B mean utility: `0.037879`.
- Maximum sign-proportion absolute difference: `0.213967`.
- Full utility vs combined-context length correlation: `-0.091520`.
- Audit3B utility vs combined-context length correlation: `0.006909`.
- Full utility vs memory length correlation: `0.074633`.
- Audit3B utility vs memory length correlation: `0.086112`.
- Conclusion recorded by the script: the 24-state audit was not fully
  representative by the configured sign-proportion and mean-utility thresholds.

## Student Split Manifest

- Manifest:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/student_split_manifest.json`.
- Version: `raw_text_teacher_future_student_split_manifest_v1`.
- Seed: 13.
- Grouping: `task_id`.
- Total tasks: 46.
- Train tasks: 37.
- Validation tasks: 9.
- Train states: 499.
- Validation states: 139.
- No student training was launched.

## Artifacts

- Cache JSONL:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/teacher_cache_full_rows.jsonl`.
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/summary.json`.
- Validation:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/validation.json`.
- Reproducibility:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/reproducibility_check.json`.
- Representative inspection:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/representative_inspection.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/report.md`.
- Progress:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/progress.json`.
- Run log:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/run.log`.

## INFERENCES

- The complete cache supports continuing the raw-text teacher path to review:
  labels are reproducible, broad, and not obviously explained by leakage or
  exact target copying.
- The full-cache distribution is materially different from the 24-state audit,
  so future decisions should use full-cache statistics where available.
- Over-context missingness should be handled explicitly during student-label
  construction, especially for late `afc0fce_1` states and the fully masked
  memory `076f5673-6565-5f20-aada-6f16a0f8d4b0`.

## UNVERIFIED

- Whether training an RCMF student on this cache improves AppWorld accuracy.
- Whether removing or separately handling all-over-context states improves
  downstream student training.
