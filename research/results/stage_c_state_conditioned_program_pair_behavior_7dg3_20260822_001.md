# EXP-025D-G3 Final Scientific Report

## Outcome

- Run UUID: `state_conditioned_program_pair_behavior_7dg3_20260822_001`
- Global seed: `25101`
- Starting commit: `a7c2fe004ddc1b394c21c37914682a7d7742046a`
- Pair-behavior source commit:
  `fcd4833622022d288dc8703352f465f16e5d1c77`
- Policy-distillation source commit:
  `6d75c3ff1f3a8b5624f72524771c34d99e7a7e07`
- Branch: `research/v4-direct-behavior-program`
- Parent Direct PairMLP u8 checkpoint SHA256:
  `80506a5d9b1c3031b5468fb59c0b6d9e01d7d50ddc1fee49115a88eb8b8b429d`
- Parent model / private decoder SHA256:
  `206985ff862506fa14168284e10a7463c981b87fe4da311dc880b6d580be28a7` /
  `4d4b84970d1c6c9818790cada49e2554fc1c3b58d298175deac4ca70c837a6cd`
- Frozen selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`
- Clean replay lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Direct PairMLP one-step gate: failed
- Conditional behavioral-policy PairMLP one-step gate: failed
- Reached preregistered branch:
  `teacher_forced_objective_not_behaviorally_retained`
- Conditional pilot result: `behavioral_policy_distillation_pairmlp_failed`
- r64 launched: no

The existing Direct PairMLP transfers a small amount of one-step behavior but
retains only `36.36%` of the raw transition's semantic-successor gain. The
automatically authorized token-policy distillation pilot does not repair the
problem: it retains `27.27%`, has no action-signature gain, and is insensitive
to state and transition shuffles on the decisive behavior metrics.

## Integrity And Attempts

The immutable Direct PairMLP, private decoder, selector, representations,
Qwen3-8B, prompt, three demonstrations, observation-excluded boundary,
`last_user_k`, and `K=4` were validated before generation. Neither audit
contains raw transition text in the student prompt.

The append-only ledger contains 10 unique, closed attempts and 20 start/end
events. Every attempt exited normally:

| Attempt | Phase |
|---|---|
| `exp025dg3-preflight-001` | Direct PairMLP audit preflight |
| `exp025dg3-formal-001` | 135 Direct PairMLP generations/executions |
| `exp025dg3-analyze-001` | Direct PairMLP analysis and gate |
| `exp025dg3-policy-preflight-001` | Conditional policy-pilot preflight |
| `exp025dg3-policy-teacher-001` | Raw-memory policy teacher cache |
| `exp025dg3-policy-train-001` | Eight updates on each of 128 pairs |
| `exp025dg3-policy-evaluate-001` | A/B/C/D/E teacher-forced evaluation |
| `exp025dg3-policy-one-step-preflight-001` | 135-condition behavior preflight |
| `exp025dg3-policy-one-step-001` | 135 policy PairMLP generations/executions |
| `exp025dg3-policy-analyze-001` | Conditional policy-pilot analysis |

A shell-side monitoring connection reset while the policy training process was
running. The Lambda process, heartbeat, and atomic checkpoint continued; no
attempt was restarted and no duplicate output key was created.

Final ledger SHA256:
`217d35ec5f4707bceab0207e967802be23c4b4476cecb1e93e1c5dea31e5f071`.
The direct and policy analysis SHA256 values are respectively
`9fc8ce6d64a6b8eb6d360555a94a9e95b409ad504f2775b92e7c05a7cdc30bec`
and `85383266b598fd7306d44c4dec5ce1c5aaf8d835d634916b58dd7b4c276ea3c9`.

The final targeted local test run passed `9/9` G3 tests. The broader relevant
source suite had already passed `39/39` locally and on Lambda before the
formal run; Python compilation and Ruff checks also passed.

## Existing Direct PairMLP One-Step Audit

The immutable 45-state manifest produced 45 each of P1 correct PairMLP, P2
shuffled-transition PairMLP, and P3 shuffled-state PairMLP. All `135/135`
conditions generated and executed once in a fresh AppWorld 0.1.0 same-world
worker, with zero infrastructure and execution exceptions.

Primary 32-state subset:

| Condition | Exact API | Action signature | Execution | Semantic successor | Normalized observation |
|---|---:|---:|---:|---:|---:|
| C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |
| F3 selected raw transition | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.770394` |
| P1 correct PairMLP | `0.81250` | `0.34375` | `0.93750` | `0.56250` | `0.559686` |
| P2 shuffled transition | `0.81250` | `0.31250` | `0.93750` | `0.53125` | `0.525736` |
| P3 shuffled state | `0.81250` | `0.31250` | `0.93750` | `0.53125` | `0.530392` |

