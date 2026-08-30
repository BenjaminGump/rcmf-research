# EXP-034A Structured Handoff

Timestamp: `2026-08-30T00:15:00Z`

## Identity

- Branch: `research/v5-rcmf-one-demo-retrain`
- Starting SHA: `a2c41652bfea380cfef89df0bee0e4d919458982`
- Archive verification: `archive/exp033a-one-demo-dev-eval-a2c41652` -> exact starting SHA
- Run UUID: `rcmf_exp031a_one_demo_retrain_11b_20260829_001`
- Seed: `25101`
- Final record commit: `FINAL_RECORD_COMMIT_PENDING`

## What Changed

The only scientific change was training-side `full_demo` to the already verified `full_demo_first_only`. Prompt-independent memory artifacts were hash-validated and reused; all prompt-dependent state, selection, outcome, teacher, zero-cache, training-unit, heldout, and dev artifacts were rebuilt. Qwen and the selector stayed frozen.

## Selected System

- Epoch 2 checkpoint SHA256: `078dcd0e3b729877a5b9994ed515cb0867601ef72381b7d8b7705dfb66cd56ef`
- New deployment field SHA256: `f24b16e4af6f1c59b0f59984b551eb590d80f09e6f380f30c370c8d5593d2fc7`
- Field: 499 memories, `A=[960,8,256]`, `B=[8,256]`, no runtime memory scan/retrieval/per-memory scoring
- Epoch 1 heldout: `PARTIAL`, score `0.0102041`
- Epoch 2 heldout: `STRONG`, score `0.0765306`; selected by the unchanged heldout-only rule

## Main Results

- D0 reused bare: `12/57`
- Old EXP-033A D1: `17/57`
- N1 retrained correct: `16/57`
- N2 retrained shuffle: `16/57`
- N1-D0: `+4/57`, 95% CI `[-2,+10]/57`, McNemar `p=0.34375`
- N1-N2: `0/57`, 95% CI `[-4,+4]/57`, McNemar `p=1.0`
- N1-old-D1: `-1/57`, 95% CI `[-7,+5]/57`, McNemar `p=1.0`
- N1 retained 5/11 old gains and recovered 3/6 old losses.

Descriptive classification: N1 exceeds D0 in point estimate; N1 does not exceed N2; N1 does not improve old D1. All intervals include zero. No new architecture conclusion or follow-on run is authorized.

## Training And Runtime

- State universe: 366 train / 98 heldout; fixed-state SHA256 `4523c5f98d45badf5d523cfe22c5f53337beef6b87c9dd344481edf6607ce484`
- Labels, train: H37/N218/P111; heldout: H8/N61/P29; 138 labels changed from three-demo
- Trainable parameters: 26,810,368
- Backwards: 1,176; epochs: 2
- Final recent losses: epoch 1 `0.178266`, epoch 2 `0.104328`
- Accounted H100-active time: `9.1179 h`
- Ledger wall span: `10.8150 h`
- Attempts: 27 total, 4 failed, 0 open

## Verification And Artifacts

- Final local tests: `726 passed, 1 skipped`
- Final Lambda tests: `727 passed`
- Audit index SHA256: `5c9ce71b618a189a502e90cd76a1e856d5bbb7f22d4a6af482f939614b4fbf2b`
- Git-safe audit: `research/audits/rcmf_exp031a_one_demo_retrain_11b_20260829_001/`
- Machine results: `research/results/exp034a_rcmf_one_demo_retrain/`
- Human report: `research/results/EXP_034A_RCMF_ONE_DEMO_RETRAIN.md`
- Lambda raw root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_exp031a_one_demo_retrain_11b_20260829_001`
- Raw JWT matches: 0; registered sensitive-observation leaks: 0

## Deviations

- Three gate-free compatibility preflights failed before producing rows.
- N1's first attempt produced all 57 rows and failed only in summary labeling; the resume reused all 57 rows and generated none.
- The first uncommitted audit export omitted three provenance fields and was quarantined; the final strict export restored them.
- Windows `apply_patch` and default pytest temp-directory helpers failed; guarded edits and isolated basetemp were used, with complete tests passing.

## Stop

EXP-034A is complete. Do not start EXP-035, retraining, calibration, first37, test-normal, test-challenge, another prompt profile, or architecture work from this handoff.