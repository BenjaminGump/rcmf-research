# Next Experiments

The current priority is correctness and observability before any expensive full
GPU run. The next iteration should first prove that representation rendering,
full legal memory-bank construction, record-level memory writes, and
additive-token injection behave as intended.

## EXP-001 Correctness Smoke Before Full Training

Goal:

- Run the next-iteration RCMF pipeline on a tiny smoke configuration after
  syncing to Lambda.
- Verify that state representations use the same full-demo message renderer and
  Qwen chat template as evaluation.
- Verify that each MemoryRecord compiles into one write after any token-weighted
  chunk aggregation.
- Verify additive-token injection audits selected prompt tokens and never
  injects target tokens during training.

Measure:

- local and Lambda tests;
- `memory_record_chunk_audit.json`;
- `memory_injection_diagnostics_v2` JSON/Markdown;
- a one-step or few-step smoke train run without automatic truncation or
  downsampling.

Stop condition:

- Do not start a full-size training run until smoke and diagnostics complete
  without correctness failures.

## EXP-002 Primary Text-Memory Teacher Pilot

Goal:

- Completed on 2026-08-05 as
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001`.
- The pilot built a raw-text teacher that uses frozen Qwen3-8B scoring over raw
  memory text, not compiled leave-one-out RCMF memory.

Measure:

- 24 selected states, 250 scored rows, 10 over-context rows skipped after
  preflight, no truncation.
- Positive/neutral/negative utility counts: 71/11/168.
- Candidate recall on the 4-state all-memory audit subset: 0/4.
- Projected full-dataset cost: about 1.77 GPU hours for candidate scoring and
  16.25 GPU hours for all-legal-memory scoring at the measured pilot rate.

Stop condition:

- Met. Stop before full student training. The next action is review, not a
  training launch.

## EXP-006 Teacher Candidate Recall Fix

Goal:

- Status after Milestone 3B: the candidate proposal remains weak, but the
  audit recommendation is not to repair retrieval before every other step. The
  expanded audit scanned all legal memories for all 24 pilot states and found
  exact proposal recall@1/2/4/8 of `1/24`, mean regret `0.275401`, and positive
  mass coverage `0.107657`.

Candidates:

- Add a cheaper first-pass target-aware scorer that still uses frozen Qwen
  representations and never uses target loss as a selector for non-audit
  training labels.
- Increase candidate count and compare marginal recall/cost.
- Add app/entity/tool-call overlap features extracted from raw MemoryRecord
  text.

Stop condition:

- Treat this as an alternative if the user and ChatGPT reject all-legal cache
  generation due to cost or label quality. Do not start full RCMF student
  training until after review.

## EXP-007 Complete All-Legal Teacher Cache

Goal:

- Completed on 2026-08-05 as
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- The cache scored every scoreable legal state-memory pair for all 638
  decision states, reusing compatible pilot/audit3B rows and masking
  over-context pairs without truncation.

Current evidence:

- Cache version: `raw_text_memory_teacher_full_cache_v1`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- Exact final counts: 638 states, 46 memory records, 28,710 legal pairs,
  27,054 scoreable pairs, and 1,656 over-context masked pairs.
- Reused compatible cached pairs: 1,080. Newly scored pairs: 26,002.
- Validation passed and reproducibility repeats for positive, neutral, and
  negative pairs had all L0/Lj/utility diffs equal to `0.0`.
- Representative inspection selected 30 rows and found 0 obvious issues.
- Runtime: `10.26` actual H100 hours.
- Utility counts positive/neutral/negative: 13,426 / 4,861 / 8,767.
- The 24-state audit was not fully representative of the complete cache:
  full positive/neutral/negative proportions were
  `0.496267/0.179678/0.324056` versus audit3B
  `0.346008/0.115970/0.538023`.

Stop condition:

- Met. Stop before student training or full AppWorld evaluation. The next
  action is review of teacher-label quality, missingness, overlap diagnostics,
  and student-label construction policy.

## EXP-008 Review-Gated Student Label Compiler

Goal:

- After user and ChatGPT review, transform the complete raw-text teacher cache
  into a student-training label dataset.
- Use the task-grouped future split manifest from Milestone 3C.
- Decide explicitly how to handle over-context rows, states with no valid
  positive or negative memories, and the fully masked long memory
  `076f5673-6565-5f20-aada-6f16a0f8d4b0`.
- Preserve the full-bank/leakage semantics and do not truncate prompts,
  targets, or raw memories silently.

Measure:

- label counts by split, task, state, and memory;
- target utility distribution after any thresholding or weighting;
- coverage of positive and negative candidates per state;
- checks that no task crosses train/validation split.

Stop condition:

- Do not start RCMF student training until the label compiler output and policy
  are reviewed.

## EXP-003 Trace-Level First-37 Diagnosis

Goal:

- Compare bare Qwen, semantic-retrieval final checkpoint, and memory-scale-zero
  behavior on the retained/gained/lost first-37 task groups.

Measure:

- exact model input, model output, and AppWorld observation for representative
  retained, gained, and lost tasks;
- tool-error loops, no-code failures, premature complete_task, and wrong-app
  actions.

Stop condition:

- Produce a failure taxonomy and at least two falsifiable next hypotheses.

## EXP-004 Memory Scale Sweep

Goal:

- Check whether semantic retrieval benefits are robust to memory perturbation
  magnitude.

Candidates:

- scale 0.0 control;
- scale 0.25;
- scale 0.5;
- scale 1.0.

Report success-set deltas, not only aggregate score.

## EXP-005 Retrieval Collapse Diagnostics

Goal:

- Decide whether architecture work should target state addressing, memory
  compiler geometry, injector gating, or loss weighting.

Measure:

- memory_z mean/std/min/max;
- state address norms;
- state-to-support distribution entropy;
- teacher/student retrieval KL;
- per-task relation between retrieval concentration and success/failure.

## Standing Rule

Before training on any new prepared AppWorld data or subset, run the context
length preflight. If over-limit examples exist, stop and ask the user before
filtering.
