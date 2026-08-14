# EXP-018 Factorized State-Conditioned Transition Program Pilot

Status: **stopped at the pre-behavior representation gate**

Run UUID: `state_conditioned_transition_6b_20260814_001`

Branch: `research/v4-decision-transition-memory`

Source commit: `0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_6b_20260814_001`

Decision branch: `state_transition_representations_insufficient`

## Scope And Stop Decision

EXP-018 first tested whether the frozen Qwen query-state and complete
decision-transition representations contained enough pairwise information to
justify expensive Qwen-backprop program training. The required concat-MLP
upper-bound gate failed on the double-held-out cell. Under the preregistered
decision tree, execution therefore stopped after Parts A-D.

The factorized behavioral models in Parts E-G were not trained or evaluated.
The optional state-conditioned trajectory control was not triggered. This is
not a behavioral failure measurement for `p(s,m_transition)`; it is a failed
precondition for launching that measurement with the current frozen
representations.

No selector, program head, injector, production full bank, Stage C2,
end-to-end RCMF model, AppWorld generation/evaluation, or V4 tag was created.
Qwen3-8B and the EXP-016C decoder remained frozen, and no Qwen behavioral
backpropagation occurred.

## EXP-017 Reuse Validation

The EXP-017 artifact was read-only and passed reconstruction, identity,
leakage, target-utility, duplicate, and no-truncation checks.

| Quantity | Count |
|---|---:|
| Train parent trajectories | 37 |
| Extracted transitions | 499 |
| Deterministic panel transitions | 148 |
| Query states | 32 |
| Legal teacher pairs | 4,640 |
| Scoreable pairs | 4,579 |
| Over-context masked pairs | 61 |

Maximum within-state L0 spread and maximum target-token utility identity error
were both exactly zero. All three deterministic positive/neutral/negative
teacher rescoring checks reproduced cached loss and utility exactly. The 61
over-context rows remained masked and were never truncated or assigned a
utility.

## Frozen Representations

| Representation | Shape | Tensor SHA256 |
|---|---:|---|
| Query state | `[32,4096]` | `a5a301cca070111b7c449423724378d0fb51b42e732163fb2b6adedf48cd73c6` |
| Transition | `[148,4096]` | `346224c535841a1cdc1de1454693f2d940716103956d96c35def28dac12d9c0c` |

Query representations reused the validated canonical full-demo V3 cache. The
renderer consumes state/history only; future target action text is not an
input. Transition representations encode source goal, complete pre-action
state, complete action, and complete post-action observation. Transition token
counts ranged from 7,413 to 35,608 with mean 11,470.13. All 148 were encoded
without truncation; none required multiple chunks. Validation-task parent
trajectories were excluded.

## Two-Axis Split

A deterministic SHA256-ordered parent split assigned 29 train parents and 8
held-out parents. Query states retained the existing 24-train / 8-held-out
split. The parent-split manifest hash is
`de17e00ccf67080c822d689b6809f377eea3c2c77e07879c708dedced0eb790b`.

| Cell | State/transition status | Pairs | States | State tasks | Transitions | Parents | Utility mean/std | Pos/neutral/neg |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | train / train | 2,667 | 24 | 12 | 115 | 29 | 0.067686 / 0.331588 | 1,343 / 370 / 954 |
| B | held-out / train | 904 | 8 | 4 | 115 | 29 | 0.024758 / 0.096050 | 403 / 349 / 152 |
| C | train / held-out | 752 | 24 | 12 | 32 | 8 | 0.084375 / 0.324114 | 408 / 119 / 225 |
| D | held-out / held-out | 256 | 8 | 4 | 32 | 8 | 0.030112 / 0.074908 | 117 / 103 / 36 |

The four cells sum to all 4,579 scoreable teacher rows. Exact task, parent,
app, utility, and early/middle/late distributions are recorded in
`two_axis_split_manifest.json` and `parts_a_d_summary.json`. Labels from B, C,
and D were never used for model selection.

