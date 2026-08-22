# EXP-025D-G3 Structured Handoff

## Status

- Run: `state_conditioned_program_pair_behavior_7dg3_20260822_001`
- Branch: `research/v4-direct-behavior-program`
- Starting SHA: `a7c2fe004ddc1b394c21c37914682a7d7742046a`
- Direct-audit source SHA: `fcd4833622022d288dc8703352f465f16e5d1c77`
- Policy-pilot source SHA: `6d75c3ff1f3a8b5624f72524771c34d99e7a7e07`
- Seed: `25101`
- Final branch: `teacher_forced_objective_not_behaviorally_retained`
- Conditional pilot result: `behavioral_policy_distillation_pairmlp_failed`
- PairMLP compiled behavior validated: no
- r64 started: no
- Safe scientific stop: yes

## What Ran

The immutable Direct PairMLP u8 checkpoint and decoder produced 45 each of P1
correct, P2 shuffled-transition, and P3 shuffled-state conditions. Because its
one-step gate failed, the preregistered conditional policy pilot automatically
cached 252 deterministic raw-memory teacher responses/top-64 distributions,
trained the PairMLP on 128 A pairs for eight updates each, evaluated A/B/C/D/E,
and ran the same 135-condition P1/P2/P3 one-step audit. Qwen, selector, input
representations, prompt, demonstrations, K=4 injection, and AppWorld bridge
remained frozen.

## Key Results

Original Direct PairMLP, primary 32 states:

- C0 signature/successor/execution: `0.31250 / 0.43750 / 0.93750`
- F3 raw signature/successor/execution: `0.68750 / 0.78125 / 1.00000`
- P1 signature/successor/execution: `0.34375 / 0.56250 / 0.93750`
- P2 signature/successor: `0.31250 / 0.53125`
- P3 signature/successor: `0.31250 / 0.53125`
- P1 raw-gain retention: signature `0.08333`, successor `0.36364`
- Positive tasks: `5/9`
- Gate: failed on required 40% retention

Conditional policy PairMLP:

- Training pairs/tasks/updates: `128 / 29 / 1,024`
- Unique teacher rows: `252`, all newly cached
- Final checkpoint SHA:
  `48eae127ead0976349f6d598c533efb39663b145e2211a3072983f5f47bacbea`
- B/E correct policy KL: `0.235326 / 0.273442`
- B/E reduction versus zero: `+0.092209 / +0.166516`
- E correct-minus-transition-shuffle KL reduction: `-0.005387`
- Policy P1 signature/successor/execution: `0.31250 / 0.53125 / 0.96875`
- Policy P2 signature/successor: `0.31250 / 0.53125`
- Policy P3 signature/successor: `0.31250 / 0.53125`
- Policy raw-gain retention: signature `0.0`, successor `0.27273`
- Positive tasks: `3/9`
- Gate: failed on retention, shuffle sensitivity, and task count

The two one-step phases completed `270/270` generations and same-world
executions with zero infrastructure or execution exceptions.

## Attempts And Recovery

The append-only ledger has 10 unique, closed attempts: three for the frozen
PairMLP audit and seven for the conditional policy pilot. Every attempt exits
zero. A monitoring SSH connection reset during policy training, but the
Lambda-side process, heartbeat, and atomic checkpoint continued. No attempt
or condition was duplicated.

Ledger SHA256 is
`217d35ec5f4707bceab0207e967802be23c4b4476cecb1e93e1c5dea31e5f071`;
direct/policy analysis SHA256 values are
`9fc8ce6d64a6b8eb6d360555a94a9e95b409ad504f2775b92e7c05a7cdc30bec`
and `85383266b598fd7306d44c4dec5ce1c5aaf8d835d634916b58dd7b4c276ea3c9`.

## Immutable Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_pair_behavior_7dg3_20260822_001`
- Ledger: `attempts.jsonl`
- Direct one-step result: `one_step/analysis.json`
- Policy preflight: `policy_distillation/preflight.json`
- Policy teacher: `policy_distillation/teacher_cache/summary.json`
- Policy training: `policy_distillation/training/training_summary.json`
- Policy teacher-forced: `policy_distillation/evaluation/summary.json`
- Policy one-step: `policy_distillation/one_step/analysis.json`
- GitHub final report:
  `research/results/stage_c_state_conditioned_program_pair_behavior_7dg3_20260822_001.md`

## Interpretation And Next Action

VERIFIED: both the scalar-direct and token-policy PairMLP programs fail the
one-step behavioral-retention gate, while the selected raw transition remains
strong. The conditional pilot does not justify r64.

INFERENCE: the current compiled intervention does not preserve the raw
transition's action-level mechanism, even when trained against raw-memory
teacher token distributions rather than only scalar utility.

UNVERIFIED: full-trajectory behavior, alternative injection/compiler methods,
and end-to-end RCMF.

Freeze the compiled-program route for the submission critical path. Preserve
the clean selector plus raw-transition causal result as the positive claim and
conduct a separate paper-scope review before any partial end-to-end run.
