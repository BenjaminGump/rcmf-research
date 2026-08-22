# EXP-027A: End-to-End Raw-Memory Upper Bound and Deep-Residual Amortized Compiler

Run UUID: `deep_residual_amortization_7f_20260822_001`

Branch: `research/v4-deep-residual-amortization`

Starting commit: `f4ec45b2d16ccebc17b481d629a0a3a445d38aa6`

Final source commit: `8467756c4ebb433b4bcceef5bce65e0a25e82c5b`

Global seed: `25101`

Decision branch: `deep_residual_amortization_failed`

## Executive Result

The experiment produced two independent negative results on the submission
critical path.

1. The frozen automatic selector plus one raw transition at every ReAct turn
   solved `5/37` first test-normal tasks, versus the immutable bare-Qwen
   baseline of `10/37`. This is in the preregistered `CLEARLY_WEAK` band.
2. The observation-excluded PairMLP deep-residual compiler fit the
   teacher-forced target well across A/B/C/D/E, but its one-step behavior was
   not memory-specific. The correct program was worse than transition shuffle
   and indistinguishable from state shuffle on semantic successor.

The PairMLP one-step classification is `CLEAR_FAILURE`. Phase D factorized
training was not started. Full-bank integration, Stage C2, end-to-end compiled
RCMF, and V4 tagging remain blocked.

## Immutable Contract

- Qwen: frozen `Qwen/Qwen3-8B`.
- Selector: frozen EXP-025C-R deployment selector,
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`.
- Clean replay lineage: unchanged.
- Prompt: canonical full-demo prompt with all three demonstrations.
- Deep carrier: layers `[7,14,21,28]`, last four user tokens.
- Compiler boundary: observation excluded; six transition views only.
- Pair universe: A/B/C/D/E `607/135/112/112/135`.
- A split: `479` train pairs from 29 tasks and `128` validation pairs from 8
  disjoint query tasks.
- Student prompt raw transition text: `false`.

## Phase A: First-37 Raw-Memory End-to-End

The v3 finalizer promoted all 37 immutable v2 task rows without loading Qwen or
regenerating a task. It corrected only the summary pointer to AppWorld 0.1.0's
authoritative `evaluation.success` field.

| Metric | Result |
| --- | ---: |
| Raw-memory selector success | `5/37` |
| Bare-Qwen success | `10/37` |
| Interpretation | `CLEARLY_WEAK` |
| Total ReAct steps | `935` |
| Generated tokens | `110,179` |
| Prompt tokens | `21,903,331` |
| Mean raw-memory overhead | `11,001.57` tokens/turn |
| Task execution wall time | `4,673.47 s` (`1.2982 h`) |

Success IDs:

`29a7b7e_1`, `325d6ec_1`, `325d6ec_3`, `634f342_1`, `634f342_3`.

Paired changes versus bare:

- Retained: `29a7b7e_1`, `325d6ec_1`, `325d6ec_3`.
- Gained: `634f342_1`, `634f342_3`.
- Lost: `0d01c76_1`, `0d01c76_2`, `0d01c76_3`, `29a7b7e_2`,
  `8749218_1`, `8749218_2`, `8749218_3`.

## Phase B: PairMLP Compiler

The shared decoder maps a 256D PairMLP latent to the fixed
`4 x 4 x 4096` residual carrier. Qwen, selector, representations, and all
teacher rows remained frozen.

### A-Validation Curve

| Updates/pair | Spearman | Huber | Maximum ratio |
| ---: | ---: | ---: | ---: |
| 4 | `0.574940` | `0.156920` | `0.252909` |
| 8 | `0.551553` | `0.150732` | `0.320734` |
| 16 | `0.431858` | `0.159546` | `0.396998` |

The preregistered lowest-A-validation-Huber rule selected u8, not the final
visited u16 checkpoint.

Selected checkpoint SHA256:
`84633fa6460b52ac6723e0c7eb6b7673b7f0dfaa08de17688fa605de7b32a1ce`.

### Frozen Final Evaluation

| Cell | N | Spearman | Huber | Zero Huber | Reduction | State shuffle | Transition shuffle | Memory swap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A-val | 128 | `0.551553` | `0.150732` | `0.256587` | `41.25%` | `0.150853` | `0.151896` | `0.150959` |
| B | 135 | `0.572432` | `0.139097` | `0.249890` | `44.34%` | `0.139567` | `0.140187` | `0.140116` |
| C | 112 | `0.515658` | `0.135426` | `0.234543` | `42.26%` | `0.134640` | `0.136082` | `0.135157` |
| D | 112 | `0.545204` | `0.136781` | `0.240019` | `43.01%` | `0.136265` | `0.137335` | `0.137573` |
| E | 135 | `0.583406` | `0.136009` | `0.243227` | `44.08%` | `0.135933` | `0.136432` | `0.136999` |

Maximum live layer ratio was below `0.303`. The model is numerically stable
and predicts the scalar teacher well, but the correct-versus-control Huber
gaps are very small and sometimes favor a shuffled state.

## Phase C: PairMLP One-Step Behavior

The formal run generated and executed `180/180` new P1/P2/P3/P0 conditions.
Every condition used a fresh AppWorld 0.1.0 world, the same live replay world
and Python namespace for generation/execution, and no raw transition in the
student prompt. There were zero infrastructure exceptions.

### Primary 32 States

| Condition | Exact API | Action signature | Execution | Semantic successor | Observation similarity |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.44171` |
| F3 raw transition | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.77039` |
| P1 correct PairMLP | `0.87500` | `0.40625` | `0.84375` | `0.59375` | `0.58789` |
| P2 transition shuffle | `0.87500` | `0.43750` | `0.90625` | `0.62500` | `0.61724` |
| P3 state shuffle | `0.87500` | `0.46875` | `0.84375` | `0.59375` | `0.58789` |
| P0 zero | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.44171` |

