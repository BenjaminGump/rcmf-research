# EXP-027B Matched-Harness Bare and Memory-Specific Deep Amortization

Date: 2026-08-23

Run UUID: `memory_specific_deep_amortization_7g_20260823_001`

Branch: `research/v4-memory-specific-deep-amortization`

Starting commit: `593fe203ab9db05889ae576da59b35124f2014ab`

Primary source commit: `6f5f5bd22411f8f12a25111935f074a98a98d3b0`

Bounded infrastructure-fix commit:
`bcf768298dccc410fc7fb411bf434bbd20e814a8`

Global seed: `25101`

Decision branch: `memory_specific_deep_amortization_failed`

## Summary

EXP-027B corrected two confounds from EXP-027A. First, bare Qwen was rerun on
the exact first-37 raw-memory harness. Second, the unchanged observation-
excluded PairMLP and fixed deep-residual carrier were trained with raw-policy
supervision plus direct transition- and state-mismatch gradients toward bare
Qwen.

The matched bare condition reached `8/37`, while automatic selector plus raw
transition remained `5/37`. The policy-amortized PairMLP learned strong policy
and mismatch separation under teacher forcing, but its correct, transition-
shuffled, and state-shuffled programs produced the same primary one-step
action-signature and semantic-successor rates. The one-step classification is
therefore `CLEAR_FAILURE`.

## Frozen Contract

