# EXP-037A-R19 Structured Handoff

## Status

- Decision: `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`
- Formal 14n result: unchanged
- Formal selected checkpoint: epoch 1, unchanged
- Training: none (`0` backward, `0` optimizer steps)
- Follow-on experiment: none

## Git

- Starting branch: `research/v6-rcmf-exp037a-continuation-semantic-dispatch-repair`
- Starting SHA: `137b68f443c8cc7b02814bd97123a416e139da87`
- Diagnostic branch: `research/v6-rcmf-exp037a-r19-epoch2-sensitivity`
- Diagnostic implementation SHA: `2a7f371f378eab42e42629a4fd5275e18b2814ce`
- Formal launch source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`
- Formal archive: `archive/exp037a-r18-launch-source-98f917d`

## Formal verification

- Formal UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Formal root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Strict completions: `18/18 PASS`
- Epoch-1 checkpoint: `c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c`
- Epoch-2 checkpoint: `1db373ba6399aac1adcd6fd721a05e79f3a9acfc39d8a03f2e0a5d8c49ea1457`
- Parent closure: `922/922`, SHA256 `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Formal-root pre/post immutability snapshots: identical

## Formal selection evidence

- Epoch 1: `STRONG`, eligible, score `0.06325301204819277`, correct policy KL
  `0.09877849346344465`, positive tasks `4/8`, stable generation.
- Epoch 2: `PARTIAL`, eligible, score `0.0391566265060241`, correct policy KL
  `0.08656201020468038`, positive tasks `5/8`, stable generation.
- Frozen selection ranks `STRONG` above `PARTIAL`; official dev and complete
  heldout trajectories did not change the choice.

## Control integrity

- Formal epoch-1 correct/shuffle audit: PASS.
- Checkpoint identical, 499-memory key/payload/rho sets identical, permutation
  bijective, fixed points `0`, condition/task/config identities matched.
- Permutation SHA256: `96f1f3784c73e45bc9036b40aa83f43eca8e03b39995cd6f9fc29cd7790c7db2`.
- Recomputed formal success counts: correct `8/57`, shuffle `18/57`.
- No task output was reused or hard-linked across conditions.

## Diagnostic execution

- Root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r19_epoch2_forced_dev_20260908_001`
- H100: NVIDIA H100 80GB HBM3
- Seed: `25101`
- Approximate end-to-end wall span: `4.2 h`; measured field/evaluation
  stage-wall sum: `3.79 h`, below the 10-hour cap.
- Epoch-1 field calibration: exact scientific tensor/hash identity PASS.
- Epoch-2 correct field: fresh 401 and 499-memory construction PASS.
- Epoch-2 shuffle field: fresh matched zero-fixed-point construction PASS.
- Correct: `16/57`, 1,339 steps, 13,335,717 prompt tokens, 130,078
  completion tokens, 8 overflows, 0 execution exceptions.
- Shuffle: `19/57`, 1,441 steps, 13,400,740 prompt tokens, 150,480
  completion tokens, 8 overflows, 0 execution exceptions.
- Correct-only/shuffle-only/both/neither: `4/7/12/34`.
- Exact two-sided McNemar p: `0.548828125`.
- Correct minus bare: `+4/57`; correct minus shuffle: `-3/57`.
- Correct minus epoch-1 correct: `+8/57`; shuffle minus epoch-1 shuffle:
  `+1/57`; correct minus 3D correct: `-1/57`.

## Interpretation

Epoch 2 improves correct over bare but does not establish matched-shuffle
specificity. The shuffle anomaly persists at epoch 2. This is post-hoc,
descriptive evidence and does not promote epoch 2 or change formal selection.
A multi-permutation sensitivity study is a possible later preregistered task,
not an authorized continuation of R19.

## Evidence

- Report: `research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md`
- Git-safe machine records: `research/results/exp037a_r19_epoch2_sensitivity/`
- Audit index: `research/audits/exp037a_r19_epoch2_forced_dev_20260908_001/index.json`
- Lambda artifact-index SHA256: `fb8a6d6e712c2d612e467a9f3d050bb54fca46a52abdc4c949883d6692cb2189`
- Lambda final-result SHA256: `e9cf96f454299601a1b2f76bc6d706e191fc67b266dadb4ce95ee9f412eb3c99`

VERIFIED facts are those above backed by strict validation, exact hashes, and
fresh task rows. The checkpoint-selection interpretation is an INFERENCE from
the frozen rule and sealed metrics. Generalization beyond exposed dev is
UNVERIFIED.
