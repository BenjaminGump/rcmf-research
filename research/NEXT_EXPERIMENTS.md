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

- Improve candidate proposal before scaling the teacher labels. The first
  all-memory audit found that the proposed candidate union did not recall the
  highest-utility memory for any of 4 audited states.

Candidates:

- Add a cheaper first-pass target-aware scorer that still uses frozen Qwen
  representations and never uses target loss as a selector for non-audit
  training labels.
- Increase candidate count and compare marginal recall/cost.
- Add app/entity/tool-call overlap features extracted from raw MemoryRecord
  text.

Stop condition:

- Do not start full RCMF student training until ChatGPT and the user review
  teacher-label quality, context feasibility, compute cost, and injection
  position smoke results.

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
