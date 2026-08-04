# Next Experiments

The current priority is diagnosing why semantic retrieval improves first-10 but
does not yet match the full baseline.

## EXP-001 Trace-Level First-37 Diagnosis

Goal:

- Compare bare Qwen, semantic-retrieval final checkpoint, and memory-scale-zero
  behavior on first-37 tasks.

Measure:

- retained/gained/lost/both-failed;
- exact model input, model output, and AppWorld observation for representative
  retained, gained, and lost tasks;
- tool-error loops, no-code failures, premature complete_task, and wrong-app
  actions.

Stop condition:

- Produce a failure taxonomy and at least two falsifiable next hypotheses.

## EXP-002 Semantic-Retrieval Checkpoint Sweep

Goal:

- Determine whether the final checkpoint is actually best beyond first-10.

Candidates:

- `checkpoint_step100.pt`;
- `checkpoint_step200.pt`;
- `checkpoint_step300.pt`;
- final checkpoint.

Use the fixed first-10 set first. Only run broader slices for candidates that
match or exceed first-10 baseline.

## EXP-003 Memory Scale Sweep

Goal:

- Check whether semantic retrieval benefits are robust to memory perturbation
  magnitude.

Candidates:

- scale 0.0 control;
- scale 0.25;
- scale 0.5;
- scale 1.0.

Report success-set deltas, not only aggregate score.

## EXP-004 Retrieval Collapse Diagnostics

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
