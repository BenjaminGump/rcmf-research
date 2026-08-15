# Handoff: EXP-020 All-Task Query-Coverage Interaction Test

## Status

- Completed under run UUID
  `all_task_query_interaction_6d_20260815_001`.
- Branch: `research/v4-decision-transition-memory`.
- Source/audit commit: `886cf2134599e8243d96d3e9fd497c661ae3e3c3`.
- Result-record commit: the commit containing this handoff; report its exact
  SHA after committing.
- Primary branch: `query_task_coverage_still_data_limited`.
- Final diagnostic branch:
  `state_intent_available_but_memory_utility_target_not_generalizing`.
- Representation gate repaired: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created.

## What Ran

- Built a deterministic 92-state manifest covering all 37 train and 9 heldout
  tasks, with two early/later states per task.
- Preserved the immutable 148-transition panel and 29/8 parent split.
- Preflighted all 13,616 Cartesian pairs and scored every new legal scoreable
  pair without truncation.
- Reused all 4,640 compatible EXP-017 rows after strict hash validation.
- Built frozen-Qwen multiview state and pair cross-encoder representations.
- Reproduced transition-only, state-only, signed bilinear, multiview low-rank
  field, multiview PairMLP, structured-feature, and prompt cross-encoder
  models at nested LC12/LC24/LC37 coverage.
- Evaluated the fixed 9-task heldout set with state, transition, and both
  shuffle controls plus paired task-grouped bootstrap intervals.
- Ran the optional all-successful-example state action-intent diagnostic.

## What Did Not Run

- No behavioral state-transition program or program latent.
- No additive-token injector or Qwen behavioral backpropagation.
- No selector modification, production field, Stage C2, or end-to-end RCMF.
- No AppWorld generation/environment evaluation.
- No full-demo or three-demonstration change.
- No V4 tag.

These omissions are required by the failed representation gate, not unfinished
execution.

## Exact Data

- Queries: 92 total = 74 train states from 37 tasks plus 18 validation states
  from 9 tasks; no shortages.
- The original 32 queries are an exact subset.
- Learning curves: LC12 12 tasks/24 states, LC24 24/48, LC37 37/74.
- Transition panel/parents: 148/37 with the immutable 29/8 parent split.
- Cartesian/illegal/legal: 13,616/296/13,320.
- Scoreable/over-context: 13,128/192; nothing was truncated.
- Reused rows: 4,640 = 4,579 scoreable plus 61 over-context.
- Newly scored: 8,549; new over-context: 131.
- Teacher positive/neutral/negative: 6,332/2,102/4,694.
- Utility mean/std/min/max:
  `0.054410/0.285000/-1.652619/1.681496`.

Cells A/B/C/D contain `8,205/2,051/2,296/576` scoreable rows. Their
positive/neutral/negative counts are `4093/1089/3023`, `800/534/717`,
`1176/325/795`, and `263/154/159`.

## Learning-Curve Result

Double-heldout D NDCG@4 at LC12/LC24/LC37:

| Model | LC12 | LC24 | LC37 |
|---|---:|---:|---:|
| Transition-only | .422732 | .496763 | .480274 |
| Prompt cross-encoder | .360914 | .461709 | .523176 |
| Multiview low-rank field | .525407 | .496470 | .510827 |

The cross-encoder curve is materially increasing. More task coverage helped,
but LC37 still failed the preregistered representation gate.

## LC37 Results

Cross-encoder B/C/D NDCG@4 is `.448943/.657188/.523176`; per-state Spearman
is `.169636/.450930/.117526`; residual Spearman is
`.138427/.523799/.063559`.

Low-rank field B/C/D NDCG@4 is `.287604/.612958/.510827`; per-state Spearman
is `.145182/.446941/.134551`; residual Spearman is
`.379732/.568455/.439346`.

On D, cross-encoder correct/state-shuffle/transition-shuffle/both NDCG@4 is
`.523176/.505407/.460532/.443671`. Its transition-shuffle contrast CI is
`[-.109427,.220537]`, and only 5/9 tasks are positive.

