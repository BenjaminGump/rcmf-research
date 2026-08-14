# Handoff: EXP-018 State-Conditioned Transition Representation Gate

## Status

- Experiment stopped correctly at the mandatory Part-D representation gate.
- Branch: `research/v4-decision-transition-memory`.
- Source commit: `0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c`.
- Result-record commit: the commit containing this handoff; report its exact
  SHA after committing.
- Run UUID: `state_conditioned_transition_6b_20260814_001`.
- Decision branch: `state_transition_representations_insufficient`.
- V4 remains a candidate; no V4 tag was created.

## What Ran

- Revalidated immutable EXP-017 data: 499 transitions, a 148-transition panel,
  32 queries, 4,640 legal rows, 4,579 scoreable rows, and 61 over-context
  masked rows.
- Built/reused frozen Qwen representations for 32 query states and 148 complete
  transitions.
- Built a deterministic 29/8 parent split and four two-axis cells.
- Trained global, state-only, transition-only, additive, signed-bilinear, and
  concat-MLP utility predictors using cell-A labels and grouped train-only CV.
- Evaluated state, transition, both-shuffle, and mean-representation controls.
- Implemented and validated future-compatible reversible V0/T field algebra.

## What Did Not Run

- No factorized program, static/conditional/state-only latent ablation, PairMLP
  latent upper bound, or Qwen behavioral scoring was trained in Parts E-G.
- The optional state-conditioned whole-trajectory control was not triggered.
- No selector, program head, injector, production full bank, Stage C2,
  end-to-end RCMF model, AppWorld generation/evaluation, or V4 tag was started.

These omissions are required by the preregistered gate, not incomplete work.

## Exact Split

| Cell | Pairs | States | Transitions | Parents | Utility mean |
|---|---:|---:|---:|---:|---:|
| A train/train | 2,667 | 24 | 115 | 29 | 0.067686 |
| B held-out state/train transition | 904 | 8 | 115 | 29 | 0.024758 |
| C train state/held-out transition | 752 | 24 | 32 | 8 | 0.084375 |
| D double held-out | 256 | 8 | 32 | 8 | 0.030112 |

The parent split manifest hash is
`de17e00ccf67080c822d689b6809f377eea3c2c77e07879c708dedced0eb790b`.
No B/C/D label was used for selection.

## Gate Result

On D, metrics are Spearman / sign agreement / Huber:

| Predictor | D metrics |
|---|---:|
| State only | 0.205547 / 0.601307 / 0.052382 |
| Transition only | 0.093928 / 0.745098 / 0.026967 |
| Additive | 0.140031 / 0.601307 / 0.058822 |
| Signed bilinear | 0.111083 / 0.575163 / 0.062106 |
| Concat MLP upper bound | 0.059482 / 0.758170 / 0.126287 |

The concat model failed Spearman >=0.20, failed to beat state-only and
transition-only, and barely changed under transition shuffle: Spearman
`0.059482 -> 0.043260`, Huber `0.126287 -> 0.126444`. Signed bilinear also
failed. The exact branch is `state_transition_representations_insufficient`.

Cell C was much easier (concat Spearman 0.633956; bilinear 0.700908), but the
interaction did not compose with held-out query tasks in D. This isolates a
double-held-out representation/generalization problem rather than a cache or
training-process failure.

## Representation Identity

- Query shape/hash: `[32,4096]`,
  `a5a301cca070111b7c449423724378d0fb51b42e732163fb2b6adedf48cd73c6`.
- Transition shape/hash: `[148,4096]`,
  `346224c535841a1cdc1de1454693f2d940716103956d96c35def28dac12d9c0c`.
- Transition tokens: min 7,413, mean 11,470.13, max 35,608.
- No truncation, future target action, validation-parent representation, or
  leakage was detected.

## Reversibility

Explicit-sum/compiled contraction, add/remove, replace, parent removal,
arbitrary order, exact restoration, and fixed runtime-shape checks all passed.
No q/k model or production transition field was trained.

## Run Provenance

- Attempt ledger: one start and one normal end for `attempt-001`.
- Start/end: `2026-08-14T13:25:18.064696Z` /
  `2026-08-14T13:29:16.355473Z`.
- Exit code 0; stop reason `normal_completion`; no resume checkpoint; no
  scientific parameter change.
- tmux session was `exp018`; the network/Codex disconnect did not interrupt it
  and did not create another attempt or run UUID.
- Wall time: 238.5433 seconds.
- Independent validator: passed with zero errors.
- Tests: local 169 passed/1 skipped; Lambda 170 passed.

## Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_6b_20260814_001`.
- Size: 2,318,267,125 bytes (about 2.159 GiB).
- Summary: `<root>/parts_a_d_summary.json`.
- Validation: `<root>/parts_a_d_postrun_validation.json`.
- Attempt ledger: `<root>/attempts.jsonl`.
- Heartbeat: `<root>/heartbeat.json`.
- Log:
  `/lambda/nfs/rcmf-persist/runs/logs/state_conditioned_transition_6b_20260814_001_parts_a_d_attempt_001.log`.

## Next Review

Do not launch behavioral `p(s,m)` training with the current representations.
The next reviewed milestone should keep the same two-axis manifest and test
representation/readout alternatives that must show transition-sensitive
double-held-out signal before Qwen behavioral training. Candidate questions
include richer frozen-Qwen token pooling and train-only cross-token
interaction, with the same state/transition shuffle controls.

At final audit there was no tmux or experiment process; H100 allocation and
utilization were 0 MiB / 0%. Lambda is safe to terminate after final Git sync.
