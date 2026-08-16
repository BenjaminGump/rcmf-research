# EXP-021 Relative and Action-Intent-Conditioned Memory-Use Target Audit

## Status

- Milestone: 6E / EXP-021.
- Run UUID: `memory_use_target_6e_20260816_001`.
- Branch: `research/v4-decision-transition-memory`.
- Source/audit commit: `3995b3cfdffdfc700846d2dec928cf5f7574e6fd`.
- Final record commit: the commit containing this report.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/memory_use_target_6e_20260816_001`.
- Selected target: T4 gap-weighted within-state pairwise preference.
- Decision branch: `raw_nll_memory_use_target_not_deployably_predictable`.
- Revised target validated: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created or moved.

The first generated EXP-021 report used a newly estimated transition-only mean
instead of the immutable EXP-020 transition-only comparator, read the wrong
transition-shuffle bootstrap key, and compared per-task results with one global
baseline. That report and summary are preserved with a
`_pre_locked_transition_repair` suffix. The record-only repair changed no
training, checkpoint, prediction, cache row, or scientific parameter. It
supersedes the earlier branch
`revised_target_learnable_but_field_factorization_insufficient`.

## Scope Compliance

Qwen3-8B remained frozen. EXP-017 through EXP-020 artifacts, the 92 queries,
148-transition panel, 29/8 parent split, full-demo prompt, three demonstrations,
leakage rules, and locked raw utility remained unchanged. No behavioral
program, injector, selector, production field, Stage C2, end-to-end RCMF,
AppWorld generation/evaluation, new query state, or new transition was run.

## Immutable Data Contract

- Queries: 92 = 74 train states from 37 tasks plus 18 held-out states from 9
  tasks.
- Transition panel: 148 transitions from 37 parents.
- Legal/scoreable/over-context rows: `13,320/13,128/192`.
- Scoreable A/B/C/D cells: `8,205/2,051/2,296/576`.
- The raw comparator remains `u_raw(s,m) = L0(s) - L_transition(s,m)`.
- B/C/D labels did not affect target, epoch, loss, or model selection.

## Raw Utility Decomposition

Cell A has mean utility `.064838` and variance `.094417`. State main effects
explain `.414517` of variance, transition main effects `.018679`, and the
additive model `.434646`; residual interaction variance is `.053379`. Raw and
residual effective ranks are `35.879/41.605`.

| Cell | Mean | Std | Negative | Neutral | Positive | Raw rank | Residual rank |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | .064838 | .307274 | 3,023 | 1,089 | 4,093 | 35.879 | 41.605 |
| B | .003757 | .180128 | 717 | 534 | 800 | 13.335 | 13.338 |
| C | .069050 | .298566 | 795 | 325 | 1,176 | 17.461 | 20.616 |
| D | .027876 | .152277 | 159 | 154 | 263 | 9.807 | 10.105 |

The mean within-state utility scales for A/B/C/D are
`.190998/.135882/.168014/.090508`. Full quantiles and transition-popularity
statistics are in the decomposition report.

## Serialization Robustness

The fixed audit has 192 pairs: 96 from A and 96 from D. Template 0 reused the
locked rows; canonical JSON and compact-tagged forms required 381 Qwen forwards
and produced three masked over-context alternatives. No row was truncated.

- Median pairwise-template Spearman: `.833893`.
- Positive/negative sign agreement: `.892081`.
- Mean per-state top-4 overlap: `.966667`.
- Length-change/utility-change Pearson: `.070247`.
- Gate result: passed.

The initial scoring process took `586.428 s`; a `15.895 s` resume reused all
384 durable alternative rows after a post-scoring aggregation-key failure.
Total serialization wall time was `602.323 s` (`.167312 H100 h`).

## Action Intent

Deterministic signatures cover app, API, coarse action type, completion, and
AST/function calls. Ground-truth query signatures were labels and oracle-only
diagnostics; they never entered deployable validation input. Predicted query
intent came from the train-only EXP-020 probe.

| Head | Held-out accuracy | Shuffled | ECE | Brier | NLL |
|---|---:|---:|---:|---:|---:|
| App | .944444 | .277778 | .076501 | .119439 | .476723 |
| API | .833333 | .222222 | .175951 | .327823 | 1.096768 |
| Action type | .888889 | .500000 | .120496 | .224034 | 1.005315 |
| Completion | 1.000000 | 1.000000 | .000360 | .000004 | .000361 |

Completion is noninformative here because every held-out completion label is
false. Mean informative correct/shuffled behavior is summarized as
`.916667/.500000` by the EXP-021 cache.

## Targets And A-Only Selection

T0 is locked absolute utility. T1/T2 are centered/scaled diagnostics. T3 is
within-state percentile rank. T4 is gap-weighted pairwise preference at a
fixed `.05` gap. T5 is query-intent/transition-signature compatibility. T6 is
predicted intent plus relative pairwise residual. T7 restricts pairwise
comparisons to intent-matched transitions.

T4 has `602,989` eligible comparisons over 87 states at the primary `.05`
gap; T7 has `110,888` matched comparisons over 86 states. Five-fold grouped CV
on A selected T4 field epoch 120 before B/C/D were evaluated.

| Candidate | Epoch | A-CV NDCG@4 | Gap accuracy | State-shuffle gap | Transition-shuffle gap |
|---|---:|---:|---:|---:|---:|
| T3 field | 120 | .391347 | .532515 | .022236 | .016257 |
| T4 field | 120 | .423183 | .558759 | .025350 | .009706 |
| T6 field | 30 | .384355 | .525447 | .000325 | .013668 |
| T7 field | 30 | .410596 | .516745 | .017358 | .065980 |

## Held-Out Results

Primary T4 field results:

| Cell | NDCG@1 | NDCG@4 | NDCG@8 | State Spearman | Residual Spearman | Gap accuracy | Raw Huber |
|---|---:|---:|---:|---:|---:|---:|---:|
| B | .351021 | .346490 | .327572 | .174307 | .142536 | .618807 | .309123 |
| C | .547127 | .584393 | .626013 | .411033 | .332893 | .746273 | .354548 |
| D | .379613 | .433983 | .495191 | .117200 | -.051060 | .583228 | .306444 |

On D, locked transition-only NDCG@4 is `.480274`, so T4 field loses `.046291`.
Its state/transition/both-shuffle drops are `.072329/.094174/.062550`.
Only the transition drop exceeds `.08`, and its task-bootstrap CI includes
zero. Four of nine held-out tasks beat their own locked transition-only
baseline.

T4 cross-encoder D NDCG@4 is `.384751`, state/residual Spearman is
`-.033663/.203538`, and state/transition shuffle gaps are
`-.032037/.028069`. It also fails the upper-bound gate.

The strongest isolated metric from another target does not rescue the formal
gate: T7 field D NDCG@4 is `.501103`, but residual Spearman is `.010895` and
transition-shuffle drop only `.034805`; T6 field has residual Spearman
`.281752` but misses the other matching requirements.

## Intent Contribution

On D, oracle-intent and predicted-intent NDCG@4 are `.337362/.344384`, both
below locked transition-only `.480274`. Their gains are
`-.142912/-.135889`, so the required predicted/oracle positive-gain retention
is undefined rather than a pass. T4 field adds `.089599` NDCG@4 over predicted
intent, but still underperforms transition-only. Coarse action intent alone
does not explain a deployable memory-use signal under the formal comparator.

## Gate And Decision

The field-compatible T4 model fails nine of ten formal checks. It passes only
the transition-shuffle point gap and measurable content-over-intent gain. It
misses transition-only gain, state/residual Spearman, gap accuracy, state
shuffle, bootstrap significance, 6/9 positive tasks, and intent retention.

Final branch:
`raw_nll_memory_use_target_not_deployably_predictable`.

VERIFIED: the raw-NLL teacher is deterministic and serialization-robust under
the audited templates, state action intent is learnable, and relative targets
can fit seen axes. VERIFIED: none of T3/T4/T5/T6/T7 yields a field-compatible
double-held-out deployment target satisfying the registered gate.

INFERENCE: raw target-NLL utility is a useful measurement but not a suitable
cross-task deployment label under the currently available prompt-only state
and transition information.

UNVERIFIED: structural procedural labels, direct next-action/API compatibility,
environment outcomes, or genuinely new source tasks may provide a better
teacher. They require a separately reviewed milestone.

## Runtime, Recovery, And Validation

- Successful serialization plus model work: `2.147676 H100 h`.
- Including the preserved interrupted scalar pair-loss attempt: approximately
  `2.504051 H100 h`.
- Final artifact size before record commit: `7,054,264,484` bytes
  (`6.5698 GiB`).
- Append-only attempt ledger: 17 rows, comprising one bootstrap failure and
  eight start/end attempt pairs. Every attempt records
  `scientific_parameter_changed=false`.
- Independent validation: `20/20` checks passed, `errors=[]`.

Implementation failures were preserved and resumed from durable state: an
EXP-019 path alias, A/B/C/D cell alias, combined-token field alias, slow scalar
pair-loss implementation, attempt-event validation, and locked-comparator
record repair. None changed the target, data, architecture, optimizer result,
or prediction rows.

## Artifacts

Primary Lambda files include `run_manifest.json`, `attempts.jsonl`,
`locked_raw_utility_decomposition.json`, `action_signatures.jsonl`,
`predicted_query_intent.json`, `intent_probe_calibration.json`,
`serialization_audit_manifest.json`, `serialization_teacher_cache.jsonl`,
`serialization_robustness.json`, `candidate_target_rows.jsonl`,
`a_only_grouped_cv_selection.json`, `model_audit_summary.json`,
`scientific_gate_repair.json`, and `postrun_validation.json`.

At the pre-record audit no experiment process was active. H100 utilization and
memory were `0% / 0 MiB`. Idle completed tmux sessions were awaiting final
record synchronization.