- Model: frozen `Qwen/Qwen3-8B`.
- Carrier: input residual stream at layers `[7,14,21,28]`.
- Positions: last four user tokens only.
- Prompt: canonical full-demo prompt with all three demonstrations.
- Selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`.
- Clean replay lineage remained unchanged.
- Compiler boundary: six observation-excluded transition vectors.
- Architecture: unchanged state tower, transition tower,
  `[state, transition, state*transition] -> 256D -> shared no-bias residual decoder`.
- Exactly one seed was used. Qwen, selector, representations, prompt, carrier,
  architecture, ratio budget, and data split were not changed.

## Phase A: Exact-Harness Bare First37

The bare run used the exact EXP-027A Phase-A bridge with selector and memory
disabled. `evaluation.success` was authoritative.

| Condition | Success |
|---|---:|
| Matched-harness bare | `8/37` |
| EXP-027A automatic raw memory | `5/37` |
| Historical unmatched bare, secondary only | `10/37` |

Matched bare success IDs:

`0d01c76_1`, `0d01c76_2`, `29a7b7e_3`, `325d6ec_1`, `8749218_1`,
`8749218_2`, `8749218_3`, `d6ac34d_2`.

Raw memory versus matched bare:

- Retained: `325d6ec_1`.
- Gained: `29a7b7e_1`, `325d6ec_3`, `634f342_1`, `634f342_3`.
- Lost: `0d01c76_1`, `0d01c76_2`, `29a7b7e_3`, `8749218_1`,
  `8749218_2`, `8749218_3`, `d6ac34d_2`.

The raw-memory result is `CLEARLY WEAK` under the preregistered interpretation
band. The condition executed 872 steps in 3238.99 seconds, with 10,519,521
prompt tokens and 113,635 generated tokens. Environment identity included
PyTorch `2.11.0+cu128`, Transformers `4.57.6`, and model config commit
`b968826d9c46dd6066d109eabc6255188de91218`.

## Pair and Teacher Contract

Structural pair counts were `A/B/C/D/E = 607/135/112/112/135`, with 970
unique pairs. The fixed task-grouped A split was 479 train pairs over 29 tasks
and 128 validation pairs over 8 disjoint query tasks.

Policy-scoreable counts were:

| Cell | Scoreable rows |
|---|---:|
| A train | 478 |
| A validation | 128 |
| B | 134 |
| C | 112 |
| D | 112 |
| E | 134 |

Two unique pairs were explicit context-headroom missing measurements. They
were not truncated, substituted, zeroed, or relabeled. The teacher cache
contained 968 rows: 252 immutable rows reused and 716 newly generated. Teacher
generation produced 38,010 tokens and completed without changing Qwen.

All 478 training pairs received deterministic transition and state mismatch
partners from A training only. All transition mismatches changed signature
class and all state mismatches changed query task. No held-out outcome entered
mismatch construction.

## A-Only Training and Selection

The fixed selection score was:

`(zero_raw_KL - correct_raw_KL) - 0.5 * (transition_mismatch_bare_KL + state_mismatch_bare_KL)`

Selection additionally required positive state and transition specificity,
finite metrics, and maximum layer ratio at most 1. B/C/D/E were not inspected
for checkpoint selection.

| Update | Correct KL | Transition bare KL | State bare KL | Transition specificity | State specificity | Selection score | Utility Spearman | Utility Huber | Max ratio |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| u2 | 0.15974 | 0.02573 | 0.02555 | 0.10340 | 0.10358 | 0.10349 | 0.51301 | 0.21369 | 0.24141 |
| u4 | 0.14604 | 0.03042 | 0.03069 | 0.11241 | 0.11215 | **0.11228** | 0.52225 | 0.22991 | 0.30607 |
| u8 | 0.13969 | 0.03803 | 0.03768 | 0.11115 | 0.11151 | 0.11133 | 0.52216 | 0.24587 | 0.38677 |

The A-only rule selected u4. Checkpoint SHA256:
`8af2d1068ee0059c79edf740920f9d506b8a880459ff53656b449f767e5dffda`.

## Frozen Teacher-Forced Evaluation

Policy metrics are primary; utility Spearman and Huber are diagnostics.

| Cell | Rows | Correct raw-policy KL | Zero KL | KL improvement | Transition specificity | State specificity | Utility Spearman | Utility Huber | Max ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A validation | 128 | 0.14604 | 0.28887 | 0.14283 | 0.11241 | 0.11215 | 0.52225 | 0.22991 | 0.30607 |
| B | 134 | 0.10858 | 0.25440 | 0.14582 | 0.11741 | 0.11765 | 0.34686 | 0.27882 | 0.29964 |
| C | 112 | 0.07756 | 0.19583 | 0.11827 | 0.09328 | 0.09330 | 0.57075 | 0.18331 | 0.29904 |
| D | 112 | 0.07495 | 0.17718 | 0.10224 | 0.07279 | 0.07268 | 0.43628 | 0.22468 | 0.29980 |
| E | 134 | 0.10536 | 0.24231 | 0.13695 | 0.10859 | 0.10883 | 0.34822 | 0.28005 | 0.29970 |

The teacher-forced policy gate passed in every cell. Both mismatch programs
remained close to bare in policy space and both specificity gaps were positive.

## One-Step Audit

The formal audit generated and executed 180 new conditions: 45 each for P1
correct, P2 transition shuffle, P3 state shuffle, and P0 zero. All conditions
used a fresh AppWorld 0.1.0 world with same-world replay and execution. There
were zero exceptions; P0 exactly reproduced immutable C0 on all 45 states.

Primary 32-state results:

| Condition | Exact API | Action signature | Execution | Semantic successor | Observation similarity |
|---|---:|---:|---:|---:|---:|
| C0 bare | 0.78125 | 0.31250 | 0.93750 | 0.43750 | 0.44171 |
| F3 raw transition | 0.87500 | 0.68750 | 1.00000 | 0.78125 | 0.77039 |
| P0 zero | 0.78125 | 0.31250 | 0.93750 | 0.43750 | 0.44171 |
| P1 correct PairMLP | 0.84375 | 0.40625 | 0.87500 | 0.53125 | 0.53548 |
| P2 transition shuffle | 0.84375 | 0.40625 | 0.87500 | 0.53125 | 0.53894 |
| P3 state shuffle | 0.84375 | 0.40625 | 0.87500 | 0.53125 | 0.54369 |

All 45-state results:

| Condition | Exact API | Action signature | Execution | Semantic successor | Observation similarity |
|---|---:|---:|---:|---:|---:|
| C0 bare | 0.73333 | 0.35556 | 0.93333 | 0.42222 | 0.45121 |
| F3 raw transition | 0.80000 | 0.64444 | 1.00000 | 0.66667 | 0.67323 |
| P0 zero | 0.73333 | 0.35556 | 0.93333 | 0.42222 | 0.45121 |
| P1 correct PairMLP | 0.82222 | 0.46667 | 0.91111 | 0.51111 | 0.53726 |
| P2 transition shuffle | 0.82222 | 0.46667 | 0.88889 | 0.51111 | 0.54762 |
| P3 state shuffle | 0.82222 | 0.46667 | 0.91111 | 0.51111 | 0.54192 |

Primary paired comparisons with task-grouped 95% bootstrap intervals:

| Contrast | Metric | Difference | 95% CI |
|---|---|---:|---:|
| P1 - C0 | Exact API | +0.06250 | [0.00000, 0.14286] |
| P1 - C0 | Action signature | +0.09375 | [-0.05887, 0.26667] |
| P1 - C0 | Execution | -0.06250 | [-0.14706, 0.00000] |
| P1 - C0 | Semantic successor | +0.09375 | [0.00000, 0.18182] |
| P1 - C0 | Observation similarity | +0.09377 | [0.01155, 0.17515] |
| P1 - P2 | Action signature | 0.00000 | [0.00000, 0.00000] |
| P1 - P2 | Semantic successor | 0.00000 | [0.00000, 0.00000] |
| P1 - P3 | Action signature | 0.00000 | [0.00000, 0.00000] |
| P1 - P3 | Semantic successor | 0.00000 | [0.00000, 0.00000] |
| P1 - F3 | Action signature | -0.28125 | [-0.41667, -0.13782] |
| P1 - F3 | Semantic successor | -0.25000 | [-0.34378, -0.16129] |

Raw-gain retention was 0.25000 for action signature and 0.27273 for semantic
successor. P1 was positive versus C0 on `4/9` tasks, below the required 5/9.
The task-level signature/successor deltas were:

| Task | States | Signature delta | Successor delta | Positive |
|---|---:|---:|---:|---|
| 229360a_1 | 4 | 0.00000 | 0.00000 | no |
| 2a163ab_1 | 4 | 0.00000 | 0.00000 | no |
| 771d8fc_3 | 4 | +0.25000 | +0.25000 | yes |
| 7d7fbf6_2 | 3 | 0.00000 | 0.00000 | no |
| 82e2fac_3 | 3 | 0.00000 | 0.00000 | no |
| aa8502b_3 | 5 | 0.00000 | +0.20000 | yes |
| b0a8eae_1 | 3 | +0.33333 | 0.00000 | yes |
| b0a8eae_2 | 3 | -0.33333 | 0.00000 | no |
| e85d92a_1 | 3 | +0.66667 | +0.33333 | yes |

## Decision

VERIFIED:

- Matched-harness bare is `8/37`; automatic raw memory is `5/37`.
- Correct policy supervision improves teacher-forced raw-policy KL and creates
  positive state and transition specificity in A validation and B/C/D/E.
- P1 improves bare on several aggregate metrics but is behaviorally identical
  to both shuffles on action signature and semantic successor.
- P1 execution is 6.25 percentage points below C0 and only 4/9 tasks are
  positive.

INFERENCE:

- The corrected teacher-forced objective learns policy-space separation that
  does not survive deterministic generation as memory-specific behavior.
- Generic PairMLP/program work with the current observation-excluded
  representations is not justified for the submission.

UNVERIFIED:

- Whether an AppWorld-structured compiler using deployment-available
  procedural features can preserve memory-specific behavior.
- Whether a different reader architecture would work; this milestone does not
  authorize one.

Reached branch: `memory_specific_deep_amortization_failed`.

Do not start generic PairMLP, rank sweeps, factorized models, a memory-reader
adapter, full-bank integration, `p(s,m_transition)`, Stage C2, end-to-end RCMF,
full AppWorld evaluation, or V4 tagging. The only compiler rescue eligible for
separate review is an AppWorld-structured compiler using deployment-available
procedural features.

## Runtime and Attempts

- Initial expected H100 time: 10.4162 hours; 12-hour review threshold passed.
- Accounted phase/process time: 9.0974 hours.
- End-to-end wall span: 9.2817 hours.
- One-step formal wall time: 1516.81 seconds.
- One-step measured Qwen generation time: 308.90 seconds, or 0.08581 H100 hour.
- Artifact size: 2,071,732,476 bytes.
- Attempts: 9, all closed.

One bounded infrastructure attempt failed before generation because the reused
7F preflight expected the legacy runtime key
`pair_evaluation_seconds_expected`. Commit `bcf7682` added a tested fallback to
the already present policy-forward estimate. The corrected preflight projected
9.1583 total H100 hours. No condition, model, selector score, checkpoint, or
scientific parameter changed, and no output was duplicated.

## Artifacts

Lambda root:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/memory_specific_deep_amortization_7g_20260823_001`