On D, field correct/state-shuffle/transition-shuffle/both NDCG@4 is
`.510827/.335396/.396184/.399018`. Its transition-shuffle contrast CI is
`[-.062307,.260903]`, and only 4/9 tasks are positive. It retains 71.22% of
the cross-encoder gain over transition-only, but the cross-encoder itself
fails and the field misses per-state/task/significance conditions.

## Action Intent

The probe evaluated 638 successful examples, with 499 train and 139 heldout
states. Correct-state mean accuracy is `.875899`, versus shuffled `.420863`
and majority `.559353`. Target app/API/action-type/completion correct
accuracies are `.856115/.791367/.856115/1.000000`.

State decision intent therefore generalizes, while pairwise raw-transition
target-NLL utility does not generalize sufficiently.

## Runtime And Size

- Preflight H100 projection: 8.1816 best, 9.0907 expected, 12.2724
  conservative.
- Actual H100 hours: teacher 2.987934, representations 3.453412, models
  5.771751, intent .160524, total 12.373621.
- Artifact size: 33,257,018,470 bytes, or 30.973 GiB.
- The complete design ran unchanged. The 12-hour line was used only as a
  preflight review threshold, never as a compute cap.

## Recovery Provenance

The append-only ledger contains ten paired attempts: seven completed and three
failed. All use one run UUID and
`scientific_parameter_changed=false`.

- Attempt 002: argparse rejected resume provenance arguments before any model
  or artifact work. The launcher contract was added and tested.
- Attempt 005: strict data validation detected a missing canonical tokenizer.
  The repair explicitly loads it and forbids generic fallback. Teacher and
  existing prompt hashes did not change.
- Attempt 007: all 60 new state rows were durable before a shape-sensitive
  immutable-row comparison failed. Old `[3,4096]` and new `[12288]` values
  were exactly equal after flattening. Attempt 008 resumed from the last row.

No completed cache row was overwritten or duplicated. Laptop/network
disconnects caused no duplicate process, artifact, or run UUID.

## Validation

Independent post-run validation reports `passed=true` and `errors=[]`. It
checks pair counts, leakage, no truncation, row identities and hashes, cache
reuse, score finiteness/nullness, query and parent manifests, representation
hashes, model/learning-curve rows, action-intent artifacts, attempt pairing,
runtime, and size.

Tests: local `221 passed, 1 skipped`; focused Lambda `17 passed`.

## Decision And Next Step

Primary branch is `query_task_coverage_still_data_limited` because the LC37
cross-encoder fails but its fixed-evaluation learning curve remains materially
increasing. The optional action-intent result selects the more specific final
branch `state_intent_available_but_memory_utility_target_not_generalizing`.

Do not start behavioral `p(s,m_transition)`. The next reviewed milestone
should test an action-intent-conditioned or relative/pairwise memory-use label
against the absolute raw-text target-NLL utility. Consider additional source
tasks or task augmentation only under a preregistered teacher-target review;
do not assume more states from the same 37 tasks are sufficient.

## Artifacts

Root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/all_task_interaction_6d_20260815_001`.

Primary files:

- `expanded_query_manifest.json`, `learning_curve_manifest.json`.
- `preflight_summary.json`, `pair_preflight.jsonl`.
- `teacher_cache.jsonl`, `teacher_summary.json`, `teacher_report.md`.
- `two_axis_summary.json`, `representation_summary.json`.
- `model_summary.json`, `learning_curve_report.md`.
- `cross_encoder_report.md`, `field_compatible_report.md`.
- `shuffle_control_report.md`, `action_intent_summary.json`.
- `final_summary.json`, `postrun_validation.json`.
- `run_manifest.json`, `attempts.jsonl`, `heartbeat.json`.

At final audit there is no tmux session or experiment process. GPU
memory/utilization are `0 MiB / 0%`. Lambda is safe to terminate after final
Git synchronization.
