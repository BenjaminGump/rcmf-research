# EXP-019 Interaction-Residual and Multi-View Representation Repair

Status: **completed; representation gate not repaired**

Run UUID: `state_transition_representation_6c_20260814_001`

Branch: `research/v4-decision-transition-memory`

Final source/audit commit before records:
`5ca600bf76fcdb9db5b0278c60a31dc35b6a7128`

Last experiment-runner fix commit:
`cbb75d474e01ee19e35a35d76814b8c63f1efdc7`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/interaction_representation_6c_20260814_001`

Decision branch: `query_task_coverage_insufficient`

## Scope And Result

EXP-019 diagnosed the EXP-018 representation gate using the immutable
two-axis split and transition-teacher labels. It tested, in order:

1. residual/listwise objectives on the original single-vector representations;
2. span-aware multi-view frozen-Qwen representations;
3. a deterministic structured-feature baseline;
4. a prompt-only frozen-Qwen cross-encoder information upper bound; and
5. cell-A-only query-task learning curves.

No candidate passed the preregistered double-held-out interaction gate. The
prompt-only cross-encoder also failed on D, but its 12-query-task learning
curve remained slightly rising and unstable. The registered decision tree
therefore selects `query_task_coverage_insufficient`, not the stronger claim
that teacher utility is intrinsically unpredictable from prompt-only text.

The behavioral `p(s,m_transition)` experiment remains blocked. No behavioral
program, additive-token injector, selector, production transition field,
Qwen behavioral backpropagation, Stage C2, end-to-end RCMF model, AppWorld
generation/evaluation, full-demo modification, or V4 tag was created.

## Immutable Input And Exact Cells

EXP-018 source commit
`0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c` and final record commit
`82cce2f99470a074591372bb2b9aaed8af0cf688` were validated read-only. All
11 registered immutable files retained their hashes.

| Quantity | Count |
|---|---:|
| Extracted EXP-017 transitions | 499 |
| Deterministic transition panel | 148 |
| Query states | 32 |
| Legal teacher rows | 4,640 |
| Scoreable rows | 4,579 |
| Over-context rows, masked | 61 |

The existing 24/8 query-state split and 29/8 transition-parent split yield:

| Cell | State / transition status | Rows | Utility mean/std | Positive / neutral / negative | Always-positive sign accuracy |
|---|---|---:|---:|---:|---:|
| A | train / train | 2,667 | 0.067686 / 0.331588 | 1,343 / 370 / 954 | 0.584676 |
| B | held-out / train | 904 | 0.024758 / 0.096050 | 403 / 349 / 152 | 0.726126 |
| C | train / held-out | 752 | 0.084375 / 0.324114 | 408 / 119 / 225 | 0.644550 |
| D | held-out / held-out | 256 | 0.030112 / 0.074908 | 117 / 103 / 36 | 0.764706 |

For D, the sign baseline is exactly `117 / (117 + 36) = 0.764706` after
excluding neutral rows. Sign accuracy near this value is not evidence of
interaction learning.

There was no leakage, duplicate pair key, truncation, L0 inconsistency, or
target-token utility mismatch. No B/C/D label was used for model or
hyperparameter selection.

## Utility Main-Effect Decomposition

Cell A was decomposed using only its labels:

`u(s,m) = mu + a(s) + b(m) + r(s,m)`

| Quantity | Value |
|---|---:|
| Global mean `mu` | 0.0676861715 |
| Total utility variance | 0.1099505467 |
| State component variance | 0.0454531963 |
| Transition component variance | 0.0040498008 |
| Residual interaction variance | 0.0605746571 |
| State-only variance explained | 0.412241 |
| Transition-only variance explained | 0.035677 |
| Additive-main variance explained | 0.449074 |

The 24-by-115 utility matrix has centered effective rank `14.4530` and stable
rank `1.6091`. The residual matrix has effective rank `16.0971` and stable
rank `2.1898`; the interaction target is therefore not a rank-one artifact.

Raw / cell-A-normalized residual mean and standard deviation were:

| Cell | Raw mean/std | Residual mean/std |
|---|---:|---:|
| A | 0.067686 / 0.331588 | 0.000000 / 0.246119 |
| B | 0.024758 / 0.096050 | -0.041466 / 0.107537 |
| C | 0.084375 / 0.324114 | 0.013298 / 0.245043 |
| D | 0.030112 / 0.074908 | -0.037574 / 0.074908 |

Unknown state or transition levels in held-out cells use the centered zero
prior. Their labels are never used to estimate main effects.

## Revised Matching Metrics

The main evaluation is within-state transition matching. The table below
shows correct-pair D results. `PS` is mean per-state Spearman, `RS` is
interaction-residual Spearman, and `Mass` is positive utility mass coverage.

| Candidate | Raw Spearman | PS | RS | NDCG@1/4/8 | Best recall@1/4/8 | Mass@4/8 | Pairwise acc. | Raw Huber |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Part-B signed bilinear | 0.166696 | 0.050357 | 0.129785 | .3888/.4849/.5059 | .0/.2/.4 | .1692/.2940 | 0.465254 | 0.073926 |
| Multi-view low-rank tensor | 0.024760 | 0.121518 | 0.188452 | .4920/.5541/.5543 | .0/.2/.4 | .1927/.3222 | 0.647311 | 0.081963 |
| Multi-view pair MLP | 0.024270 | 0.015900 | 0.333560 | .3433/.3925/.4698 | .0/.0/.6 | .1212/.3046 | 0.593551 | 0.057701 |
| Structured feature model | -0.123733 | -0.009943 | 0.227923 | .4650/.3959/.4253 | .2/.4/.4 | .1041/.2170 | 0.454879 | 0.112371 |
| Prompt-only cross-encoder | -0.054631 | -0.054573 | -0.035283 | .3715/.3796/.4265 | .0/.0/.2 | .1238/.2485 | 0.519767 | 0.204088 |
| Transition-only baseline | 0.074578 | 0.004124 | n/a | .5289/.5668/.5774 | .0/.4/.4 | .2054/.3516 | 0.580313 | 0.046163 |

None exceeds the transition-only NDCG@4 baseline by the required `0.05`.
This is the central scientific failure; pooled sign accuracy is secondary.

## Part B: Objective Shortcut Repair

The original 4096D vectors were retained. Interaction branches were trained
against the cell-A residual with masked Huber, state-grouped listwise,
within-state gap-weighted ranking, and a small raw-utility auxiliary. Main
effect heads did not receive listwise/ranking losses.

Double-held-out metrics are pooled Spearman / per-state Spearman / NDCG@4 /
residual Spearman / Huber.

| Model | Correct | Shuffled state | Shuffled transition |
|---|---:|---:|---:|
| Decomposed additive | .1510/.0472/.5515/.0000/.0609 | -.1499/.0472/.5515/.0000/.0816 | .1482/.1324/.6242/.0000/.0597 |
| Decomposed signed bilinear | .1667/.0504/.4849/.1298/.0739 | -.1595/.0668/.4721/-.0604/.0903 | .0907/-.0802/.3362/.0608/.0754 |
| Decomposed concat interaction | .1504/.0466/.4703/.0874/.0626 | -.1472/.0414/.5297/.2156/.0808 | .1553/.1358/.6286/-.1487/.0615 |

The signed model improved D NDCG@4 over transition shuffle by `0.148670`, but
the task-grouped bootstrap interval included zero (`[-0.251855, 0.388492]`).
Its correct-minus-state-shuffle gain was only `0.012744`, and only one of four
held-out tasks showed positive relative behavior. The objective-only repair
gate failed, so Parts C/D were required.

## Parts C/D: Span-Aware Multi-View Representations

The canonical full-demo prompt and its three examples were unchanged. The
side channel extracted five state views and five transition views, with token
mean and span-final-token pooling. Final-layer and mean-final-four-layer
readouts were evaluated only through cell-A grouped CV.

Span validation:

| Quantity | Value |
|---|---:|
| State records | 32 |
| Transition records | 148 |
| Total spans | 900 |
| Exact decoded spans | 461 |
| Token-boundary-expanded but source-aligned spans | 439 |
| Invalid spans | 0 |
| State token min/mean/max | 7,889 / 11,725.06 / 22,433 |
| Transition token min/mean/max | 7,413 / 11,470.13 / 35,608 |

The expanded spans reflect tokenizer boundaries and decode to source-aligned
text; they are not semantic expansion or truncation. Query views contain no
ground-truth next action or future observation.

Cache shapes are `[32,10,4096]` for states and `[148,10,4096]` for
transitions under both layer readouts. Geometry across views:

| Side / layer | Centered effective-rank range | Pairwise-cosine mean range | App probe range | Task probe range | Step probe range |
|---|---:|---:|---:|---:|---:|
| State / final | 9.980-26.714 | .5238-.9790 | .219-1.000 | .031-1.000 | .344-.500 |
| State / final-four mean | 12.490-27.227 | .9260-.9982 | .219-1.000 | .031-1.000 | .375-.531 |
| Transition / final | 24.129-92.432 | .5245-.9924 | .250-.939 | .007-1.000 | .236-.797 |
| Transition / final-four mean | 24.193-94.833 | .8852-.9980 | .250-.926 | .007-1.000 | .236-.784 |

The views are not numerically constant, although some pooled views are highly
anisotropic. Full singular spectra and per-view probes are in
`parts_c_d_summary.json`.

Double-held-out interaction results:

| Model | Selected layer | NDCG@4 | Raw Spearman | Per-state Spearman | Residual Spearman | State-shuffle NDCG@4 | Transition-shuffle NDCG@4 | Positive tasks | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Signed bilinear | final | .374059 | -.055752 | -.040277 | .082560 | .403282 | .341603 | 0/4 | fail |
| Low-rank tensor | final | .554092 | .024760 | .121518 | .188452 | .533599 | .420201 | 1/4 | fail |
| Pair MLP upper bound | final-four mean | .392459 | .024270 | .015900 | .333560 | .508507 | .415213 | 0/4 | fail |
| Structured features | n/a | .395926 | -.123733 | -.009943 | .227923 | .502783 | .290140 | 1/4 | fail |

The strongest field-compatible candidate, the low-rank tensor, remained below
transition-only NDCG@4 (`0.554092` versus `0.566808`) and had only a `0.020493`
state-shuffle drop. Its transition-shuffle NDCG contrast CI
`[-0.133444, 0.317761]` included zero. Structured features also failed, so
EXP-019 does not support the branch `frozen_qwen_pooling_representation_failure`.

## Part E: Prompt-Only Cross-Encoder Upper Bound

Because Parts B-D failed, the preregistered O(number-of-memories) frozen-Qwen
cross-encoder upper bound ran. Its prompt contains the canonical current state
and one legal raw transition, but never the ground-truth target action. Qwen
remained frozen and no behavioral loss was backpropagated through it.

Preflight and cache validation:

| Quantity | Value |
|---|---:|
| Exact scoreable pairs | 4,579 |
| Over-context / truncated | 0 / 0 |
| Prompt tokens min/mean/max | 15,318 / 22,923.28 / 40,829 |
| Cached spans | 13,737 |
| Aggregate shape | `[4579,12288]` |
| Aggregate file SHA256 | `d40f4e2dfc516a02bb4066f195a27e6e3612a344a6761858787aefe86bc1c763` |

Attempt 006 computed all 4,579 atomic rows before a post-cache path error.
Attempt 007 validated and reused all 4,579 rows, computed zero duplicates, and
completed training/evaluation. Cell-A-only CV selected 30 epochs.

| Cell | NDCG@4 | Per-state Spearman | Raw Spearman | Residual Spearman | State shuffle | Transition shuffle |
|---|---:|---:|---:|---:|---:|---:|
| A | .820228 | .612301 | .768382 | .651570 | .283631 | .298186 |
| B | .309609 | .088357 | -.048635 | .069637 | .326271 | .216550 |
| C | .661463 | .515241 | .713342 | .565254 | .341249 | .383926 |
| D | .379564 | -.054573 | -.054631 | -.035283 | .350995 | .495154 |

On D, transition shuffling improves NDCG@4 by `0.115589`. The registered
correct-minus-transition-shuffle bootstrap mean is `-0.109452`, CI
`[-0.287746, 0.084727]`. Correct pairing beats both single-axis baselines on
only one of four held-out query tasks. The cross-encoder upper bound fails.

## Part F: Query-Task Learning Curves

All learning curves use cell A only, deterministic nested query-task subsets,
five grouped folds, and all 29 train-transition parents at each level. The 105
model/fold/level result rows are complete.

| Model | NDCG@4 at 4 / 8 / 12 tasks | Residual Spearman at 4 / 8 / 12 | 12-task NDCG std | Still rising | Unstable |
|---|---:|---:|---:|---|---|
| Decomposed concat | .4634/.4512/.4318 | .3353/.2468/.0845 | .0598 | no | no |
| Decomposed signed | .4987/.5212/.5112 | .1899/.1786/.1009 | .0705 | no | no |
| Multi-view low-rank | .5119/.5023/.4866 | .0429/.0713/.1802 | .0579 | yes | no |
| Multi-view pair MLP | .4695/.4877/.3745 | .0983/.1092/.2171 | .0486 | yes | no |
| Multi-view signed | .4230/.4581/.4253 | .0058/.0582/.0311 | .1101 | no | yes |
| Prompt cross-encoder | .4695/.4261/.4311 | -.0689/-.1511/.0032 | .0537 | yes | yes |
| Structured features | .5420/.5227/.4870 | -.0041/.0952/-.0066 | .0669 | no | yes |

The cross-encoder's final NDCG gain is small (`+0.005078`) while residual
Spearman recovers by `+0.154288`; fold behavior remains unstable. Under the
registered rule, a failed strongest upper bound plus a rising 12-task curve
selects `query_task_coverage_insufficient`.

An expanded raw-transition teacher cache was estimated but not launched:

| Query states | Legal pairs | Scoreable | Over-context | Projected H100 hours |
|---:|---:|---:|---:|---:|
| 64 | 9,280 | 9,158 | 122 | 4.5510 |
| 96 | 13,920 | 13,737 | 183 | 6.8265 |

These are exact linear projections from the immutable EXP-017 pair counts at
`1.788982` seconds per scoreable pair, not a proposal to downsample.

## Attempt Ledger And Recovery

The append-only ledger contains nine paired attempts under one run UUID:

| Attempt | Result | Source commit | Stop reason / completed phase |
|---|---|---|---|
| 001 | failed | `b012c09` | EXP-018 reproduction tolerance rejected 1.12e-9 serialization noise |
| 002 | complete | `385fb8c` | Parts A/B |
| 003 | failed | `ed202b4` | generation-boundary tokenizer edge |
| 004 | failed | `d85bfe1` | source-goal tokenizer boundary expansion |
| 005 | complete | `0c69850` | Parts C/D |
| 006 | failed | `b151dde` | wrong post-cache multiview artifact path; all 4,579 rows complete |
| 007 | complete | `00500b8` | Part E, reusing 4,579 rows |
| 008 | failed | `00500b8` | wrong cross-encoder aggregate path before Part-F training |
| 009 | complete | `cbb75d4` | Part F and final decision |

Regression tests were added for every repair. The failed artifacts and exit
reasons remain preserved. Every ledger row records
`scientific_parameter_changed=false`; no model setting, split, label, gate,
or cache identity changed. Laptop/Codex disconnections did not create a
duplicate run UUID or overwrite atomic rows.

Active attempt wall time was `10,505.717 s` (`2.9183 h`), including failed
attempts and the initial cross-encoder cache construction. The span between
first start and final completion also includes monitoring and source-sync
gaps. Artifact size is `23,462,274,209 bytes` (`21.851 GiB`).

## Validation

Independent post-run validation passed with zero errors. It checked:

- all 11 immutable EXP-018 file hashes;
- exact A/B/C/D and 4,579 total row counts;
- all multiview shapes and tensor hashes;
- 4,579 atomic cross-encoder rows and the aggregate hash;
- prediction, checkpoint, configuration, and manifest hashes;
- all 105 learning-curve results;
- nine paired append-only attempts; and
- the registered decision and hard-scope stop.

The strengthened validator tests passed on Lambda (`6 passed`). The complete
local suite before the final documentation-only commit passed
`204 passed, 1 skipped`.

## Decision

VERIFIED:

- Main effects explain 44.91% of cell-A utility variance, leaving substantial
  nontrivial residual structure.
- Objective-only, multi-view, structured-feature, and cross-encoder candidates
  all fail the registered memory-specific D gate.
- The prompt-only cross-encoder fits A and generalizes to C, but fails
  state-held-out B and double-held-out D and does not preserve correct
  transition pairing on D.
- The strongest A-only learning curves are not uniformly saturated; the
  prompt cross-encoder remains slightly rising and unstable at 12 query tasks.

INFERENCE:

- Current evidence is more consistent with insufficient query-task coverage
  for estimating cross-task interaction generalization than with a proven
  absence of prompt-only interaction signal.
- Adding more transition views alone is unlikely to repair D while query-state
  coverage remains 12 train tasks.

UNVERIFIED:

- Whether 64 or 96 query states would make the cross-encoder and a
  field-compatible interaction pass the double-held-out gate.
- Whether a representation that passes after expanded coverage would support
  the behavioral `p(s,m_transition)` program.

Final branch: `query_task_coverage_insufficient`.

Recommended next milestone: expand query-state teacher coverage with the same
transition panel, leakage rules, no-truncation policy, and two-axis evaluation;
rerun learning curves and the prompt cross-encoder first, then require a
field-compatible model to retain at least 70% of the upper-bound gain before
reviewing behavioral training. Do not start behavioral training automatically.

At final audit, no tmux session or experiment Python process was active. The
H100 reported `0 MiB` allocated and `0%` utilization. Lambda is safe to
terminate after the final record commit is synchronized.
