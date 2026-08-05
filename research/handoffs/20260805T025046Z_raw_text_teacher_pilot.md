# Codex Session Handoff

## Session Metadata

- Date: 2026-08-05.
- User request: Milestone 3 only, implement and run a Primary Raw-Text Memory
  Teacher Pilot; do not start full student training or full AppWorld
  evaluation.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user: `fba2a8d`.
- Source commit used for Lambda pilot:
  `e295a2bd449f38f87e4ad8d945e73aa55d0e5ef7`.
- Ending branch: `workflow/research-loop`.
- Final record commit: created after this handoff is written.

## 1. Requested Goal

Implement a versioned raw-text teacher-label pipeline that scores the
ground-truth next action under frozen Qwen3-8B with and without one legal raw
MemoryRecord, run a small deterministic pilot, run additive-token smoke checks,
record the result, and stop before full training/evaluation.

## 2. Initial State

VERIFIED:

- Bare Qwen full baseline: `53/168 = 31.55%`.
- Bare Qwen fixed first-10 baseline: `3/10 = 30%`.
- Previous semantic-retrieval first-10 RCMF result: `4/10`.
- Previous semantic-retrieval paired first-37 result: RCMF `7/37`, baseline
  `10/37`.
- Lambda verified virtual environment:
  `/home/ubuntu/venvs/rcmf-py311`.

## 3. Files Changed

- `scripts/run_raw_text_teacher_pilot.py`
- `scripts/smoke_additive_token_positions.py`
- `tests/test_raw_text_teacher_pilot.py`
- `research/CURRENT_STATE.md`
- `research/NEXT_EXPERIMENTS.md`
- `research/DECISIONS.md`
- `research/experiments.jsonl`
- `research/results/raw_text_teacher_pilot_20260805_001.md`
- `research/handoffs/20260805T025046Z_raw_text_teacher_pilot.md`

## 4. Implementation Summary

VERIFIED:

- Added `raw_text_memory_teacher_labels_v1`.
- Added teacher-only raw memory insertion with clear delimiters:
  `teacher_only_raw_memory_section_v1`.
- L0 is the frozen-Qwen mean target-token NLL under the unchanged full-demo
  prompt.
- Lj_text is the same target loss after one legal raw MemoryRecord is inserted.
- Utility is `L0 - Lj_text`.
- Labels include target hash, memory text hash, token counts, renderer version,
  checkpoint identity, candidate source, and commit SHA.
- The teacher never calls an external API, never generates actions, and never
  uses compiled RCMF memory.
- The target sequence appends EOS and only target tokens are trained/scored;
  prompt labels are masked with `-100`.
- Candidate proposal is the union of cosine top-2, up to 2 same-app memories,
  and 2 deterministic random low-similarity memories.
- A 4-state audit scans all legal memories.
- Additive-token smoke covers `first_k`, `last_prompt_k`, and `last_user_k`
  with K=4 and zero-memory equivalence.

## 5. Validation

Local:

```powershell
python -m py_compile scripts\run_raw_text_teacher_pilot.py scripts\smoke_additive_token_positions.py
python -m pytest -q tests\test_raw_text_teacher_pilot.py
python -m pytest -q
```

VERIFIED:

- Local full tests passed: `47 passed`.
- Lambda full tests at source commit `e295a2b` passed: `47 passed`.

Lambda:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python -m pytest -q
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/smoke_additive_token_positions.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --output-json runs/teacher/raw_text_pilot_20260805_001/additive_token_position_smoke.json \
  --output-md runs/teacher/raw_text_pilot_20260805_001/additive_token_position_smoke.md \
  --num-tokens 4
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_raw_text_teacher_pilot.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --output-dir runs/teacher/raw_text_pilot_20260805_001 \
  --pilot-size 24 \
  --audit-size 4 \
  --representation-batch-size 1
```

## 6. Results

VERIFIED:

- Artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001`.
- Selected states: 24.
- Proposed candidate pairs: 96.
- Unique pairs including audit rows: 260.
- Scored rows: 250.
- Over-context rows skipped after preflight: 10.
- Utility counts: positive 71, neutral 11, negative 168.
- Utility mean/std: `-0.008413` / `0.313950`.
- Utility min/max: `-1.060414` / `1.452909`.
- Utility vs raw memory length correlation: `0.075251`.
- Utility vs combined context length correlation: `0.081745`.
- All-memory audit recall: `0/4`.
- Runtime: `498.29` seconds on one Lambda H100.
- Projected full-dataset candidate scoring cost: about `1.77` GPU hours.
- Projected full-dataset all-legal scan cost: about `16.25` GPU hours.
- Additive-token smoke passed for all three positions with zero loss delta and
  zero embedding delta under zero memory.

## 7. Failed Attempts

- Lambda direct GitHub pull remains unavailable because the instance has no
  GitHub private key/deploy key. Sync used the established local git-bundle
  upload and remote fast-forward fallback.
- No pilot command failed after the source commit was synced.

## 8. Deviations and Workarounds

VERIFIED:

- Ten over-context state-memory pairs were skipped after reporting token counts
  and IDs. No truncation was performed.
- The small audit subset was intentionally limited to 4 states per the
  Milestone 3 scope.

There were no deviations from the instruction to avoid full student training,
full AppWorld evaluation, external APIs, generation-based scoring, or compiled
RCMF memory labels.

## 9. Research-Relevant Observations

VERIFIED:

- Positive raw-memory utility examples exist, with max observed utility
  `1.452909`.
- Negative raw-memory utility examples also exist, with min observed utility
  `-1.060414`.
- Current proposal candidates missed the highest-utility legal memory in all 4
  audited states.

INFERENCES:

- The target-loss-difference teacher signal appears meaningful, but candidate
  proposal quality is currently the bottleneck.
- Scaling the current candidate policy directly into student labels would risk
  selecting weak memories even though stronger legal memories exist.

## 10. Next Questions for ChatGPT and User

- Is 0/4 candidate recall acceptable for a first teacher pilot, or should the
  next milestone focus entirely on retrieval/candidate generation?
- Should the audit subset be expanded before any full teacher-cache generation?
- Should candidate count be increased, or should same-app/entity/tool-overlap
  features be added first?
- Which additive-token position should be preferred for later training:
  `first_k`, `last_prompt_k`, or `last_user_k`?

## 11. Artifact References

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/summary.json`.
- Teacher labels:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/teacher_labels.jsonl`.
- Token preflight:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/token_length_preflight.json`.
- Pilot states:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/pilot_states.json`.
- Additive-token smoke JSON:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/additive_token_position_smoke.json`.
- Additive-token smoke Markdown:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/additive_token_position_smoke.md`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/report.md`.
- Run log:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001/run.log`.

## 12. GitHub State

- Remote: `git@github.com:BenjaminGump/rcmf-research.git`.
- Branch: `workflow/research-loop`.
- Source commit `e295a2b` was pushed to GitHub and synced to Lambda before the
  pilot.
- This handoff is part of the final record commit to be pushed after
  documentation updates.