P1 versus C0:

- Action signature: `+0.09375`, task-bootstrap 95% CI
  `[-0.06897, 0.27273]`.
- Semantic successor: `+0.15625`, CI `[0.00000, 0.32353]`.
- Execution: `-0.09375`, CI `[-0.19355, -0.02564]`.

Memory-specific controls:

- P1 minus P2 action signature/successor: `-0.03125/-0.03125`.
- P1 minus P3 action signature/successor: `-0.06250/0.00000`.
- Raw-gain retention: action signature `25.00%`; semantic successor `45.45%`.
- Positive tasks versus C0: `4/9`.

All-state P1 action signature/successor/execution was
`0.44444/0.53333/0.88889`; P2 was `0.46667/0.55556/0.93333`; P3 was
`0.48889/0.53333/0.88889`.

The preregistered classifier returned `CLEAR_FAILURE` because P1 did not beat
both shuffles, had no positive memory-specific gap, exceeded the allowed
execution drop, and was positive on fewer than five tasks.

## Runtime and Attempts

- Run wall span: `8.6048 h`.
- Experiment-accounted H100 time before one-step: `5.0723 h`.
- One-step Qwen generation H100 time: `0.0909 h`.
- Total accounted H100 time: approximately `5.1632 h`.
- One-step lifecycle wall time: `1,554.06 s` (`0.4317 h`).
- Artifact size: `1,761,517,591` bytes.
- Append-only ledger: 12 attempts, 24 start/end events, all closed.
- Ledger SHA256:
  `1fab95b46e81cd5bf14f6401aea9177d4b08be12e931bff5aea21e5413d4e423`.

Three ledger attempts ended nonzero during bounded infrastructure recovery:
two Phase-A bridge failures and one missing runtime-component key after u8.
All resumed from immutable rows/checkpoints without changing scientific
parameters. One later preflight invocation failed at Python import before the
ledger could initialize; commit `8467756c` corrected the internal loader
import, and the authoritative preflight then passed. No condition was
duplicated.

## Scientific Decision

VERIFIED:

- Automatic selected raw memory is weak on the paired first-37 end-to-end
  task audit (`5/37` versus bare `10/37`).
- The PairMLP compiler predicts teacher-forced utility across A/B/C/D/E.
- Its one-step output is not transition- or state-specific enough to support a
  deployable compiled-memory claim.
- No factorized Phase-D model or full bank was trained.

INFERENCE:

- Because the free deep-residual carrier previously matched raw behavior
  exactly, the present bottleneck is amortizing pair-specific behavior from
  the current observation-excluded representations and objective, not carrier
  capacity.
- The weak first-37 result also shows that the current one-step raw-memory
  benefit does not automatically compound into trajectory success.

UNVERIFIED:

- An AppWorld-enhanced structured compiler using adapter-level procedural
  features.
- A fixed trained memory-reader adapter.
- Full-bank compiled RCMF or end-to-end performance under either alternative.

Reached branch: `deep_residual_amortization_failed`.

Recommended next action: freeze this compiled-program path for the submission
and conduct an immediate paper-scope/architecture review. If exactly one
follow-up is authorized, choose between the two preregistered reviewed options
above; do not start another carrier, rank sweep, or factorized Phase D from
this checkpoint.

## Lambda Artifacts

Root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/deep_residual_amortization_7f_20260822_001`

- `phase_a_first37_v3/summary.json`
- `phase_a_first37_v3/report.md`
- `compiler/runtime_preflight.json`
- `compiler/pairmlp/training_summary.json`
- `compiler/pairmlp/checkpoints/model_u08.pt`
- `compiler/pairmlp/final_evaluation_summary.json`
- `compiler/pairmlp/one_step/condition_manifest.json`
- `compiler/pairmlp/one_step/preflight.json`
- `compiler/pairmlp/one_step/generation_summary.json`
- `compiler/pairmlp/one_step/analysis.json`
- `compiler/pairmlp/one_step/report.md`
- `attempts.jsonl`

No model cache, benchmark data, raw task output, or checkpoint is committed to
GitHub.
