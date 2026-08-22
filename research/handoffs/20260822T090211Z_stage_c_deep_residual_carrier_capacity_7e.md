# EXP-026B Structured Handoff

## Status

- Run: `deep_residual_carrier_capacity_7e_20260822_001`
- Branch: `research/v4-deep-residual-carrier`
- Starting SHA: `496b8f868a36b2389b1965878dee1b365c2e4176`
- Experiment-source SHA: `15dcf000225dbdd631eb6dcf6b89c88a8b1ac8d6`
- Global seed: `25101`
- Decision: `deep_residual_carrier_capacity_validated`
- Carrier-capacity gate: passed
- Compiler/program trained: no
- Safe scientific stop: yes

## What Ran

The audit reused the exact 32 primary clean EXP-026A state/F3 pairs and frozen
raw-memory policy teachers. One free `4 x 4 x 4096` DeltaH tensor was optimized
per pair at deterministic decoder blocks `[7,14,21,28]` and the locked last
four user-token positions. Qwen, selector, prompt, demonstrations, positions,
observation boundary, and AppWorld 0.1.0 live bridge remained frozen. The
final deltas and a no-fixed-point cyclic shuffle were generated and executed
once per state.

## Key Results

- Free parameters per pair/all pairs: `65,536 / 2,097,152`
- Zero-equivalence representatives: `4/4`
- Exact zero logit/NLL differences: `0.0 / 0.0`
- Gradient-positive layer checks: `16/16`
- Final u16 policy KL / teacher CE: `0.003246 / 0.006278`
- Final maximum layer/global ratio: `0.484395 / 0.129083`
- New R/S generations and executions: `64/64`
- Same-world and same-namespace: `64/64`
- Infrastructure/execution exceptions: `0/0`
- C0 signature/successor: `0.31250 / 0.43750`
- F3 signature/successor: `0.68750 / 0.78125`
- R signature/successor: `0.68750 / 0.78125`
- S signature/successor: `0.37500 / 0.50000`
- R raw-gain retention: `1.0000 / 1.0000`
- R-minus-S: `+0.3125 / +0.28125`
- Positive tasks: `8/9`

## Recovery And Runtime

- Append-only ledger: `10` attempts / `20` closed events
- Three failed validation attempts preceded training and changed no scientific
  parameter or output.
- Ledger SHA:
  `58175edaf54beb94cf4e9a9b3705b2068810e64626590ab10a95a5facb3a3549`
- Final checkpoint SHA:
  `f22fa287122db676e45f95797b86a779de67394a72adec11cc58d2f23bd0ca7b`
- One-step analysis SHA:
  `82ab28adff760316113416f3d422e422b4860656a4db50ba86eb5756308e2efc`
- Wall span: `1.1460` hours
- H100-resident process time including validation recovery: `0.7089` hours
- Artifact size: `135,535,950` bytes

## Immutable Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/deep_residual_carrier_capacity_7e_20260822_001`
- Ledger: `attempts.jsonl`
- Preflight: `preflight.json`, `runtime_preflight.md`
- Implementation gate: `implementation_validation.json`
- Training: `training/summary.json`, `training/checkpoint_u16.pt`
- Teacher forced: `teacher_forced/summary.json`
- One step: `one_step/condition_manifest.json`,
  `one_step/generation_summary.json`, `one_step/analysis.json`
- GitHub report:
  `research/results/stage_c_deep_residual_carrier_capacity_7e_20260822_001.md`

## Interpretation And Next Action

VERIFIED: the deep fixed-size carrier can preserve the raw selected
transition's one-step effect under a free pair-specific oracle and beats the
shuffled-residual control.

INFERENCE: the EXP-026A failure was specific to the shallow embedding carrier,
and one deep-residual compiler test is scientifically justified.

UNVERIFIED: amortized compilation, full-bank behavior, multi-step behavior,
and end-to-end RCMF.

Prepare, but do not start without review, one single-seed observation-excluded
PairMLP from existing state/transition representations to a 256D latent and a
shared decoder into the fixed four-layer/four-position carrier. Do not test
another carrier or broaden the layer/position search. p(s,m_transition), the
full bank, Stage C2, end-to-end evaluation, and V4 tagging remain blocked.
