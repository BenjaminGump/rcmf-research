# Handoff: Milestone 5F-C / EXP-016C

## Status

- Milestone: completed.
- Branch: `workflow/research-loop`.
- Lambda source commit:
  `95be149e26598546327c33e8207c1c4f833130aa`.
- Result record commit: the commit containing this handoff; report its exact
  SHA after committing.
- Decision branch:
  `shared_decoder_optimization_or_generalization_failure`.
- Formal shared-decoder gate: failed only because no decoder reached the
  corrected plateau in all three folds.
- Stage C2, compiler, selector, AppWorld, and end-to-end RCMF work remain
  unstarted.

## Objective

Determine whether the converged Stage-5F-B K=4 `last_user_k` direct DeltaE
solutions can be compressed into and recovered through a shared 128D no-bias
decoder on state-grouped held-out pairs.

## Direct Sources

| Target | Updates | Checkpoint SHA256 | DeltaE SHA256 | Shape | Reproduction error |
|---|---:|---|---|---|---:|
| u112 | 112 | `4d4e971fde7b4ab57fa629735f0304fd9eb3859f62414f4d4f8c33506a883210` | `35046ae87aaf2a51dc2ec07b37a234a05fff7cad85cfe613d4e458ee969be0ab` | `[192,4,4096]` | 0 |
| u128 | 128 | `a31d53f426aeea9f01ea9def68c66004f49143d68e37f215efe0e9d2564e27f4` | `bf501c69bc7f800ed7f03debf65bf9a84f8e043c7acd76426797d2486d31b2b8` | `[192,4,4096]` | 0 |

u112 is the primary target because it is the best observed Stage-5F-B Huber
checkpoint. u128 is the formal Stage-5F-B stop and robustness target. Neither
source artifact was modified.

## Split

- manifest seed: `20260810`;
- manifest SHA256:
  `3cde923e362c06d9c355f5acd0071b391c1047e0667c916837e8bb0e7421811b`;
- 192 pairs, 57 grouped states, 36 memories;
- each fold: 128 train and 64 held-out pairs;
- held-out states by fold: 17/20/20;
- no state leakage; every pair held out once; all 36 memories present in every
  train fold.

## Geometry And Low Rank

- u112/u128 uncentered effective rank: `179.6761 / 179.8787`.
- rank-128 squared-norm retention: `84.5709% / 84.4404%`.
- rank-128 utility Spearman: `0.980704 / 0.974470`.
- rank-128 sign agreement: `0.992806 / 0.992806`.
- rank-128 sequence Huber: `0.047138 / 0.050873`.
- rank-128 low-rank gate: passed for both targets.
- rank-192 exact tensor and Qwen behavior reproduction: passed.

## Tensor Reconstruction

- Every fold/target/decoder reached its tensor plateau or numerical floor.
- Linear relative Frobenius error range: `8.30e-6` to `1.57e-5`.
- MLP relative Frobenius error range: `5.90e-4` to `1.88e-3`.
- Held-out DeltaE rows never entered decoder training.
- Qwen was not called during tensor-space training.

## Pooled Held-Out Inversion

| Target/method | Spearman | Sign | Sequence Huber | Huber reduction vs zero | Max ratio |
|---|---:|---:|---:|---:|---:|
| u112 frozen linear | .988537 | .992806 | .027538 | 94.6555% | 1.0000001 |
| u112 frozen MLP | .970199 | .992806 | .065315 | 87.3238% | 1.0000007 |
| u112 joint MLP/z | .988757 | 1.000000 | .034243 | 93.3542% | 1.0000010 |
| u112 direct | .984810 | .992806 | .029525 | 94.2696% | 1.0000001 |
| u128 frozen linear | .994685 | 1.000000 | .015615 | 96.9694% | 1.0000001 |
| u128 frozen MLP | .932921 | .971223 | .097231 | 81.1296% | 1.0000010 |
| u128 joint MLP/z | .976308 | .992806 | .051242 | 90.0550% | 1.0000010 |
| u128 direct | .979465 | .992806 | .034512 | 93.3019% | 1.0000001 |

Zero Huber was `0.515256`. Matched-random Huber was `0.514250` on u112 and
`0.513836` on u128. Frozen-linear Huber versus zero had 95% paired-bootstrap
CIs `[-.555564,-.419715]` and `[-.569236,-.432749]` respectively.

Frozen linear was positive in all folds. At u128 it significantly beat the
full direct checkpoint on Huber, difference `-.018897`, CI
`[-.035194,-.003961]`; this is permitted because DeltaE solutions are not
unique.

## Plateau And Decision

- All numerical gate conditions passed for every frozen/joint path on both
  targets.
- No path reached corrected plateau in all three folds.
- Fold-2 frozen MLP stopped at u64 for both targets because Huber deteriorated
  beyond the best-value guard. All other paths reached u128.
- Frozen decoder hashes remained exactly unchanged throughout inversion.
- Formal gate: failed.
- Branch: `shared_decoder_optimization_or_generalization_failure`, reproduced
  on u128.

The data rule out train-tensor reconstruction as the main problem. They also
do not support a 128D dimension failure. The unresolved formal issue is
held-out Qwen inversion convergence/generalization. The SVD-initialized linear
decoder is the strongest next path; do not claim the MLP-specific decision
branch until linear inversion reaches a documented plateau.

## Attempt History

- `_001`: preserved; stopped before decoder training on float32 rank-192
  exactness failure.
- `_002`: preserved; stopped because tolerance handling changed rank-192
  behavior.
- `_003`: resumed atomically across implementation repairs and completed.
- Regression fixes covered SVD precision, tolerance-safe rows, missing import,
  CUDA tensor targets, tensor plateau/numerical floor, best checkpoint restore,
  and u64 continuation.
- No scientific contract changed. No duplicate `_003` run was created.

## Artifacts

- root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_decoder_5fc_20260810_003`;
- summary: `<root>/summary.json`;
- generated report: `<root>/report.md`;
- source validation: `<root>/source_checkpoint_validation.json`;
- split manifest: `<root>/decoder_split_manifest.json`;
- geometry: `<root>/geometry/`;
- low rank: `<root>/low_rank/`;
- decoder checkpoints and rows: `<root>/decoders/u112/` and
  `<root>/decoders/u128/`;
- final post-run audit: `<root>/postrun_validation.json`;
- log:
  `/lambda/nfs/rcmf-persist/runs/logs/oracle_decoder_5fc_20260810_003.log`.

## Validation And Runtime

- local related tests: `54 passed, 1 skipped`;
- Lambda CUDA related tests: `55 passed`;
- independent audit: passed, `0` errors;
- final resumed process runtime: `146552.206 s = 40.7089 h`;
- `_003` artifact wall span: about `80.23 h`, including interruptions and
  repair/resume gaps;
- final controls/report/validation after the last inversion checkpoint:
  about `2.31 h`;
- no tmux or active process; GPU `0 MiB / 0%`; safe to terminate.

## Next Reviewed Milestone

Design EXP-016D as a convergence-only extension of the existing frozen-linear
held-out z checkpoints. Preserve pair IDs, decoder hashes, z values, optimizer
states, objective, ratio 1.0, K=4, `last_user_k`, and the corrected plateau
rule. Stop for review after that gate. Do not start compiler, selector, full
bank, Stage C2, AppWorld, or end-to-end RCMF work.