## Train-Only Cross-Validation

Five-fold task/parent-grouped CV used only cell A. Fixed candidate epoch
counts were selected by the registered train-only rule.

| Predictor | Selected epochs | CV Spearman | CV sign | CV Huber |
|---|---:|---:|---:|---:|
| State only | 60 | 0.106709 | 0.555223 | 0.201293 |
| Transition only | 10 | 0.106040 | 0.533102 | 0.189425 |
| Additive state + transition | 30 | 0.156770 | 0.595991 | 0.186811 |
| Signed bilinear | 60 | 0.055431 | 0.541627 | 0.209526 |
| Concat MLP upper bound | 30 | 0.141229 | 0.564020 | 0.215158 |

The empirical cell-A global utility mean was `0.0676861715`. Original
held-out labels did not participate in hyperparameter selection.

## Correct-Pair Held-Out Metrics

Values are Spearman / positive-negative sign agreement / Huber.

| Predictor | B: held-out state | C: held-out transition | D: double held-out |
|---|---:|---:|---:|
| Global mean | n/a / 0.726126 / 0.039392 | n/a / 0.644550 / 0.163275 | n/a / 0.764706 / 0.030970 |
| State only | 0.298138 / 0.637838 / 0.056440 | 0.546486 / 0.748815 / 0.116702 | 0.205547 / 0.601307 / 0.052382 |
| Transition only | 0.055217 / 0.700901 / 0.036871 | 0.143042 / 0.647709 / 0.160838 | 0.093928 / 0.745098 / 0.026967 |
| Additive | 0.266599 / 0.623423 / 0.062141 | 0.595247 / 0.770932 / 0.113975 | 0.140031 / 0.601307 / 0.058822 |
| Signed bilinear | 0.198345 / 0.601802 / 0.074904 | 0.700908 / 0.770932 / 0.083757 | 0.111083 / 0.575163 / 0.062106 |
| Concat MLP | 0.203518 / 0.724324 / 0.143361 | 0.633956 / 0.701422 / 0.141778 | 0.059482 / 0.758170 / 0.126287 |

Cell C alone contains strong predictable structure, but neither interaction
model composes that signal with unseen query tasks in cell D. On D, the concat
MLP is worse than state-only in both Spearman and Huber, and worse than
transition-only in Huber.

## Pairing Controls

The following double-held-out values are Spearman / sign / Huber.

| Model/control | Metrics |
|---|---:|
| Concat correct | 0.059482 / 0.758170 / 0.126287 |
| Concat shuffled state | -0.312102 / 0.751634 / 0.148919 |
| Concat shuffled transition | 0.043260 / 0.758170 / 0.126444 |
| Concat both shuffled | -0.055857 / 0.751634 / 0.138353 |
| Concat mean state | 0.070542 / 0.764706 / 0.110718 |
| Concat mean transition | 0.044478 / 0.764706 / 0.131287 |
| Bilinear correct | 0.111083 / 0.575163 / 0.062106 |
| Bilinear shuffled state | -0.235637 / 0.653595 / 0.081539 |
| Bilinear shuffled transition | 0.060834 / 0.575163 / 0.062649 |
| Bilinear both shuffled | 0.046230 / 0.562092 / 0.069467 |
| Bilinear mean state | 0.034292 / 0.764706 / 0.039342 |
| Bilinear mean transition | 0.139339 / 0.601307 / 0.059140 |

State shuffling degrades both interaction models. Transition shuffling changes
concat Spearman by only `0.016222`, below the registered materiality threshold
of `0.05`, and changes its Huber by only `0.000158`. The concat upper bound
therefore does not demonstrate transition-specific double-held-out behavior.

## Representation Gate

The gate required the concat MLP on D to achieve Spearman at least 0.20, sign
agreement at least 0.60, materially beat state-only and transition-only, and
materially degrade under both state and transition shuffle. It passed only the
sign and state-shuffle checks. The signed bilinear model separately failed the
Spearman, sign, and baseline-comparison checks.

