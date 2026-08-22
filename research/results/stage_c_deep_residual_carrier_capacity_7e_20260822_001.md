# EXP-026B Final Scientific Report

## Outcome

- Run UUID: `deep_residual_carrier_capacity_7e_20260822_001`
- Global seed: `25101`
- Starting commit: `496b8f868a36b2389b1965878dee1b365c2e4176`
- Experiment-source commit:
  `15dcf000225dbdd631eb6dcf6b89c88a8b1ac8d6`
- Archive branch: `archive/v4-input-embedding-channel-failed`
- Working branch: `research/v4-deep-residual-carrier`
- Frozen selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Clean replay lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Primary states/tasks: `32 / 9`
- Reached branch: `deep_residual_carrier_capacity_validated`

The free pair-specific deep-residual carrier passes the preregistered one-step
capacity gate. On the same 32 states, it exactly matches the selected raw
transition on action signature and semantic successor, retains `100%` of both
raw gains over bare Qwen, and materially beats the cyclic shuffled-residual
control. This validates carrier capacity only. No observation-excluded
compiler, PairMLP, factorized program, selector, Qwen parameter, or full bank
was trained.

## Carrier Contract

The actual Qwen3-8B configuration has 36 transformer blocks and hidden size
4,096. The deterministic preregistered formula selected zero-based layers
`[7, 14, 21, 28]`. Each free pair intervention has shape
`[4 layers, 4 prompt positions, 4096 hidden]`, or `65,536` scalars; all 32
pairs contain `2,097,152` free diagnostic scalars.

Injection occurs at each selected decoder block input, before input layer
normalization and attention, and only at the locked last four user-token
positions during prompt prefill. Generated tokens are not directly modified;
the modified prompt K/V state persists naturally through decoding. Prompt
length, tokens, position IDs, the three demonstrations, K=4, and Qwen weights
remain unchanged.

## Implementation Validation

All four deterministic short/median/long/near-limit representatives passed:

- target-logit maximum absolute difference at zero delta: `0.0 / 0.0 / 0.0 / 0.0`;
- target-NLL absolute difference at zero delta: `0.0 / 0.0 / 0.0 / 0.0`;
- exact deterministic generation and extracted code: `4/4`;
- prompt token count, position IDs, and KV-cache length: `4/4`;
- directly modified layers exactly `[7,14,21,28]`: `4/4`;
- directly modified positions exactly the locked four user tokens: `4/4`;
- generated-token hook calls skipped: `4/4`;
- nonzero gradient at every active layer: `16/16` layer-state checks;
- Qwen parameter gradients: `0`.

The focused deep-residual/direct-channel suite passes `20/20` locally and on
Lambda. Python compilation passes. Ruff is unavailable in the verified Lambda
environment and is recorded as an environment limitation.

## Attempts And Recovery

The append-only ledger contains 10 unique closed attempts and 20 start/end
events. Three validation attempts failed before any scientific optimization:

| Attempt | Stopped reason | Scientific work repeated |
|---|---|---|
| `exp026b-validate-001` | GPU/CPU mismatch in ratio-report serialization | none |
| `exp026b-validate-002` | audit helper rejected the legal unbatched generation tensor | none |
| `exp026b-validate-003` | residual hooks were removed before activation-checkpoint recomputation | none |

The fixes only corrected audit tensor placement/shape and kept residual hooks
installed through the corresponding backward recomputation. The aggregate
training objective, samples, layers, norm budget, seed, and Qwen model did not
change. An earlier bootstrap import check also failed before the attempt ledger
or any run artifact was created; it was covered by a regression test. One SSH
watchdog connection reset during one-step execution, while tmux, heartbeat,
and atomic rows continued. No run, training update, or condition was
duplicated.

- Attempt ledger SHA256:
  `58175edaf54beb94cf4e9a9b3705b2068810e64626590ab10a95a5facb3a3549`
- Preflight SHA256:
  `b0c0bfba09f32f86bb19b97937102725dfd2ea31dc3030b9306cb06be3c24205`
- Implementation validation SHA256:
  `e796fe5ac154c43bd88fab814bf5f32d952b4ae97a811a765ff1227f46b7460b`
- Training summary SHA256:
  `90fe313fa3a20520b510ba5d2a54414407f9e9605f1ed73996286c618728316c`
- One-step analysis SHA256:
  `82ab28adff760316113416f3d422e422b4860656a4db50ba86eb5756308e2efc`

## Runtime Preflight

- Backward calls at u8/u16 bounds: `256 / 512`
- New Qwen generations and AppWorld executions: `64 / 64`
- Expected maximum H100 time: `1.1684` hours
- Best/conservative maximum estimates: `0.8430 / 2.0767` H100 hours
- Automatic six-hour launch gate: passed
- Projected work changed after preflight: no

## Teacher-Forced Curves

Every pair received exactly 16 updates. The u4-to-u8 continuation test passed:
policy KL improved `71.44%`, teacher CE improved `68.88%`, and no ratio limit
was approached. u16 was the preregistered maximum.

| Updates | Policy KL | Teacher CE | Teacher top-1 | Ground-truth NLL | Max layer ratio | Max global ratio |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | `0.337537` | `0.348049` | `0.947182` | `0.400728` | `0.000000` | `0.000000` |
| 4 | `0.077045` | `0.078644` | `0.981568` | `0.183323` | `0.212216` | `0.057239` |
| 8 | `0.022001` | `0.024472` | `0.996354` | `0.145017` | `0.327940` | `0.092664` |
| 16 | `0.003246` | `0.006278` | `0.999178` | `0.075623` | `0.484395` | `0.129083` |

