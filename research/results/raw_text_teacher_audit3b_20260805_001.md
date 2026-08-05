# Milestone 3B Expanded Raw-Text Teacher Audit

Date: 2026-08-05.

## VERIFIED

- Branch: `workflow/research-loop`.
- Source commit:
  `964063416a2fc3c48bf04bb11db7354fac96028c`.
- Lambda artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001`.
- Audit cache version: `raw_text_memory_teacher_audit3b_v1`.
- Model/checkpoint identity:
  `frozen_hf_pretrained:Qwen/Qwen3-8B`.
- Dataset:
  `/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`.
- Context limit: 40,960 tokens.
- No full teacher-cache generation, student training, or full AppWorld
  evaluation was launched.

## Scope

- Reused the existing 24 pilot states from Milestone 3.
- Reused 260 cached Milestone 3 rows.
- Scored all missing legal memories for those states, preserving exclusion of
  same task, episode, replay, and lineage.
- Recorded and masked over-context pairs. No truncation was performed.
- Ran full 638-state all-legal token preflight without scoring all pairs.

## 24-State Audit Results

- Legal pairs: 1,080.
- Scoreable rows: 1,052.
- Over-context rows masked: 28.
- Newly scored rows: 802.
- Utility counts: positive 364, neutral 122, negative 566.
- Utility mean/std: `0.047545` / `0.350456`.
- Utility percentiles: p05 `-0.358663`, p25 `-0.119575`, p50 `-0.018214`,
  p75 `0.088473`, p95 `0.847706`.
- Utility min/max: `-1.243614` / `1.620315`.
- Utility vs memory length correlation: `0.086112`.
- Utility vs combined context length correlation: `0.006909`.
- Runtime: `2,314.84` seconds.
- Measured new Lj_text scoring speed: `1.486616` seconds per pair.
- Measured L0 scoring speed from reproducibility repeats: `0.802833` seconds
  per state.

## Proposal Metrics

- Existing proposal recall@1: `1/24 = 0.041667`.
- Existing proposal recall@2: `1/24 = 0.041667`.
- Existing proposal recall@4: `1/24 = 0.041667`.
- Existing proposal recall@8: `1/24 = 0.041667`.
- Mean proposal regret: `0.275401`.
- Median proposal regret: `0.104668`.
- Max proposal regret: `1.108213`.
- Positive utility mass coverage: `0.107657`.
- Mean per-state positive utility mass coverage: `0.070604`.

Thresholded metrics:

| best legal utility threshold | states | recall@4 | mean regret | median regret | positive mass coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| `>=0.05` | 17 | `0.058824` | `0.363960` | `0.277801` | `0.107844` |
| `>=0.10` | 16 | `0.062500` | `0.381725` | `0.307190` | `0.107912` |
| `>=0.25` | 13 | `0.076923` | `0.441131` | `0.459139` | `0.108338` |

Source ablations:

| source | best-hit rate | mean regret | median regret | positive mass coverage |
| --- | ---: | ---: | ---: | ---: |
| `cosine_top2` | `1/24 = 0.041667` | `0.311817` | `0.149131` | `0.069089` |
| `same_app` | `1/24 = 0.041667` | `0.311817` | `0.149131` | `0.069089` |
| `random_low_similarity` | `0/24 = 0.0` | `0.384903` | `0.172149` | `0.038568` |

## Per-State Table

| state | L0 | best legal utility | best proposed utility | pos/neu/neg | over ctx | regret | r@4 | mass cov |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `appworld:trace:07b42fd_3:step:4:line:477` | 1.096929 | 0.821011 | 0.064631 | 30/1/13 | 1 | 0.756380 | false | 0.006663 |
| `appworld:trace:229360a_2:step:6:line:344` | 0.007376 | 0.007376 | -0.007340 | 0/20/24 | 1 | 0.014716 | false | 0.000000 |
| `appworld:trace:229360a_3:step:7:line:362` | 0.874297 | 0.874294 | 0.030056 | 14/0/30 | 1 | 0.844238 | false | 0.006780 |
| `appworld:trace:287e338_1:step:3:line:119` | 1.129781 | 1.129698 | 1.127969 | 43/0/1 | 1 | 0.001729 | false | 0.138752 |
| `appworld:trace:2a163ab_1:step:4:line:24` | 0.013946 | 0.013934 | -0.012086 | 2/2/40 | 1 | 0.026020 | false | 0.000000 |
| `appworld:trace:3c13f5a_2:step:11:line:491` | 1.010872 | 1.010388 | -0.097825 | 5/2/37 | 1 | 1.108213 | false | 0.000000 |
| `appworld:trace:6104387_3:step:15:line:55` | 0.006427 | 0.006421 | 0.006415 | 0/44/0 | 1 | 0.000006 | false | 0.095537 |
| `appworld:trace:76f2c72_1:step:6:line:197` | 0.595841 | 0.346637 | -0.112502 | 6/0/38 | 1 | 0.459139 | false | 0.000000 |
| `appworld:trace:76f2c72_2:step:5:line:210` | 1.092937 | 1.089767 | 1.051492 | 37/0/7 | 1 | 0.038275 | false | 0.111396 |
| `appworld:trace:76f2c72_3:step:8:line:223` | 0.895372 | 0.125601 | 0.030505 | 22/2/20 | 1 | 0.095096 | false | 0.022616 |
| `appworld:trace:771d8fc_1:step:7:line:421` | 0.178308 | 0.178305 | -0.099496 | 4/0/40 | 1 | 0.277801 | false | 0.000000 |
| `appworld:trace:771d8fc_2:step:3:line:431` | 0.002702 | 0.002630 | -0.227427 | 0/4/40 | 1 | 0.230057 | false | 0.000000 |
| `appworld:trace:7d7fbf6_1:step:5:line:388` | 1.507315 | 1.507107 | 0.669493 | 37/0/7 | 1 | 0.837614 | false | 0.050144 |
| `appworld:trace:7d7fbf6_3:step:9:line:411` | 0.854706 | 0.414583 | 0.078003 | 31/4/9 | 1 | 0.336580 | false | 0.032464 |
| `appworld:trace:aa8502b_2:step:10:line:620` | 0.078464 | 0.000939 | -0.013130 | 0/19/23 | 3 | 0.014069 | false | 0.000000 |
| `appworld:trace:afc0fce_1:step:8:line:64` | 1.714340 | 0.796625 | 0.184743 | 19/2/21 | 3 | 0.611881 | false | 0.080147 |
| `appworld:trace:b0a8eae_1:step:4:line:257` | 0.521421 | 0.521414 | 0.521403 | 34/0/10 | 1 | 0.000011 | false | 0.126176 |
| `appworld:trace:b0a8eae_2:step:17:line:294` | 0.357386 | 0.032020 | -0.082220 | 1/3/40 | 1 | 0.114240 | false | 0.000000 |
| `appworld:trace:b0a8eae_3:step:3:line:298` | 1.620341 | 1.620315 | 1.452909 | 14/4/26 | 1 | 0.167407 | false | 0.422327 |
| `appworld:trace:b7a9ee9_2:step:13:line:178` | 0.142179 | 0.049767 | 0.026577 | 25/9/10 | 1 | 0.023190 | false | 0.091837 |
| `appworld:trace:c901732_1:step:6:line:568` | 0.211372 | 0.061520 | -0.018202 | 2/2/40 | 1 | 0.079721 | false | 0.000000 |
| `appworld:trace:c901732_3:step:3:line:578` | 0.453723 | 0.453723 | -0.119509 | 4/1/39 | 1 | 0.573232 | false | 0.000000 |
| `appworld:trace:cf6abd2_2:step:5:line:515` | 0.204865 | 0.204865 | 0.204864 | 23/0/21 | 1 | 0.000000 | false | 0.137088 |
| `appworld:trace:e7a10f8_2:step:6:line:550` | 0.744759 | 0.744756 | 0.744756 | 11/3/30 | 1 | 0.000000 | true | 0.372570 |

## Reproducibility

Fixed positive, neutral, and negative rows were rescored deterministically.

| category | state | memory | L0 diff | Lj diff | utility diff |
| --- | --- | --- | ---: | ---: | ---: |
| positive | `appworld:trace:b0a8eae_3:step:3:line:298` | `eae250ca-3524-5821-9f35-3f48c30f5612` | 0.0 | 0.0 | 0.0 |
| neutral | `appworld:trace:3c13f5a_2:step:11:line:491` | `ef830c7b-329b-52a0-9e67-733a3a8ec0d7` | 0.0 | 0.0 | 0.0 |
| negative | `appworld:trace:b0a8eae_1:step:4:line:257` | `c36d723a-3ab4-5777-8fc2-92fcec2e488f` | 0.0 | 0.0 | 0.0 |

## Prompt Inspection

- Inspected rows: 6.
- Obvious issue count: 0.
- High-positive rows checked:
  `eae250ca-3524-5821-9f35-3f48c30f5612`,
  `04738f66-ac89-5b21-8265-efdfa90396d8`,
  `524eab83-5520-5d42-8bd3-751abd3dee72`.
- High-negative rows checked:
  `c36d723a-3ab4-5777-8fc2-92fcec2e488f`,
  `188b5566-89f8-5e15-8fa1-20199ceb9680`,
  `ef830c7b-329b-52a0-9e67-733a3a8ec0d7`.
- Checks covered leakage key overlap, teacher/current-state delimiter counts,
  section order, memory id/task id presence, target hash, and memory text hash.

## Full-Cache Preflight

- Total states: 638.
- Memory records: 46.
- Exact legal pairs: 28,710.
- Exact scoreable pairs: 27,054.
- Exact over-context pairs: 1,656.
- Over-context fraction: `0.057680`.
- Preflight runtime: `1,077.62` seconds.
- Estimated complete all-legal scoring wall time: `40,731.11` seconds.
- Estimated complete all-legal cache GPU cost: `11.31` H100 hours.

## Recommendation

Recommendation: A, generate the complete all-legal teacher cache after user and
ChatGPT review.

Rationale: the local-Qwen target-loss teacher is reproducible, representative
prompts did not show obvious leakage or delimiter errors, positive and negative
utility signal exists, and all-legal scoring removes the candidate-recall
bottleneck instead of depending on it. The estimated cost is moderate at about
11.31 H100 hours.

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/summary.json`.
- Audit labels:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/teacher_labels_audit3b.jsonl`.
- Per-state JSON:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/per_state_table.json`.
- Per-state CSV:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/per_state_table.csv`.
- Full-cache preflight summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/full_cache_preflight_summary.json`.
- Full-cache preflight rows:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/full_cache_preflight_rows.jsonl`.
- Reproducibility check:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/reproducibility_check.json`.
- Prompt inspection:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/representative_prompt_inspection.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/report.md`.
- Run log:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/run.log`.

## INFERENCES

- The existing proposal mechanism is poor, but all-legal scoring is viable
  enough to review as the next step.
- Some recall misses have tiny regret, so exact top-1 recall alone is too harsh
  a decision metric.
- The local-Qwen raw-text teacher should not be abandoned based on this audit.

## UNVERIFIED

- Whether complete all-legal labels improve student training.
- Whether masked over-context pairs are acceptable for the final teacher cache.
- Whether the 24-state utility distribution remains stable over all 638 states.