Final gate results:

- concat MLP: failed;
- signed bilinear: failed;
- proceed to behavioral training: false;
- branch: `state_transition_representations_insufficient`.

This invokes decision-tree branch A. No loss/rank sweep or behavioral program
training was used to work around the failed gate.

## Field Algebra

The future-compatible V0/T algebra was implemented and tested independently of
behavioral training. All checks passed:

- explicit per-transition sum equals compiled tensor contraction;
- single-transition add/remove and exact restoration;
- replacement and exact restoration;
- parent removal by subtracting all child deltas and exact restoration;
- arbitrary insertion order;
- runtime tensor shape independent of transition count.

The synthetic runtime shapes were `V0=[7,11]` and `T=[7,5,11]`. No q/k model
was trained and no production field was constructed.

## Reproducibility And Recovery

The append-only attempt ledger contains one start/end pair:

- attempt: `attempt-001`;
- tmux: `exp018`;
- started: `2026-08-14T13:25:18.064696Z`;
- ended: `2026-08-14T13:29:16.355473Z`;
- exit code: 0;
- stop reason: `normal_completion`;
- resume checkpoint: none;
- parent attempt: none;
- scientific parameter changed: false.

The network/Codex disconnect did not interrupt the Lambda process and did not
create a duplicate run. The run used one UUID and one attempt only. Persistent
heartbeat ended with status `completed`; all 148 transition representations
and all 75 CV/final model jobs were complete.

Independent post-run validation passed with zero errors. It verified the pair
count, all four cell counts, representation hashes, run UUID, append-only
attempt completion, gate branch, and hard-scope stop.

Source tests passed locally (`169 passed, 1 skipped`) and on Lambda
(`170 passed`).

## Runtime And Artifacts

- Parts A-D wall time: `238.5433 s` (3m 58.5s).
- Artifact size: `2,318,267,125 bytes` (about 2.159 GiB).
- Attempt log:
  `/lambda/nfs/rcmf-persist/runs/logs/state_conditioned_transition_6b_20260814_001_parts_a_d_attempt_001.log`.
- Main summary: `<artifact>/parts_a_d_summary.json`.
- Independent validation: `<artifact>/parts_a_d_postrun_validation.json`.
- Attempt ledger: `<artifact>/attempts.jsonl`.
- Heartbeat: `<artifact>/heartbeat.json`.
- Two-axis manifest: `<artifact>/two_axis_split_manifest.json`.
- CV manifest: `<artifact>/cheap_gate/grouped_cv_manifest.json`.
- Final predictor checkpoints: `<artifact>/cheap_gate/checkpoints/final/`.

At final audit, no tmux server or experiment Python process was active. The
H100 reported 0 MiB allocated and 0% utilization. The instance is safe to
terminate after the final record commit is synchronized.

## Interpretation

VERIFIED:

- The EXP-017 cache, two-axis split, frozen representations, and field algebra
  are valid under the registered checks.
- The concat MLP upper bound does not meet the double-held-out representation
  gate and does not materially depend on transition pairing there.
- The signed bilinear interaction also fails the gate.
- EXP-018 correctly stopped before behavioral training.

INFERENCE:

- The immediate bottleneck is the current frozen representation path or its
  ability to generalize the interaction across both unseen query tasks and
  unseen transition parents. It is premature to blame the frozen decoder,
  factorized program equation, or K=4 injection path because none was trained
  in this run.

UNVERIFIED:

- Whether richer state/transition readouts, token-level cross-attention, or a
  representation trained only on cell-A labels can pass the same strict
  double-held-out interaction gate.
- Whether `p(s,m_transition)` would pass behavioral gates after such a
  representation repair.

The recommended next milestone is a representation-focused diagnostic using
the same immutable two-axis manifest. It should determine whether richer
frozen-Qwen token pooling or train-only interaction readouts recover genuine
transition-shuffle-sensitive signal on D before any Qwen behavioral training.
