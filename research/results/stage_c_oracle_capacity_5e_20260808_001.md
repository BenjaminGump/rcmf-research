# Milestone 5E / EXP-015 Oracle Pair-Latent Injector Capacity Diagnostic

Date: 2026-08-08

Source commit: `c786a9735add6de640869f497013014a937b4c0a`

Final record commit: pending at report creation time.

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001`

Run log:
`/lambda/nfs/rcmf-persist/runs/logs/stage_c_oracle_capacity_5e_20260808_001.log`

## Scope

VERIFIED:

- This milestone diagnosed the Stage-C additive-token injection channel only.
- It reused the validated Stage-5D pair-response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`.
- Qwen3-8B remained frozen.
- The signed selector, selector scores, selector gate, empirical `mu_i`, and
  full-bank aggregation were not used.
- AppWorld generation/evaluation, Stage C2, and end-to-end RCMF training were
  not run.

## Cache And Subset

VERIFIED:

- Pair cache validation passed: `1,728 / 1,728` expected pairs, with
  `error_count=0`.
- Target-token teacher utility identity passed over all `1,728` pairs:
  `mean_t(log P_teacher(y_t) - log P_baseline(y_t)) == L0 - Lj_text` within
  tolerance. Maximum absolute error was `1.001358e-06`.
- Train pairs: `1,152`.
- State-held-out validation pairs: `576`.
- Diagnostic direct-oracle subset: `192` validation pairs.
- The subset was balanced across categories:
  positive `48`, neutral `48`, negative `48`, random `48`.
- The subset covered all `36` effective train memories.

## Direct DeltaE Oracle

The direct oracle bypassed the 128D latent and injector MLP and optimized a
free additive tensor directly at the selected prompt token embeddings.

Best K=4 run by target-delta criteria:
`target_delta_plus_sparse_kl_ratio_0.5`.

Metrics on the 192-pair subset:

| Metric | Value |
|---|---:|
| target NLL | 0.748773 |
| sparse teacher KL | 0.261206 |
| target-token delta Huber | 0.573381 |
| target-token delta MSE | 8.044045 |
| target-token delta correlation | 0.369083 |
| `u_text` vs `u_direct` Spearman | 0.641904 |
| `u_text` vs `u_direct` Pearson | 0.491805 |
| sign / direction agreement | 0.776978 |
| mean perturbation ratio | 0.488439 |

Decision against the direct-channel gate:

- Gate threshold was Spearman `>= 0.70`, sign agreement `>= 0.80`, and
  target-token delta correlation `>= 0.80` under perturbation ratio `<= 2.0`.
- The best K=4 run did not pass any of the three thresholded primary criteria.

Ratio sweep notes:

- Ratio `1.0` with target-delta plus sparse KL had Spearman `0.612793`, sign
  agreement `0.755396`, target-token delta correlation `0.424890`, and mean
  perturbation ratio `0.972898`.
- Ratio `2.0` had Spearman `0.421678`, sign agreement `0.705036`, and
  target-token delta correlation `0.280911`.
- Increasing the direct perturbation budget did not reliably improve
  utility-aligned behavior.

## K=8 Optional Width Check

K=8 was triggered because K=4 direct DeltaE failed.

Metrics for `target_delta_plus_sparse_kl_ratio2.0_k8`:

| Metric | Value |
|---|---:|
| target NLL | 0.785561 |
| sparse teacher KL | 0.319932 |
| target-token delta Huber | 0.617700 |
| target-token delta correlation | 0.219402 |
| `u_text` vs `u_direct` Spearman | 0.608854 |
| sign / direction agreement | 0.784173 |
| mean perturbation ratio | 1.883921 |

VERIFIED:

- K=8 did not improve over the best K=4 result.
- The failure is therefore not explained by K=4 bandwidth alone.

## Objective Ablation

All objective variants were evaluated on the same 192-pair subset.

| Objective | Spearman | Sign agreement | Target-delta corr | Target-delta Huber | Target NLL |
|---|---:|---:|---:|---:|---:|
| sparse behavioral-delta Huber | -0.092488 | 0.460432 | -0.068517 | 0.617408 | 0.765732 |
| target-token delta Huber | 0.636335 | 0.798561 | 0.411695 | 0.563077 | 0.744733 |
| target-token delta + sparse KL | 0.612793 | 0.755396 | 0.424890 | 0.565952 | 0.749829 |

VERIFIED:

- Target-token delta supervision is much better aligned with raw teacher
  utility than the old union-top64 sparse behavioral-delta Huber objective.
- The old sparse objective is a verified contributing bottleneck.
- Target-token delta supervision alone still does not make the direct
  additive-token channel pass the oracle capacity gate.

## Free Pair-Z

The free pair-z diagnostic used the existing `AdditiveTokenMemoryInjector`
interface with `z_{s,i} in R^128`.

Train-pair z and injector on `1,152` train pairs:

- `u_text/u_student` Spearman `0.001823`.
- Sign agreement `0.503676`.
- Target-token delta correlation `0.028497`.
- Target-token delta Huber `0.628591`.
- Mean perturbation ratio `0.015093`.

Frozen-injector validation inversion on the 192-pair subset:

- `u_text/u_student` Spearman `-0.069538`.
- Sign agreement `0.467626`.
- Target-token delta correlation `-0.080057`.
- Target-token delta Huber `0.614797`.
- Target NLL `0.773750`.
- Sparse teacher KL `0.278938`.
- Mean perturbation ratio `0.015999`.

