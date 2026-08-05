# Primary Raw-Text Memory Teacher Pilot

Date: 2026-08-05.

## VERIFIED

- Branch: `workflow/research-loop`.
- Source commit used by the teacher cache:
  `e295a2bd449f38f87e4ad8d945e73aa55d0e5ef7`.
- Lambda artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001`.
- Teacher cache version: `raw_text_memory_teacher_labels_v1`.
- Teacher-only raw memory renderer version:
  `teacher_only_raw_memory_section_v1`.
- Model/checkpoint identity:
  `frozen_hf_pretrained:Qwen/Qwen3-8B`.
- Dataset:
  `/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`.
- Context limit: 40,960 tokens.
- No full student training and no full AppWorld evaluation were launched.

## Method

- For each selected decision state, compute L0, the mean target-token NLL of
  the ground-truth next action under the unchanged full-demo prompt.
- Insert one legal cross-task raw MemoryRecord into a clearly delimited
  teacher-only memory section.
- Compute Lj_text on the same target and define text utility as
  `L0 - Lj_text`.
- Legal candidates exclude same task, episode, replay, and lineage memory
  records.
- Candidate proposal is only a proposal mechanism. Utility labels come only
  from deterministic target-loss differences.
- Proposed candidates are the union of 2 highest frozen-Qwen representation
  cosine memories, up to 2 same-app memories from programmatic parsing, and 2
  deterministic random low-similarity memories.
- A 4-state audit subset scanned all legal memories to estimate whether the
  proposal procedure recalls the highest-utility memory.

## Token Preflight

- Selected states: 24.
- Proposed candidate pairs: 96.
- Unique pairs including audit rows: 260.
- Scored rows: 250.
- Over-context rows skipped: 10.
- No full-demo prompt, raw memory, or target was truncated.

Over-context pairs:

| state | memory | total tokens |
| --- | --- | ---: |
| `appworld:trace:2a163ab_1:step:4:line:24` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 44,773 |
| `appworld:trace:afc0fce_1:step:8:line:64` | `007f8489-ac8f-5555-8fda-325e4c83da50` | 43,966 |
| `appworld:trace:afc0fce_1:step:8:line:64` | `22ba7fd6-83da-5c74-8277-a15137b53db0` | 45,504 |
| `appworld:trace:afc0fce_1:step:8:line:64` | `3aa08204-ebfa-5dbc-8b45-f90e9b78ccb6` | 44,262 |
| `appworld:trace:76f2c72_1:step:6:line:197` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 44,728 |
| `appworld:trace:76f2c72_3:step:8:line:223` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 46,187 |
| `appworld:trace:229360a_2:step:6:line:344` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 46,107 |
| `appworld:trace:229360a_3:step:7:line:362` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 46,626 |
| `appworld:trace:7d7fbf6_3:step:9:line:411` | `076f5673-6565-5f20-aada-6f16a0f8d4b0` | 45,092 |
| `appworld:trace:aa8502b_2:step:10:line:620` | `22ba7fd6-83da-5c74-8277-a15137b53db0` | 42,497 |

## Results

- Positive utility rows: 71.
- Neutral utility rows: 11.
- Negative utility rows: 168.
- Utility mean: `-0.008413`.
- Utility std: `0.313950`.
- Utility percentiles: p05 `-0.339788`, p25 `-0.152369`, p50
  `-0.047554`, p75 `0.016794`, p95 `0.660061`.
- Utility min/max: `-1.060414` / `1.452909`.
- Utility vs raw memory length correlation: `0.075251`.
- Utility vs combined context length correlation: `0.081745`.
- Runtime: `498.29` seconds.
- Measured speed: `1.993` seconds per scored pair.
- Projected full-dataset candidate scoring cost: about `1.77` GPU hours.
- Projected full-dataset all-legal-memory scoring cost: about `16.25` GPU
  hours.

## Audit Recall

- Audited states: 4.
- Candidate recall of the highest-utility legal memory: `0/4 = 0.0`.

| state | legal memories | proposed memories | best utility | candidate best utility | recalled |
| --- | ---: | ---: | ---: | ---: | --- |
| `appworld:trace:229360a_3:step:7:line:362` | 44 | 4 | `0.874294` | `0.030056` | false |
| `appworld:trace:2a163ab_1:step:4:line:24` | 44 | 4 | `0.013934` | `-0.012086` | false |
| `appworld:trace:76f2c72_1:step:6:line:197` | 44 | 4 | `0.346637` | `-0.112502` | false |
| `appworld:trace:afc0fce_1:step:8:line:64` | 42 | 4 | `0.796625` | `0.184743` | false |

## Representative Rows

Positive:

- State `appworld:trace:b0a8eae_3:step:3:line:298`.
- Memory `28b694ab-6a47-515b-9dd7-b6386eb692ac` from task `cf6abd2_1`.
- Candidate source: `cosine_top2,same_app`.
- L0 `1.620341`, Lj_text `0.167432`, utility `1.452909`.
- Raw memory tokens `10,194`; total tokens with target `18,943`.

Neutral:

- State `appworld:trace:2a163ab_1:step:4:line:24`.
- Memory `3aa08204-ebfa-5dbc-8b45-f90e9b78ccb6` from task `229360a_3`.
- Candidate source: `audit_all_memory`.
- L0 `0.013946`, Lj_text `0.014042`, utility `-0.000095`.
- Raw memory tokens `23,821`; total tokens with target `33,031`.

Negative:

- State `appworld:trace:76f2c72_1:step:6:line:197`.
- Memory `ef830c7b-329b-52a0-9e67-733a3a8ec0d7` from task `771d8fc_2`.
- Candidate source: `audit_all_memory`.
- L0 `0.595841`, Lj_text `1.656254`, utility `-1.060414`.
- Raw memory tokens `13,668`; total tokens with target `22,836`.

## Additive-Token Smoke

- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/additive_token_position_smoke.json`.
- State: `appworld:trace:287e338_3:step:1:line:134`.
- Prompt tokens: 7,319. Target tokens: 19.
- Base loss: `1.401838`.
- `first_k`: selected token indices `[0,1,2,3]`, texts
  `<|im_start|>`, `user`, newline, `I`; zero-delta loss diff `0.0`.
- `last_prompt_k`: selected token indices `[7315,7316,7317,7318]`, texts
  `<think>`, double-newline, `</think>`, double-newline; zero-delta loss diff
  `0.0`.
- `last_user_k`: selected token indices `[7306,7307,7308,7309]`, texts
  ` me`, ` on`, ` Spotify`, `.`; zero-delta loss diff `0.0`.
- All three variants had zero max embedding delta and zero-delta-equivalent
  target loss.

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/summary.json`.
- Teacher labels:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/teacher_labels.jsonl`.
- Token preflight:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/token_length_preflight.json`.
- Pilot states:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/pilot_states.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/report.md`.
- Run log:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/run.log`.

## INFERENCES

- The raw-text teacher can identify high positive and high negative memory
  effects, so the target-loss-difference label itself is informative.
- The candidate proposal is not yet good enough to scale directly, because it
  missed the best legal memory in every audited state.
- Memory length and combined context length were only weakly correlated with
  utility in this pilot, so label sign is not merely a length artifact.

## UNVERIFIED

- The pilot does not prove label stability across larger samples or seeds.
- The pilot does not prove that these labels improve an RCMF student.
- The audit subset is small and should be expanded before committing to a full
  teacher-cache generation strategy.
