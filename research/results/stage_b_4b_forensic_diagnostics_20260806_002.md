# Milestone 4B Forensic Diagnostics

Date: 2026-08-06.

## VERIFIED

- Source commit:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Previous Stage-B pilot:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003`.
- Hard scope was preserved: no Stage C, no program-head training, no injector,
  no Qwen action loss, no full RCMF training, and no AppWorld evaluation.

## Hard-Top-K Dead Zone

Constructed disjoint-support test:

- rank: 64.
- top-k: 4.
- state support: `[0, 1, 2, 3]`.
- memory support: `[60, 61, 62, 63]`.
- support intersection: 0.
- `q = rho * dot(b, alpha)` without rho scaling in the demo had dot `0.0`.
- state logits gradient norm: `0.0`.
- memory logits gradient norm: `0.0`.
- neither state nor memory support can move by gradient from this pair.

Overlapping-support control:

- q/dot: `0.596093`.
- state gradient norm: `0.127603`.
- memory gradient norm: `0.128555`.

## Existing Best Checkpoints

Validation has 139 states and 36 effective memories, for 5,004
state-memory pairs.

| seed | best epoch | zero-overlap fraction | zero raw-dot fraction | support histogram | state top-1 load | alpha top-1 load | gradient status |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| 1 | 1 | 1.000000 | 1.000000 | `{0: 5004}` | 1.0 | 1.0 | all inspected groups zero |
| 2 | 15 | 0.000000 | 0.000000 | `{1: 5004}` | 1.0 | 1.0 | nonzero but small state gradients |
| 3 | 1 | 1.000000 | 1.000000 | `{0: 5004}` | 1.0 | 1.0 | all inspected groups zero |

Representative-batch gradient norms:

- seed 1 best: all inspected groups `0.0`.
- seed 2 best:
  state projector `0.003323`, state address head `0.008742`,
  memory projector `0.111386`, alpha head `0.609727`, rho head `0.553775`.
- seed 3 best: all inspected groups `0.0`.
- program head: `0.0` throughout, as required.

Random initialization and reconstructed epoch-1 snapshots:

| seed | snapshot | zero-overlap | zero raw-dot | state top-1 load | alpha top-1 load |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | init | 0.936651 | 0.936651 | 0.856115 | 1.000000 |
| 1 | epoch 1 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| 2 | init | 0.523181 | 0.523181 | 0.640288 | 0.194444 |
| 2 | epoch 1 | 1.000000 | 1.000000 | 1.000000 | 1.000000 |
| 3 | init | 0.910671 | 0.910671 | 0.877698 | 0.805556 |
| 3 | epoch 1 | 0.000000 | 0.000000 | 1.000000 | 1.000000 |

## Conclusion

The failure is consistent with both:

- A. disjoint-support zero-gradient trapping, verified directly and observed in
  best checkpoints for seeds 1 and 3;
- B. shared-basis collapse, observed in all three best checkpoints.

It is not primarily explained by:

- C. rho/global-prior domination. Seed 2 had nonzero raw dots and gradients,
  but was still state-insensitive because all states and memories collapsed
  onto shared basis coordinates.

## INFERENCES

- Hard top-k is unsafe as an initial addressing parameterization for this
  setting. Once supports become disjoint, gradients through `dot(b, alpha)` do
  not recover the pair.
- Dense softmax alone is also not sufficient; separate scorer ablations show
  the label signal exists, but the current address parameterization does not
  express it.

