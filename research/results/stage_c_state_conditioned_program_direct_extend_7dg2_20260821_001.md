# EXP-025D-G2 Final Scientific Report

## Outcome

- Run UUID: `state_conditioned_program_direct_extend_7dg2_20260821_001`
- Global seed: `25101`
- Starting commit: `d66733e1a3c47a405a5781c613965b55f8078af2`
- GPU training-source commit: `673e4e3244651b657dee8ded117a5697b73349c7`
- Final analysis-source commit: `f8dcc3695a7cc22f9cb3cf67a16f77a977e992c0`
- Parent run: `state_conditioned_program_direct_7dg_20260821_001`
- Parent u16 checkpoint SHA256:
  `9433518d828930dfc31e63d18f5477ba563b8870cb4a91ec3665f6890c5e90ff`
- Frozen selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`
- Clean replay-validated lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Selected checkpoint: u48
- Selected checkpoint SHA256:
  `659a7bc8349f8a2632697e79d7f0f1a0576595e044f467907a8ca93f7705eeb1`
- Selected global gain: `gamma=1.0`
- Teacher-forced gate: passed
- One-step gate: failed
- Decision branch: `calibrated_factorized_program_not_behaviorally_retained`
- Compiled program behavior validated: no

The r16 program was still improving at u16 and, after exact continuation,
passed the teacher-forced B/E gate. It did not retain enough of the raw
transition's one-step behavioral gain and did not beat the shuffled-transition
control. Full-bank integration and all later program stages remain blocked.

## Integrity And Recovery

The run restored the exact 479-pair u16 model, private decoder, Adam state,
RNG state, update counts, and pair ordering. Qwen, selector, representations,
prompt, demonstrations, observation-excluded boundary, K=4 injection, and
`last_user_k` were frozen. No B/C/D/E result was inspected during checkpoint
or gain selection.

The append-only ledger contains 10 closed attempts:

| Attempt | Result | Stop reason |
|---|---:|---|
| `exp025dg2-preflight-001` | 0 | normal completion |
| `exp025dg2-train-001` | 1 | atomic teacher rows were initially treated as one JSONL file |
| `exp025dg2-train-002` | 1 | serialized CUDA RNG state needed restoration through CPU ByteTensor |
| `exp025dg2-train-003` | 0 | normal completion |
| `exp025dg2-one-step-preflight-003` | 0 | normal completion |
| `exp025dg2-one-step-formal-003` | 0 | normal completion |
| `exp025dg2-one-step-analyze-003` | 0 | normal completion |
| `exp025dg2-finalize-003` | 0 | normal completion |
| `exp025dg2-one-step-analyze-seedfix-004` | 0 | analysis-only single-seed correction |
| `finalize-seedfix-004` | 0 | normal completion |

The first two training attempts stopped before the first update. Their source
repairs have regression tests and changed no scientific parameter. The first
one-step analysis used per-metric bootstrap seed offsets. It was preserved as
a deprecated provenance copy, the analysis code was fixed, and all intervals
were recomputed from the immutable 180 condition outputs using only seed
`25101`. Training, generations, row metrics, gate decisions, and condition
outputs did not change.

## Calibration Audit At U16

On the 128 A-validation pairs:

| Quantity | Value |
|---|---:|
| Teacher utility mean / std | `0.185754 / 0.408016` |
| Student utility mean / std | `0.003523 / 0.324747` |
| Student minus teacher bias | `-0.182232` |
| Pearson / Spearman | `0.545918 / 0.407772` |
| Teacher-from-student slope / intercept | `0.685898 / 0.183338` |

Category means, shown as teacher / student, were:

- negative, n=37: `-0.188843 / -0.074825`
- neutral, n=18: `0.001181 / -0.209047`
- positive, n=73: `0.421130 / 0.095648`

## Exact Continuation

| Checkpoint | A-val Spearman | A-val Huber | Relative Huber change | Decision |
|---|---:|---:|---:|---|
| u16 | `0.407772` | `0.248198` | baseline | continue |
| u32 | `0.593601` | `0.155799` | `-37.2278%` | continue |
| u48 | `0.614717` | `0.150141` | `-3.6317%` | stop |
| u64 | not visited | not visited | not applicable | prohibited by continuation rule |

The u48 checkpoint was selected using A-validation only. The training stop
reason was `preregistered_continuation_rule_not_met`.

The A-only gain audit was:

| Gamma | Spearman | Huber | Max ratio | Eligible |
|---:|---:|---:|---:|---|
| 0.25 | `0.151626` | `0.234784` | `0.609764` | no, Spearman |
| 0.50 | `0.415972` | `0.258809` | `1.000000119` | yes |
| 0.75 | `0.611598` | `0.150954` | `1.000000119` | yes |
| 1.00 | `0.614717` | `0.150141` | `1.000000119` | selected |

The trained program and decoder SHA256 values are respectively
`ccc3aa1027b8945ef4ac9ff8a1198213fac36a22ada1e3768f58109f7f07fdec`
and `5112f86279945ac82eb2aa57d11545540d1d055976531b0d4e9bf959eb43c47a`.

## Teacher-Forced Evaluation

The A-validation final correct/zero Huber values were `0.150141/0.256587`, a
`41.4853%` reduction, with Spearman `0.614717`.

| Cell | Spearman | Correct Huber | Zero | Reduction | Static | State shuffle | Transition shuffle | Swap | Max ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B | `0.551575` | `0.175668` | `0.249890` | `29.7020%` | `0.227259` | `0.218843` | `0.213188` | `0.210286` | `1.000000119` |
| C | `0.447270` | `0.172539` | `0.234543` | `26.4360%` | `0.223157` | `0.239644` | `0.201482` | `0.186412` | `1.000000119` |
| D | `0.504023` | `0.167943` | `0.240019` | `30.0291%` | `0.216362` | `0.217631` | `0.169113` | `0.175145` | `1.000000119` |
| E | `0.573837` | `0.168699` | `0.243227` | `30.6415%` | `0.222597` | `0.222406` | `0.198665` | `0.201015` | `1.000000119` |

All preregistered A/B/E teacher-forced checks pass. C and D are positive
diagnostics. The tiny ratio excess is floating-point tolerance around the
projected ratio-one boundary and passed the locked validator.

## One-Step Audit

The conditional phase ran all `180/180` H1-H4 generations and same-world
AppWorld 0.1.0 executions, with zero infrastructure exceptions. C0 and F3 are
immutable EXP-025C-R comparators.

Primary 32-state non-documentation Tier-3/4 subset:

| Condition | Exact API | Action signature | Execution | Semantic successor | Normalized observation |
|---|---:|---:|---:|---:|---:|
| C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |
| F3 raw selected transition | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.770394` |
| H1 full factorized | `0.75000` | `0.28125` | `0.93750` | `0.53125` | `0.543853` |
| H2 static | `0.78125` | `0.31250` | `0.90625` | `0.50000` | `0.518982` |
| H3 shuffled transition | `0.75000` | `0.31250` | `0.90625` | `0.53125` | `0.565910` |
| H4 zero | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |

All 45 states:

| Condition | Exact API | Action signature | Execution | Semantic successor |
|---|---:|---:|---:|---:|
| C0 | `0.73333` | `0.35556` | `0.93333` | `0.42222` |
| F3 | `0.80000` | `0.64444` | `1.00000` | `0.66667` |
| H1 | `0.73333` | `0.35556` | `0.95556` | `0.48889` |
| H2 | `0.75556` | `0.37778` | `0.93333` | `0.46667` |
| H3 | `0.75556` | `0.40000` | `0.88889` | `0.51111` |
| H4 | `0.73333` | `0.35556` | `0.93333` | `0.42222` |

On the primary subset, H1 minus C0 is `-0.03125` action signature,
`+0.09375` semantic successor, and `0.0` execution. The single-seed
task-bootstrap 95% intervals are `[-0.17241,0.10000]`,
`[0.00000,0.21222]`, and `[-0.08333,0.10000]`, respectively. Normalized
observation similarity improves by `+0.102144`, CI `[0.019701,0.215641]`.

H1 loses to F3 by `-0.40625` action signature, CI
`[-0.59375,-0.24240]`, and by `-0.25000` successor, CI
`[-0.38710,-0.11765]`. H1 minus H2 is only `+0.03125` successor and
`-0.03125` action signature. H1 does not beat H3: successor difference is
`0.0` and action-signature difference is `-0.03125`.

Oracle gain retention is `-0.08333` for action signature and `0.27273` for
semantic successor, below the required 40% on both. Positive relative behavior
appears on only `3/9` tasks, below the required `5/9`.

## Runtime And Artifacts

- Measured training/evaluation allocation: `7.89735` H100 hours
- Successful training through teacher-forced finalization: `7.43536` H100 hours
- One-step Qwen generation: `0.10738` H100 hours
- One-step phase wall time: `0.40720` hours
- End-to-end wall span through corrected finalization: `8.77893` hours
- Final artifact size: `632,139,760` bytes
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_direct_extend_7dg2_20260821_001`
- Persistent log: `/lambda/nfs/rcmf-persist/runs/logs/exp025dg2.log`

## Scientific Interpretation

VERIFIED:

- Exact continuation repairs the prior under-convergence concern: r16 passes
  the teacher-forced A/B/E gate at u48 with no representation, rank, objective,
  selector, or decoder-initialization change.
- The compiled H1 intervention does not meet the one-step retention gate and
  does not beat its shuffled-transition control.
- The raw selected transition F3 remains substantially stronger than H1.

INFERENCE:

- Scalar teacher-forced utility calibration is not sufficient for preserving
  the structured next-action behavior induced by raw episodic content.
- The remaining bottleneck is behavioral retention by the compiled r16
  intervention, not convergence of its scalar utility objective.

UNVERIFIED:

- No full-bank compiled program, p(s,m_transition), program compiler, Stage C2,
  end-to-end RCMF, full ReAct evaluation, or V4-tag claim exists.

## Decision

Reached `calibrated_factorized_program_not_behaviorally_retained`.

Do not resume u64, start r64, or begin full-bank integration automatically.
For the submission critical path, preserve the clean selector plus raw-
transition causal result as the validated positive result and treat r16
compilation as a bounded negative result. Any further program work requires a
separately reviewed behavioral-retention objective or factorization milestone.

