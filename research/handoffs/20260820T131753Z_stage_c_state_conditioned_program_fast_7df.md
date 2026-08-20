# Structured Handoff: EXP-025D-Fast

## Identity

- Run UUID: `state_conditioned_program_fast_7df_20260819_001`
- Starting branch/SHA: `research/v4-state-conditioned-transition-program` at
  `a1e8bad323b05c741bfb914d0563f9e4a89726b6`
- Archive: `archive/v4-pre-fast-program-pilot` at the same SHA
- Working branch: `research/v4-fast-program-pilot`
- Final source SHA: `21784e4f1e37a59e3d0895ea78fbc43351b7b4a4`
- Final record SHA: commit containing this handoff
- Artifact: `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_fast_7df_20260819_001`

## Recovery and attempts

The append-only ledger has nine closed attempts and 18 events after
finalization. Preflight attempts 002/003/005 completed; 004 rejected a config
manifest mismatch. GPU-001 exposed a CPU/CUDA index mismatch. GPU-002 showed
the clean row-repair SVD decoder did not pass and prompted the preregistered
bounded fallback. GPU-003 completed u64 and repeat-u16 targets, then stopped on
a stability-call API error. GPU-004 resumed those exact checkpoints under the
regression-tested fix and stopped scientifically when PairMLP failed. No
scientific parameter changed across recovery attempts.

## Verified results

- A/B/C/D/E: `128/24/24/24/32`; `224` unique pairs; zero truncation.
- Incremental field and observation-isolation contracts pass.
- Prefix equivalence fails; the run used full forward.
- Fallback decoder passes; SHA256
  `71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.
- Canonical u64: Spearman `0.989921`, Huber `0.035762`, 224 x 64 updates.
- Stability: decoded cosine `0.965719`, utility Spearman `0.997059`, sign `1.0`.
- PairMLP: cosine `0.214108`, MSE reduction `0.000180`; gate fails.
- Primary factorized: cosine `0.169363`, MSE reduction `-0.079540`.
- Action-plus-outcome also fails; outcome access does not rescue the target.
- Measured GPU-attempt time `7.9936` H100 hours; wall span `8.2799` hours.

## Decision and boundary

Decision branch: `state_transition_representations_insufficient`.

The pair targets are identifiable, but current independently encoded state and
transition multiview features cannot amortize them. B/C/D/E teacher-forced
Qwen validation and H1-H4 AppWorld conditions were not unlocked and have no
metrics. The compiled program does not work under this pilot.

Do not proceed to full-bank integration, p(s,m_transition), compiler/injector,
Stage C2, end-to-end RCMF, full AppWorld evaluation, or V4 tagging.

## Recommended 3-5 day review plan

1. Day 1: audit canonical-target rank, target normalization, split difficulty,
   and state/transition nearest-neighbor leakage on the frozen 224 pairs.
2. Day 2: run one bounded pair-aware frozen-Qwen information upper bound using
   the same targets and grouped split; no architecture sweep.
3. Day 3: if the upper bound passes, distill one field-compatible richer
   observation-excluded representation and rerun PairMLP/factorized tensor gates.
4. Day 4: only after tensor passage, run B/C/D/E teacher-forced controls.
5. Day 5: only after teacher-forced passage, run the existing H1-H4 one-step
   harness and make the go/no-go decision for full-bank integration.

At handoff there is no `exp025df` tmux session or active experiment process;
GPU utilization and memory are zero. Artifacts are finalized and safe to
terminate.

