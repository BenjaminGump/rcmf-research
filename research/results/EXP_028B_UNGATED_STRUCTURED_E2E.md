# EXP-028B Ungated Structured Compiler E2E Audit

## Identity

- Run UUID: `ungated_structured_compiler_e2e_7h2_20260824_001`
- Global seed: `25101`
- Starting commit: `6343f433ba96442d4a6e421ded072cfd85406fb8`
- Source commit: `8a784b270482cbdb38da5bf2b415d2a1eeb10c3e`
- Context-terminal accounting commit: `2a76e135ad258b271d18167edcd7efcac0221c5a`
- Gate SHA256: `7c49ace41b81763df4457c976d8800ccb1af11559b016fb98e04cf851098416d`
- Compiler SHA256: `95bc2869df1084eb1166cadeb0edfad584814d5fe0c049b3d7beab59b2c4cab3`
- Selector SHA256: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`

## Gate Distribution

The frozen feature order, categorical vocabulary, missing-value behavior,
standardizer, gate checkpoint, temperature `2.0`, threshold `0.60`, and
inference code all match. Recomputed live probabilities reproduce the saved
EXP-028A values with maximum absolute error `1.1920929e-7`. No first37 outcome
was read by this audit.

| Gate diagnostic | Heldout train | First37 live |
| --- | ---: | ---: |
| Rows | 98 | 871 available / 873 turns |
| P(POSITIVE) mean | 0.288031 | 1.00525e-8 |
| P(POSITIVE) median | 0.251474 | 0 |
| P(POSITIVE) p95 | 0.819380 | 0 |
| P(POSITIVE) maximum | 0.943520 | 8.75569e-6 |
| Activations at 0.50 | 15 | 0 |
| Activations at 0.60 | 11 | 0 |
| Activations at 0.70 | 9 | 0 |
| Activations at 0.80 | 7 | 0 |
| Activations at 0.90 | 3 | 0 |

Two frozen EXP-028A live turns, `ff58e36_1` steps 39 and 40, have explicit
missing feature rows because the selected raw class had no context-feasible
member. They remain missing and are not imputed.

Diagnosis: `broad_feature_state_distribution_shift`.

- Balanced train-vs-live classifier heldout AUC: `0.997624`.
- Features with absolute standardized mean difference at least 0.5:
  `64/186` (`34.41%`).
- Maximum absolute standardized mean difference: `4.838915`.
- Every categorical UNK feature stays zero in both domains; vocabulary drift
  is not the explanation.

Largest shifts:

| Feature | SMD | Live outside train range |
| --- | ---: | ---: |
| `pair.documentation_compatibility` | -4.8389 | 97.47% |
| `pair.predicted_memory_api_mass` | -3.4363 | 0% |
| `state.completion_probability` | +2.8530 | 0% |
| `pair.predicted_memory_app_mass` | -2.3202 | 0% |
| `selector.score_min` | -1.8148 | 1.61% |
| `state.action_probability[python_or_reasoning]` | +1.7849 | 0% |
| `state.intent_api_margin` | -1.6632 | 90.13% |
| `state.intent_api_confidence` | -1.6450 | 92.19% |

The strongest first-order positive-logit shift is
`pair.documentation_compatibility`; its live mean shift contributes
approximately `+9058` logit units under the frozen gate's local derivative.
This is a diagnosis of the frozen model, not a proposed recalibration.

## First37 Results

The same AppWorld 0.1.0 harness, seed, prompt, selector, compiler, residual
layers `[7,14,21,28]`, and last four user-token positions were used. U1 and U2
forced the compiler multiplier to `1.0`; student prompts contained no raw
transition text. The U2 mapping was frozen over all 499 transitions before
generation, always changed transition ID, and changed signature class for all
499 mappings.

| Condition | Success | Steps | Prompt tokens | Generated tokens | Execution exceptions |
| --- | ---: | ---: | ---: | ---: | ---: |
| U0 matched bare | 8/37 | immutable | immutable | immutable | immutable |
| U1 correct structured compiler | 0/37 | 1,718 | 22,632,756 | 144,863 | 0 |
| U2 transition-shuffled compiler | 2/37 | 1,156 | 13,957,060 | 114,660 | 0 |

U2 successes: `0d01c76_2`, `325d6ec_1`.

- U1 - U0: `-8` tasks; U1 retained none of the eight bare successes.
- U1 - U2: `-2` tasks; the correct compiler was worse than its shuffled
  control.
- U2 - U0: `-6` tasks; U2 retained two bare successes.
- U1 produced four locked no-truncation context terminations. They are counted
  as end-to-end task failures; no prompt, context limit, or history rule was
  changed.
- Task-level changes are single-seed descriptive diagnostics, not statistical
  claims.

Actual accepted task wall time was `4.1620` H100-hours. Summed active attempt
wall time, including the stopped U1 attempt and reload, was approximately
`4.248` H100-hours; end-to-end elapsed time was approximately `4.329` hours.
The final durable artifact directory is 16 MiB.

## Fresh-Test Audit

The official AppWorld 0.1.0 `test_normal` pool contains exactly 168 tasks. The
historical full bare baseline has a per-task result for all 168, so all 168 are
exposed under the preregistered EXP-028B definition. Untouched count is zero.

Therefore `fresh_test37_post_exp028b.json` records
`insufficient_untouched_tasks` with `0/37` selected tasks. No task was replaced,
and no outcome was used to manufacture a nominal fresh set.

- Exposure manifest SHA256:
  `c94889a80e22284cf0c805c71586488c57540e30e381c54b9bf36d2da308146c`
- Source artifact manifest SHA256:
  `a7495b50a5dba9ecb036ae296a3a56335353aa183e7cf837956982b516dc212f`

## Decision

Reached branch:

`structured_compiler_live_specificity_failed`

The curated one-step memory-specific difference does not survive complete
live ReAct trajectories. Lowering or retraining the gate alone cannot repair
the compiler because ungated correct compilation is worse than both bare and
transition shuffle.

Stop structured-compiler work for the submission. Do not start full-bank
integration, gate tuning, another compiler, Qwen training, Stage C2, or V4
tagging. The next 48-hour review should choose between a tightly bounded fixed
memory-reader adapter study and narrowing the paper to the clean provenance,
selector, oracle raw-memory, carrier-capacity, and negative amortization
results.