Final checkpoint SHA256:
`f22fa287122db676e45f95797b86a779de67394a72adec11cc58d2f23bd0ca7b`.
These teacher-forced results are a capacity diagnostic, not the scientific
decision.

## One-Step Audit

The condition manifest contains 128 logical rows: 32 reused C0 bare, 32 reused
F3 raw, 32 new correct residual R, and 32 new cyclic shuffled residual S. The
permutation has zero fixed points. All `64/64` new conditions generated and
executed once in the same live AppWorld 0.1.0 world/namespace, with zero
infrastructure or execution exceptions.

| Condition | Exact API | Action signature | Execution | Semantic successor | Normalized observation |
|---|---:|---:|---:|---:|---:|
| C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |
| F3 selected raw | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.770394` |
| R correct deep residual | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.772289` |
| S shuffled deep residual | `0.78125` | `0.37500` | `0.93750` | `0.50000` | `0.514259` |

Task-grouped bootstrap uses the single global seed and 2,000 samples:

| Contrast | Action-signature delta (95% CI) | Successor delta (95% CI) |
|---|---:|---:|
| R - C0 | `+0.37500 [0.20690, 0.54545]` | `+0.34375 [0.21423, 0.45946]` |
| R - F3 | `0.00000 [0.00000, 0.00000]` | `0.00000 [0.00000, 0.00000]` |
| R - S | `+0.31250 [0.18750, 0.43344]` | `+0.28125 [0.16667, 0.40625]` |

Per-task R-minus-C0 action-signature/successor deltas are:

| Task | States | Signature | Successor | Positive |
|---|---:|---:|---:|---|
| `229360a_1` | 4 | `+0.25` | `+0.50` | yes |
| `2a163ab_1` | 4 | `0.00` | `+0.25` | yes |
| `771d8fc_3` | 4 | `+0.75` | `+0.50` | yes |
| `7d7fbf6_2` | 3 | `0.00` | `0.00` | no |
| `82e2fac_3` | 3 | `+0.3333` | `+0.3333` | yes |
| `aa8502b_3` | 5 | `+0.40` | `+0.40` | yes |
| `b0a8eae_1` | 3 | `+0.6667` | `0.00` | yes |
| `b0a8eae_2` | 3 | `+0.3333` | `+0.3333` | yes |
| `e85d92a_1` | 3 | `+0.6667` | `+0.6667` | yes |

## Decisive Gate

- action-signature raw-gain retention: `1.0000`;
- semantic-successor raw-gain retention: `1.0000`;
- R-minus-S action-signature/successor: `+0.3125 / +0.28125`;
- R execution minus C0: `+0.0625`;
- positive tasks: `8/9`;
- 70% primary retention: passed;
- 50% companion retention: passed;
- shuffled-control contrast: passed;
- execution degradation: passed;
- six-task gate: passed.

Decision: `deep_residual_carrier_capacity_validated`.

## Runtime And Artifacts

- Full wall span: `1.1460` hours
- Total H100-resident process time, including three failed validation checks:
  approximately `0.7089` H100 hours
- Successful validation/training/one-step process time: `0.6628` H100 hours
- Formal Qwen generation compute: `0.02709` H100 hours
- Training elapsed: `1,683.32` seconds
- One-step wall elapsed: `511.58` seconds
- Artifact size: `135,535,950` bytes
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/deep_residual_carrier_capacity_7e_20260822_001`
- Ledger: `attempts.jsonl`
- Preflight: `preflight.json`, `runtime_preflight.md`
- Validation: `implementation_validation.json`
- Training: `training/summary.json`, `training/checkpoint_u*.pt`
- Teacher forced: `teacher_forced/summary.json`
- Behavior: `one_step/condition_manifest.json`, `one_step/preflight.json`,
  `one_step/generation_summary.json`, `one_step/analysis.json`

At final inspection, no EXP-026B process or tmux session remained. The H100
reported `0 MiB` allocated and `0%` utilization; only older idle tmux sessions
remained. The machine and persistent artifacts are safe to terminate.

## Interpretation

VERIFIED:

- A free pair-specific fixed-size residual intervention at four deterministic
  layers and four prompt positions reproduces the selected raw transition's
  one-step action-signature and semantic-successor behavior on this audit.
- The correct residual materially beats a no-fixed-point shuffled residual,
  and the decisive bootstrap intervals exclude zero.
- The shallow input-embedding failure was carrier-specific; it was not a
  general fixed-size-neural-intervention capacity failure.
- No compiler/program, full bank, selector, Qwen parameter, Stage C2,
  end-to-end RCMF, full AppWorld evaluation, or V4 tag was created.

INFERENCE:

- The deep residual carrier is a viable target space for one narrowly reviewed
  amortization experiment.
- Matching the raw intervention with a deployable compiler remains the central
  unresolved step; this oracle result does not itself validate deployment-time
  memory compilation.

UNVERIFIED:

- Whether observation-excluded state/transition representations can compile a
  256D program into this carrier; full-bank behavior; multi-step trajectories;
  end-to-end AppWorld performance.

Recommended next milestone: a separately reviewed, single-seed
observation-excluded PairMLP mapping to a 256D latent and shared decoder into
the fixed `[layers 7,14,21,28] x [last four user positions]` residual carrier.
Do not broaden layers, positions, carrier families, or architecture search.
p(s,m_transition), a production full bank, Stage C2, end-to-end RCMF, full
AppWorld evaluation, and V4 tagging remain blocked pending that compiler gate.
