# EXP-026A Structured Handoff

## Status

- Run: `direct_injection_channel_capacity_7dh_20260822_001`
- Branch: `research/v4-direct-behavior-program`
- Starting SHA: `93745a1e92c650af372970b7f2c242047d3675ff`
- Experiment-source SHA: `f3640575a494189d32dc2b443bcd6e4bf99d6e69`
- Global seed: `25101`
- Frozen selector SHA:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Decision: `input_embedding_channel_behavioral_capacity_failed`
- Passing K values: none
- Conditional widened PairMLP: not triggered
- Safe scientific stop: yes

## What Ran

The audit reused the exact 32 primary EXP-025C-R states and their frozen F3
transitions. For each feasible state and K in `4/8/16`, it optimized one free
`K x 4096` DeltaE tensor directly through frozen Qwen against the cached raw-
memory policy teacher. No decoder, latent, program, selector training, raw
transition text in the student prompt, or full memory bank was used. Frozen
DeltaE tensors were then tested in fresh AppWorld 0.1.0 worlds against a fixed
cyclic shuffled-oracle control.

## Key Results

- Feasibility K4/K8/K16: `32/32`, `32/32`, `28/32`
- Teacher rows reused/new: `17/15`
- Free parameters per pair: `16,384 / 32,768 / 65,536`
- Final policy KL K4/K8/K16: `0.297942 / 0.355502 / 0.540739`
- Zero policy KL: `0.337537 / 0.337537 / 0.384371`
- K4 O signature/successor/execution: `0.50000 / 0.65625 / 0.90625`
- K4 S signature/successor: `0.40625 / 0.46875`
- K4 retention signature/successor: `0.5000 / 0.6364`
- K4 positive tasks: `6/9`
- K8 retention signature/successor: `0.2500 / 0.5455`; positive tasks `5/9`
- K16 retention signature/successor: `0.0000 / 0.1000`; positive tasks `2/9`
- Passing channel gates: none

K4 has a measurable effect over bare and shuffled controls, but it misses the
locked 70%/50% raw-gain retention requirement. Larger K does not rescue the
channel, so no conditional PairMLP or factorized capacity run is authorized.

## Runtime And Recovery

- Append-only attempts: `11` closed attempts / `22` events
- Ledger SHA:
  `e4529c7d81123b528d0cfd1d42298f49d86cef5daf83b5b7a406488c860b7bae`
- Formal generations/executions: `184/184`
- Same-world/namespace checks: `184/184`
- Infrastructure/execution exceptions: `0/0`
- Wall span: `1.84862` hours
- Successful GPU process time: approximately `1.5297` H100 hours
- Artifact size: `135,221,819` bytes

Four stopped attempts are preserved: selector-hash typo rejection, bounded
parent-cache incompleteness, replay-config unwrapping, and CPU bootstrap-key
analysis. Fixes were covered by tests, changed no scientific input or output,
and caused no duplicate training or generation.

## Immutable Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/direct_injection_channel_capacity_7dh_20260822_001`
- Ledger: `attempts.jsonl`
- Preflight: `preflight.json`, `runtime_preflight.md`
- Teacher: `teacher_cache/summary.json`
- Training: `training/summary.json`, `training/K*/checkpoint_u*.pt`
- Teacher forced: `teacher_forced/summary.json`
- One step: `one_step/condition_manifest.json`,
  `one_step/generation_summary.json`, `one_step/analysis.json`
- GitHub report:
  `research/results/stage_c_direct_injection_channel_capacity_7dh_20260822_001.md`

## Interpretation And Next Action

VERIFIED: no free direct K4/K8/K16 intervention passes the behavioral channel
gate, despite eliminating decoder, latent, and amortization bottlenecks.

INFERENCE: the current additive last-user embedding channel cannot carry enough
of the selected raw transition's episodic behavioral effect for the submission
route.

UNVERIFIED: alternative intervention sites, full trajectories, and a working
compiled transition field.

Stop r16/r64/PairMLP/program and full-bank work for the submission. Prepare the
paper around the clean corpus, validated signature-balanced selector, and raw-
transition causal result; report compilation/channel transfer as a bounded
negative. A partial selector-plus-raw-transition end-to-end audit, if needed,
must be separately specified and reviewed.
