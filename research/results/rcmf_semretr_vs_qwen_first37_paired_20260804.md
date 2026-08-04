# RCMF Semantic Retrieval vs Bare Qwen First-37 Paired Comparison

Date: 2026-08-04.

## VERIFIED

- Baseline artifact:
  `/lambda/nfs/rcmf-persist/project/runs/experiments/qwen_appworld_full_prompt_context40_newline_full_20260731_235900/evaluate/test`.
- Candidate artifact:
  `/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_full_prompt_filtered_no_2a163ab3_semretr_full_20260803_172000/evaluate/test`.
- Candidate run contains 37 completed task JSON files.
- Baseline run contains 168 completed task JSON files.
- Paired comparison was computed on the 37 candidate-completed task ids.

## Result

- Baseline success on paired tasks: `10/37 = 27.03%`.
- RCMF candidate success on paired tasks: `7/37 = 18.92%`.
- Retained baseline successes: `5`.
- Lost baseline successes: `5`.
- Gained over baseline: `2`.
- Both failed: `25`.

Retained:

- `0d01c76_1`
- `0d01c76_2`
- `29a7b7e_1`
- `325d6ec_1`
- `325d6ec_3`

Lost:

- `0d01c76_3`
- `29a7b7e_2`
- `8749218_1`
- `8749218_2`
- `8749218_3`

Gained:

- `325d6ec_2`
- `634f342_1`

## Paired Rows

| task_id | baseline_success | rcmf_success | baseline_steps | rcmf_steps |
| --- | --- | --- | ---: | ---: |
| `0d01c76_1` | true | true | 13 | 18 |
| `0d01c76_2` | true | true | 15 | 15 |
| `0d01c76_3` | true | false | 13 | 50 |
| `21abae1_1` | false | false | 8 | 9 |
| `21abae1_2` | false | false | 12 | 12 |
| `21abae1_3` | false | false | 10 | 10 |
| `29a7b7e_1` | true | true | 36 | 13 |
| `29a7b7e_2` | true | false | 14 | 16 |
| `29a7b7e_3` | false | false | 50 | 16 |
| `2d9f728_1` | false | false | 17 | 14 |
| `2d9f728_2` | false | false | 16 | 50 |
| `2d9f728_3` | false | false | 15 | 19 |
| `325d6ec_1` | true | true | 17 | 15 |
| `325d6ec_2` | false | true | 15 | 8 |
| `325d6ec_3` | true | true | 20 | 15 |
| `3d9a636_1` | false | false | 20 | 24 |
| `3d9a636_2` | false | false | 50 | 26 |
| `3d9a636_3` | false | false | 19 | 50 |
| `634f342_1` | false | true | 50 | 28 |
| `634f342_2` | false | false | 19 | 50 |
| `634f342_3` | false | false | 50 | 50 |
| `6f4b9a5_1` | false | false | 50 | 15 |
| `6f4b9a5_2` | false | false | 9 | 9 |
| `6f4b9a5_3` | false | false | 50 | 50 |
| `8749218_1` | true | false | 11 | 9 |
| `8749218_2` | true | false | 13 | 11 |
| `8749218_3` | true | false | 13 | 9 |
| `d18139b_1` | false | false | 25 | 50 |
| `d6ac34d_1` | false | false | 8 | 10 |
| `d6ac34d_2` | false | false | 8 | 11 |
| `d6ac34d_3` | false | false | 8 | 14 |
| `fd1f8fa_1` | false | false | 13 | 12 |
| `fd1f8fa_2` | false | false | 14 | 13 |
| `fd1f8fa_3` | false | false | 10 | 10 |
| `ff58e36_1` | false | false | 17 | 50 |
| `ff58e36_2` | false | false | 14 | 15 |
| `ff58e36_3` | false | false | 50 | 50 |

## INFERENCES

- The semantic-retrieval RCMF run improved two tasks that bare Qwen failed but
  also disrupted five tasks that bare Qwen solved.
- The candidate is below the locked bare-Qwen baseline on this paired slice, so
  it should not be treated as a successful full-run direction without additional
  diagnostics and a corrected next iteration.

## UNVERIFIED

- This report does not inspect per-step prompts or observations for the lost
  and gained tasks.
- This report does not rerun any task; it only reads existing Lambda artifacts.