Controls on the same subset:

- Zero z had Spearman `0.151501`, sign agreement `0.525180`,
  target-token delta Huber `0.613507`, and target NLL `0.777454`.
- Random z had Spearman `-0.050481`, sign agreement `0.510791`,
  target-token delta Huber `0.615082`, and target NLL `0.773879`.

Joint validation pair-z upper bound:

- Spearman `0.424636`.
- Sign agreement `0.669065`.
- Target-token delta correlation `0.501448`.
- Target-token delta Huber `0.493115`.
- Target NLL `0.720144`.
- Sparse teacher KL `0.224593`.
- Mean perturbation ratio `2.370567`.

VERIFIED:

- The frozen-injector inversion did not beat zero/random controls on the
  primary utility-aligned metrics.
- The joint pair-z upper bound improved target-delta Huber and NLL, but did so
  with perturbation ratio `2.370567` and still did not meet the pair-latent
  gate.

## Free Per-Memory Z

The free per-memory diagnostic trained one `z_i in R^128` per memory and
evaluated all `576` state-held-out validation pairs.

Correct memory-z:

- Spearman `0.194337`.
- Sign agreement `0.570048`.
- Target-token delta correlation `0.159418`.
- Target-token delta Huber `1.460607`.
- Target NLL `1.548202`.
- Sparse teacher KL `1.351795`.
- Mean perturbation ratio `1.110175`.

Controls:

- Zero memory-z: Spearman `0.072883`, sign agreement `0.541063`,
  target-token delta Huber `0.564574`, target NLL `0.634003`,
  sparse KL `0.309482`.
- Mean memory-z: Spearman `0.190957`, sign agreement `0.497585`,
  target-token delta Huber `1.164041`, target NLL `1.220396`,
  sparse KL `0.912072`.
- Random memory-z: Spearman `0.099584`, sign agreement `0.512077`,
  target-token delta Huber `1.382156`, target NLL `1.439910`,
  sparse KL `1.253972`.
- Shuffled memory-z: Spearman `0.140374`, sign agreement `0.545894`,
  target-token delta Huber `1.500429`, target NLL `1.586331`,
  sparse KL `1.398367`.

INTERPRETATION:

- The script's simple `fixed_memory_latent_gate_passed` boolean was true
  because it only checked weak positive correlation/sign above chance.
- Scientifically, free per-memory z did not pass: it catastrophically worsened
  NLL, sparse KL, and target-delta Huber versus the zero control.
- Future gate helpers should include control-relative NLL/KL/delta-error
  requirements, not only weak rank/sign thresholds.

## Hierarchy

| Model | Spearman | Sign agreement | Target-delta Huber | Target NLL | Sparse KL | Delta ratio |
|---|---:|---:|---:|---:|---:|---:|
| direct DeltaE oracle | 0.641904 | 0.776978 | 0.573381 | 0.748773 | 0.261206 | 0.488439 |
| free pair-z joint oracle | 0.424636 | 0.669065 | 0.493115 | 0.720144 | 0.224593 | 2.370567 |
| free pair-z frozen inversion | -0.069538 | 0.467626 | 0.614797 | 0.773750 | 0.278938 | 0.015999 |
| free memory-z | 0.194337 | 0.570048 | 1.460607 | 1.548202 | 1.351795 | 1.110175 |

## Decision

Reached branch: `direct_delta_fails`.

Identified bottleneck:
`additive_token_injection_location_bandwidth_or_behavioral_target`.

VERIFIED:

- Direct DeltaE did not pass the capacity gate.
- K=8 did not repair the direct channel.
- The old sparse behavioral-delta objective is misaligned with target utility.
- Target-token delta supervision is better, but still insufficient to make the
  current last-user additive-token channel pass.
- Pair-latent injector and frozen-injector inversion failed.
- Free per-memory z is not a viable Stage-C repair despite weak positive
  Spearman/sign metrics.

Decision:

- Do not proceed to Stage C2.
- Do not return immediately to memory-content compiler work.
- The next milestone should diagnose or redesign the injection target/location
  and behavioral objective before any full-bank program retraining. Since K=8
  did not help, prioritize injection site and decoder mechanics, such as
  comparing last-user embedding deltas with later-layer residual insertion or
  a direct logit/hidden-state oracle under the same target-token-delta metric.

## Runtime

- Runtime: `14,737.21` seconds, approximately `4.09` hours.
- Final Lambda status after the run: no active Stage-5E tmux session, no tmux
  server, GPU `0 MiB / 0%`.
- Safe to terminate: yes.

## Checkpoints

- Direct best:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/direct_delta/checkpoints/direct_delta_target_delta_plus_sparse_kl_ratio0.5_k4.pt`
- K=8:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/direct_delta/checkpoints/direct_delta_target_delta_plus_sparse_kl_ratio2.0_k8.pt`
- Pair-z trained injector:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/pair_z/checkpoints/pair_z_trained_injector.pt`
- Frozen-injector validation pair-z:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/pair_z/checkpoints/frozen_injector_validation_pair_z.pt`
- Joint validation pair-z:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/pair_z/checkpoints/joint_validation_pair_z.pt`
- Memory-z:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001/memory_z/checkpoints/memory_z.pt`
