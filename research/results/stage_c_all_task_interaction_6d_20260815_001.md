# EXP-020 All-Task Query-Coverage Interaction Test

## Status

- Milestone: 6D / EXP-020.
- Run UUID: `all_task_query_interaction_6d_20260815_001`.
- Branch: `research/v4-decision-transition-memory`.
- Source/audit commit: `886cf2134599e8243d96d3e9fd497c661ae3e3c3`.
- Final record commit: the commit containing this report.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/all_task_interaction_6d_20260815_001`.
- Primary interaction branch: `query_task_coverage_still_data_limited`.
- Final diagnostic branch:
  `state_intent_available_but_memory_utility_target_not_generalizing`.
- Representation gate passed: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created or moved.

## Scope Compliance

Qwen3-8B remained frozen. The EXP-017 transition panel, 29/8 parent split,
raw-transition teacher definition, full-demo renderer, three demonstrations,
leakage exclusions, and no-truncation policy were preserved. The run did not
train a behavioral program, additive-token injector, signed selector,
production field, Stage C2 model, or end-to-end RCMF model. It did not run
AppWorld generation or environment evaluation.

## Query Manifest

- Train: 74 states from all 37 train tasks, exactly two per task.
- Validation: 18 states from all 9 validation tasks, exactly two per task.
- Total: 92 states; no task shortage or replacement was required.
- Original EXP-017/018/019 states: all 32 are an exact subset.
- Earlier/later selections: 46/46.
- Prompt-length buckets: short 28, medium 44, long 20.
- Target action types: API documentation 35, API read/login 49, and
  Python/reasoning 8.
- Multi-label app counts: file_system 20, phone 14, simple_note 12, spotify
  56, and venmo 8.
- Nested learning-curve subsets: LC12 = 12 tasks/24 states, LC24 = 24/48,
  and LC37 = 37/74. The same 9-task/18-state validation set is used at every
  point.

Selection used only task identity and deterministic early/later decision
position. It did not inspect transition utility, validation labels, or model
predictions.

## Pair And Teacher Cache

| Quantity | Count |
|---|---:|
| Query-transition Cartesian pairs | 13,616 |
| Illegal leakage pairs | 296 |
| Exact legal pairs | 13,320 |
| Scoreable pairs | 13,128 |
| Over-context pairs | 192 |
| Reused EXP-017 rows | 4,640 |
| Reused scoreable / over-context | 4,579 / 61 |
| Newly scored rows | 8,549 |
| New over-context rows | 131 |

Every same-task, same-episode, same-replay, and same-lineage pair is excluded.
All 192 over-context rows have null utility and `valid_for_loss=false`; no
prompt, target, or transition was truncated. Cache validation found zero
duplicate keys, leakage violations, identity mismatches, or nonfinite scored
rows. Deterministic positive, neutral, and negative rescoring reproduced both
loss and utility exactly.

The scoreable utility distribution contains 6,332 positive, 2,102 neutral,
and 4,694 negative rows. Mean/std are `0.054410/0.285000`; min/max are
`-1.652619/1.681496`. Percentiles p01/p05/p25/p50/p75/p95/p99 are
`-0.624501/-0.346477/-0.056247/0.004391/0.138245/0.555356/1.161710`.

Teacher-cache SHA256:
`f7a08f716b1c14dd377ebcd9310404e91ee66cef789edae624861e322ac5bb26`.

## Two-Axis Cells

| Cell | Rows | States/tasks | Transitions/parents | Utility mean/std | Pos/neutral/neg | Majority sign |
|---|---:|---:|---:|---:|---:|---:|
| A train-state/train-transition | 8,205 | 74/37 | 115/29 | .064838/.307274 | 4093/1089/3023 | .575183 |
| B heldout-state/train-transition | 2,051 | 18/9 | 115/29 | .003757/.180128 | 800/534/717 | .527357 |
| C train-state/heldout-transition | 2,296 | 74/37 | 32/8 | .069050/.298566 | 1176/325/795 | .596651 |
| D heldout-state/heldout-transition | 576 | 18/9 | 32/8 | .027876/.152277 | 263/154/159 | .623223 |

B/C/D labels did not affect model, view, epoch, or hyperparameter selection.

## Preflight, Runtime, And Size

Before GPU scoring, the complete pair set projected to 8.1816 best-case,
9.0907 expected, and 12.2724 conservative H100 hours. Expected and
conservative artifact sizes were 72.8404 and 98.3346 GiB. Because the expected
projection was below the 12-hour review threshold, the full prespecified run
started unchanged; the transition panel, query set, legal pairs, and updates
were not reduced.

Actual H100-active durations were:

| Phase | H100 hours |
|---|---:|
| Expanded teacher scoring | 2.987934 |
| Frozen representations | 3.453412 |
| Models and learning curves | 5.771751 |
| Action-intent probe | .160524 |
| Total | 12.373621 |

Final artifact directory size, including the post-run validation outputs, is
33,258,921,348 bytes, or 30.9748 GiB. The validator's pre-report accounting was
33,257,018,470 bytes. The actual total slightly exceeded 12 hours, but 12
hours was a review threshold rather than a compute cap; the expected preflight
projection was 9.09 hours.

## Frozen Representations

- State multiview tensor: `[92,10,4096]`; 32 immutable rows reused and 60 new
  rows computed atomically.
- Transition multiview tensor: `[148,10,4096]`, reused unchanged.
- Cross-encoder tensor: `[13128,12288]`; 4,579 immutable rows reused and
  8,549 new rows computed.
- Prompt tokens min/mean/max: `15,074/22,465.567/40,845`.
- No target action, future observation, raw transition in the student path,
  or truncated input entered a query representation.
- Cross-encoder aggregate tensor SHA256:
  `d0b2c4cc83c3e3da6a0357be186a3dc296a59413b82efb772bb1d4d1203e7d38`.

The original 4,579 aggregate rows were validated by identical flattened
values. The old cache stores each row as `[3,4096]`, while the new aggregate
stores `[12288]`; this representational shape difference is normalized only
for validation and does not change values.

## Learning Curves

Double-heldout D NDCG@4 on the fixed 9-task validation set:

| Model | LC12 | LC24 | LC37 | Curve |
|---|---:|---:|---:|---|
| Transition-only | .422732 | .496763 | .480274 | unstable/flat |
| Prompt cross-encoder | .360914 | .461709 | .523176 | materially increasing |
| Multiview low-rank field | .525407 | .496470 | .510827 | unstable/flat |

The prompt cross-encoder rose by `0.162262` from LC12 to LC37 and therefore
did not satisfy the preregistered flat/degrading branch. This is evidence that
query-task coverage mattered, but it is not a gate pass.

## LC37 Double-Heldout Metrics

| Model | NDCG@4 | Per-state Spearman | Residual Spearman | Raw Huber |
|---|---:|---:|---:|---:|
| State-only | .301960 | - | - | - |
| Transition-only | .480274 | .054476 | - | .066209 |
| Decomposed signed bilinear | .495892 | .053621 | .162967 | .091428 |
| Multiview low-rank field | .510827 | .134551 | .439346 | .085152 |
| Multiview PairMLP | .515093 | .038510 | .124362 | .081415 |
| Structured features | .504148 | .054721 | .037775 | .080856 |
| Prompt cross-encoder | .523176 | .117526 | .063559 | .098524 |

Additional D metrics:

| Model | Recall@4 | Utility mass@4 | Pairwise accuracy | Sign agreement |
|---|---:|---:|---:|---:|
| Low-rank field | .230769 | .179083 | .613254 | .601896 |
| Prompt cross-encoder | .307692 | .159936 | .551439 | .533175 |

## B/C/D Generalization

| Model/cell | NDCG@4 | Per-state Spearman | Residual Spearman | Raw Huber |
|---|---:|---:|---:|---:|
| Cross B | .448943 | .169636 | .138427 | .114676 |
| Cross C | .657188 | .450930 | .523799 | .084425 |
| Cross D | .523176 | .117526 | .063559 | .098524 |
| Field B | .287604 | .145182 | .379732 | .111292 |
| Field C | .612958 | .446941 | .568455 | .085885 |
| Field D | .510827 | .134551 | .439346 | .085152 |

Both candidates remain much stronger on seen-query cell C than on unseen-query
cells B/D.

## Shuffle Controls And Gates

Prompt cross-encoder LC37 D NDCG@4:

- Correct: `0.523176`.
- Shuffled state: `0.505407`, drop `0.017769`.
- Shuffled transition: `0.460532`, drop `0.062644`.
- Both shuffled: `0.443671`, drop `0.079505`.
- Transition-shuffle task-bootstrap contrast: mean `0.055472`, 95% CI
  `[-0.109427, 0.220537]`.
- Positive relative behavior: 5/9 heldout tasks.

The cross-encoder misses the required per-state and residual Spearman of 0.20,
both 0.08 shuffle drops, significant transition-shuffle contrast, and 6/9
positive tasks. Its NDCG@4 gain over transition-only is `0.042903`, also below
the required 0.05.

Multiview low-rank field LC37 D NDCG@4:

- Correct: `0.510827`.
- Shuffled state: `0.335396`, drop `0.175431`.
- Shuffled transition: `0.396184`, drop `0.114643`.
- Both shuffled: `0.399018`.
- Transition-shuffle task-bootstrap contrast: mean `0.104488`, 95% CI
  `[-0.062307, 0.260903]`.
- State-shuffle CI: `[0.099247, 0.301115]`.
- Positive relative behavior: 4/9 heldout tasks.

The field retains `0.712157` of the cross-encoder gain over transition-only,
but the upper bound itself failed. The field also misses per-state Spearman,
the 6/9 task condition, and a significant transition-shuffle contrast.

## Action-Intent Diagnostic

The optional probe used all 638 successful decision examples: 499 train and
139 heldout validation states. It reused 92 representation rows and computed
546 new rows. Correct-state mean accuracy was `0.875899`, versus shuffled
`0.420863` and majority `0.559353`; the correct-minus-shuffled gap was
`0.455036`.

| Head | Correct | Shuffled | Majority | Coverage |
|---|---:|---:|---:|---:|
| Target app | .856115 | .280576 | .467626 | 1.000000 |
| Target API | .791367 | .172662 | .366906 | .985612 |
| Action type | .856115 | .345324 | .467626 | 1.000000 |
| Completion | 1.000000 | .884892 | .935252 | 1.000000 |

The state views therefore contain substantial heldout-task decision intent.
The failure is more specific to learning the raw-transition target-NLL utility
interaction, rather than an absence of state decision information.

## Decision

The prompt cross-encoder failed its LC37 scientific gate while its LC12/24/37
curve remained materially increasing. The primary registered branch is
`query_task_coverage_still_data_limited`. Because all 37 source train tasks are
already represented and the action-intent probe succeeds while all pair models
fail, the final diagnostic branch is
`state_intent_available_but_memory_utility_target_not_generalizing`.

The representation gate is not repaired. Behavioral `p(s,m_transition)` must
remain blocked. The next reviewed milestone should test whether an
action-intent-conditioned or relative/pairwise memory-use target generalizes
better than absolute raw-text target-NLL utility. Additional source tasks or
task augmentation can be considered, but simply scoring more states from the
same 37 tasks is not justified as the only repair.

## Recovery Provenance

The append-only ledger contains ten paired attempts: seven completed and three
failed. Every row uses the same run UUID and records
`scientific_parameter_changed=false`.

- Attempt 002 failed at argparse validation before tokenizer/model/GPU work.
- Attempt 005 failed because a data-validation path had no canonical
  tokenizer and the renderer fell back to a generic tokenizer. Existing
  teacher and prompt hashes were unchanged. The repair loads the canonical
  tokenizer explicitly and forbids fallback for strict validation.
- Attempt 007 computed all 60 new state rows, then failed because it compared
  old `[3,4096]` and new `[12288]` rows with shape-sensitive equality. The
  values were exactly equal after flattening. The repair performs strict
  flattened-value comparison and attempt 008 resumed from the last atomic row.

Atomic rows, parent attempt IDs, checkpoint hashes, optimizer/update state
where applicable, and heartbeat files prevented duplicate work. Laptop and
network disconnects did not create a duplicate run or run UUID.

## Validation And Artifacts

Independent post-run validation passed with `errors=[]`. It verified exact
query, transition, parent, pair, cache, representation, learning-curve,
checkpoint, attempt, and runtime accounting. Local tests passed `221 passed,
1 skipped`; focused Lambda tests passed `17 passed`.

Key artifacts under the root:

- `expanded_query_manifest.json` and `learning_curve_manifest.json`.
- `preflight_summary.json`, `preflight_report.md`, and `pair_preflight.jsonl`.
- `teacher_cache.jsonl`, `teacher_summary.json`, and `teacher_report.md`.
- `data_preparation_summary.json` and `two_axis_summary.json`.
- `representation_summary.json` and `representation_report.md`.
- `model_summary.json`, `model_report.md`, and `learning_curve_report.md`.
- `cross_encoder_report.md`, `field_compatible_report.md`, and
  `shuffle_control_report.md`.
- `action_intent_summary.json` and `action_intent_report.md`.
- `final_summary.json`, `postrun_validation.json`, and
  `postrun_validation.md`.
- `run_manifest.json`, `attempts.jsonl`, and `heartbeat.json`.

At final audit no tmux session or experiment Python process was active. GPU
memory/utilization were `0 MiB / 0%`. The Lambda instance is safe to terminate
after final Git synchronization.