All 45 states:

| Condition | Exact API | Action signature | Execution | Semantic successor |
|---|---:|---:|---:|---:|
| C0 | `0.73333` | `0.35556` | `0.93333` | `0.42222` |
| F3 | `0.80000` | `0.64444` | `1.00000` | `0.66667` |
| P1 | `0.77778` | `0.40000` | `0.95556` | `0.51111` |
| P2 | `0.77778` | `0.37778` | `0.95556` | `0.48889` |
| P3 | `0.77778` | `0.37778` | `0.95556` | `0.48889` |

Primary paired task-bootstrap contrasts, all using seed `25101` and 2,000
samples:

| Contrast | Action-signature delta (95% CI) | Successor delta (95% CI) |
|---|---:|---:|
| P1 - C0 | `+0.03125 [-0.10345, 0.16129]` | `+0.12500 [0.03571, 0.19444]` |
| P1 - F3 | `-0.34375 [-0.53125, -0.14706]` | `-0.21875 [-0.34483, -0.10336]` |
| P1 - P2 | `+0.03125 [0.00000, 0.10345]` | `+0.03125 [0.00000, 0.09383]` |
| P1 - P3 | `+0.03125 [0.00000, 0.10345]` | `+0.03125 [0.00000, 0.09383]` |

P1 retained `8.33%` of the F3 action-signature gain and `36.36%` of its
semantic-successor gain. It was positive relative to C0 on `5/9` tasks and
preserved execution, but it missed the required 40% retention on both primary
metrics. The decisive PairMLP gate therefore failed.

## Conditional Behavioral-Policy Pilot

The failed direct-behavior gate automatically activated the preregistered
narrow policy pilot, not r64.

### Data And Teacher

- A training pairs / tasks: `128 / 29`
- Evaluation pairs: A-validation/B/C/D/E = `32/24/24/24/32`
- Evaluation logical rows: `136`
- Unique raw-memory policy-teacher pairs: `252`
- Reused/new policy-teacher rows: `0/252`
- Teacher-generated tokens: `14,485`
- Top-K teacher distribution: `K=64`
- Teacher responses hitting the fixed 512-token maximum: `1`
- Pair manifest SHA256:
  `29e58c801aed28f7ac521aef7a3882966b0cc3c692411ab5c06aedf99f589583`
- Teacher row-set SHA256:
  `5e854296a05f2edecb24f17d95529a188d10241cb5a9c20531f306ad27612478`

The student was warm-started from the immutable Direct PairMLP and private
decoder, then trained with a fresh optimizer for exactly eight updates per
pair (`1,024` total). Qwen and the selector remained frozen. At u1 to u8,
policy KL fell `0.210188 -> 0.170883`, teacher-token CE fell
`0.230097 -> 0.189642`, ground-truth CE fell `0.427246 -> 0.354188`, and
teacher-token top-1 accuracy rose `0.955421 -> 0.957582`. The maximum ratio was
`1.000000119`, within locked floating-point tolerance.

Final policy checkpoint / model / decoder SHA256:

- `48eae127ead0976349f6d598c533efb39663b145e2211a3072983f5f47bacbea`
- `a1e16484a959cc70dac82991ecf7cf4b4153d551d613f9bd759837774dcea37b`
- `fecf9ba4db728e7713f0870e88e48a1f6173635e4d74dea27f7b8468654a98ff`

### Teacher-Forced Policy Metrics

| Cell | Rows | Correct policy KL | Zero KL | Reduction vs zero | State-shuffle gap | Transition-shuffle gap | Teacher-token top-1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A-val | 32 | `0.164560` | `0.278308` | `+0.113748` | `+0.004591` | `+0.002573` | `0.953375` |
| B | 24 | `0.235326` | `0.327535` | `+0.092209` | `+0.004598` | `+0.012783` | `0.947027` |
| C | 24 | `0.152895` | `0.271225` | `+0.118330` | `+0.013015` | `+0.005027` | `0.954949` |
| D | 24 | `0.140006` | `0.218342` | `+0.078336` | `-0.009021` | `+0.001872` | `0.952102` |
| E | 32 | `0.273442` | `0.439958` | `+0.166516` | `+0.005869` | `-0.005387` | `0.943625` |

The policy student is substantially better than zero at teacher-forced token
matching, but the correct-pair advantage over shuffles is small and reverses
for D state shuffle and E transition shuffle. The bounded one-step audit was
still run as preregistered; no post-hoc teacher-forced gate was introduced.

