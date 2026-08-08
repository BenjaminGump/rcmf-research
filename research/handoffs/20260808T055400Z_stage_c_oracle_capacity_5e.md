# Handoff: Milestone 5E / EXP-015 Oracle Capacity Diagnostic

Date: 2026-08-08

Branch: `workflow/research-loop`

Source commit: `c786a9735add6de640869f497013014a937b4c0a`

Final record commit: pending at handoff creation time.

## Objective

Diagnose whether the Stage-C additive-token behavioral channel can reproduce
raw-memory teacher effects before returning to memory-content compiler work.

## Scope Status

VERIFIED:

- Qwen3-8B stayed frozen.
- The signed selector, selector scores, selector gate, empirical `mu_i`, and
  full-bank aggregation were not used.
- The content program compiler was not retrained as the primary experiment.
- AppWorld generation/evaluation, Stage C2, and end-to-end RCMF training were
  not run.

## Inputs

- Stage-5D pair cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`
- Stage-5D artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001`

## Outputs

- Stage-5E artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001`
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/report.md`
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c_oracle_capacity_5e_20260808_001.log`

## Validation

- Pair cache validation: passed, `1,728 / 1,728` pairs.
- Target-token utility identity: passed, maximum absolute error
  `1.001358e-06`.
- Diagnostic subset: `192` validation pairs, balanced as
  positive/neutral/negative/random `48/48/48/48`, covering all `36` effective
  train memories.

## Key Results

- Best direct DeltaE K=4 run:
  Spearman `0.641904`, sign agreement `0.776978`, target-token delta
  correlation `0.369083`, target-token delta Huber `0.573381`, target NLL
  `0.748773`, sparse KL `0.261206`, perturbation ratio `0.488439`.
- Direct-channel gate failed.
- K=8 direct DeltaE failed to improve: Spearman `0.608854`, sign agreement
  `0.784173`, target-token delta correlation `0.219402`.
- Objective ablation:
  old sparse behavioral-delta Huber Spearman `-0.092488`; target-token delta
  Huber Spearman `0.636335`.
- Frozen-injector pair-z inversion failed: Spearman `-0.069538`, sign
  agreement `0.467626`, target-token delta Huber `0.614797`.
- Joint validation pair-z upper bound improved Huber to `0.493115` but used
  perturbation ratio `2.370567` and did not pass.
- Free memory-z worsened NLL/KL/delta error versus zero control, despite weak
  positive Spearman `0.194337`.

## Decision

Reached branch: `direct_delta_fails`.

Identified bottleneck:
`additive_token_injection_location_bandwidth_or_behavioral_target`.

Stage C2 remains blocked.

## Important Interpretation Note

The script boolean `fixed_memory_latent_gate_passed=true` is too permissive.
It did not include control-relative NLL/KL/delta-error requirements. The
research interpretation is that free per-memory z did not pass.

## Next Recommended Milestone

Run EXP-016: injection-site and objective capacity repair.

Start with target-token delta as the primary utility-aligned objective. Since
K=8 did not repair direct DeltaE, prioritize injection site and decoder
mechanics, such as later-layer residual insertion or direct hidden/logit oracle
diagnostics. Do not return to memory-content compiler work until an injection
site passes the oracle capacity gate.

## Safe-To-Terminate

Yes. The `stage5e_exp015` tmux session ended, there is no tmux server, and GPU
memory/utilization was `0 MiB / 0%` after completion.