Key paths:

- `attempts.jsonl`
- `phase_a_matched_bare_first37/summary.json`
- `compiler/pairmlp/pair_contract.json`
- `compiler/pairmlp/training_mismatch_manifest.json`
- `compiler/pairmlp/raw_policy_teacher_cache/summary.json`
- `compiler/pairmlp/training_summary.json`
- `compiler/pairmlp/checkpoints/model_u04.pt`
- `compiler/pairmlp/final_evaluation_summary.json`
- `compiler/pairmlp/one_step/condition_manifest.json`
- `compiler/pairmlp/one_step/generation_summary.json`
- `compiler/pairmlp/one_step/analysis.json`

Key SHA256 values:

- Config: `535c4b14ba458f8a43a481aaaba6193e6c73ad05edc372bb264604d4075962ab`
- Attempt ledger: `7404845cce2d8bcf475f8ef7549aa41cbfd5d5f5682294516befec6aa4106965`
- Teacher row set: `654b43262da2819ed3b0721def7dc6d54a9b2b5e9c404306fc9a5435558de43a`
- Selected checkpoint: `8af2d1068ee0059c79edf740920f9d506b8a880459ff53656b449f767e5dffda`
- Condition manifest: `2f68cb3419b869d1eb4143e68c9c82f2b5ca7d14aed44f1fbc139d031ef3dd87`
- One-step analysis: `3c824ac1c10e1d2600b49db7cc231d7b9f20d530ed5bab4b96b54e563b695eae`

## Final Machine State

The persistent filesystem remained mounted. The EXP-027B processes and tmux
sessions exited normally. The H100 reported 0 MiB used and 0% utilization.
Only older idle shell tmux sessions remained. It is safe to terminate the
Lambda instance after Git synchronization.