### Policy PairMLP One-Step Result

The fixed 45-state manifest produced `135/135` new generations and same-world
executions, 45 each for policy P1/P2/P3. Same-world and namespace checks were
`135/135`; infrastructure and execution exception counts were zero.

Primary 32-state subset:

| Condition | Exact API | Action signature | Execution | Semantic successor | Normalized observation |
|---|---:|---:|---:|---:|---:|
| C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |
| F3 selected raw transition | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.770394` |
| Policy P1 correct | `0.81250` | `0.31250` | `0.96875` | `0.53125` | `0.532565` |
| Policy P2 shuffled transition | `0.78125` | `0.31250` | `0.96875` | `0.53125` | `0.533083` |
| Policy P3 shuffled state | `0.81250` | `0.31250` | `0.96875` | `0.53125` | `0.534896` |

Primary paired task-bootstrap contrasts:

| Contrast | Action-signature delta (95% CI) | Successor delta (95% CI) |
|---|---:|---:|
| Policy P1 - C0 | `0.00000 [-0.12903, 0.11429]` | `+0.09375 [0.03030, 0.16667]` |
| Policy P1 - F3 | `-0.37500 [-0.57143, -0.16667]` | `-0.25000 [-0.39394, -0.12500]` |
| Policy P1 - P2 | `0.00000 [0.00000, 0.00000]` | `0.00000 [0.00000, 0.00000]` |
| Policy P1 - P3 | `0.00000 [0.00000, 0.00000]` | `0.00000 [0.00000, 0.00000]` |

Policy P1 retained `0%` of raw action-signature gain and `27.27%` of raw
semantic-successor gain. It preserved execution but was positive on only
`3/9` tasks and did not beat either shuffle on the two decisive metrics. The
conditional policy pilot therefore failed, and no r64 or factorized policy
model was started.

## Runtime And Artifacts

- Active GPU-attempt time across both audits: `2.17684` H100 hours
- End-to-end wall span from first preflight through final analysis:
  `3.58825` hours
- Original Direct PairMLP one-step Qwen generation: `0.06955` H100 hours
- Policy PairMLP one-step Qwen generation: `0.06843` H100 hours
- Total one-step generations/executions: `270/270`
- Total final artifact bytes: `554,678,145`
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_pair_behavior_7dg3_20260822_001`
- Ledger: `attempts.jsonl`
- Direct audit: `one_step/condition_manifest.json`,
  `one_step/generation_summary.json`, `one_step/analysis.json`
- Policy manifest/teacher: `policy_distillation/pair_manifest.json`,
  `policy_distillation/teacher_cache/summary.json`
- Policy training/evaluation:
  `policy_distillation/training/training_summary.json`,
  `policy_distillation/evaluation/summary.json`
- Policy behavior: `policy_distillation/one_step/condition_manifest.json`,
  `policy_distillation/one_step/generation_summary.json`,
  `policy_distillation/one_step/analysis.json`

## Scientific Interpretation

VERIFIED:

- The original Direct PairMLP passes scalar teacher-forced utility gates but
  fails the preregistered one-step oracle-retention threshold.
- Token-policy distillation improves teacher-forced imitation over zero in
  every cell, yet it also fails one-step retention and pair-shuffle controls.
- Frozen selected raw transitions remain substantially more effective than
  either compiled PairMLP intervention.
- No r64, factorized policy model, full bank, Stage C2, end-to-end RCMF, or V4
  tag was started.

INFERENCE:

- The failure is not limited to scalar target-NLL utility. Matching the raw-
  memory teacher along its deterministic response sequence still does not
  transfer enough pair-specific behavior through the current compiled
  PairMLP intervention.
- Before the submission deadline, the defensible positive result is the clean
  signature-balanced selector plus raw-transition causal effect, not a
  behaviorally validated compiled program.

UNVERIFIED:

- Whether a different injection mechanism, recurrent/compiler architecture,
  or end-to-end policy objective can preserve raw episodic behavior.
- Full-trajectory and end-to-end AppWorld performance.

## Decision

Reached `teacher_forced_objective_not_behaviorally_retained`; the conditional
policy pilot additionally records `behavioral_policy_distillation_pairmlp_failed`.
Do not run r64, another representation study, full-bank compilation,
`p(s,m_transition)`, injector training, Stage C2, end-to-end RCMF, or V4-tag
work from this run.

Recommended next step is a deadline scope lock: preserve the validated clean
selector/raw-transition result for the paper, record compiled-program
behavioral transfer as a bounded negative result, and separately review a
minimal selector-plus-raw-transition partial end-to-end evaluation only if it
is essential to the submission claim.
