# EXP-016C Direct DeltaE Geometry

Status: **completed**

Run ID: `stage_c_oracle_decoder_5fc_20260810_003`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_decoder_5fc_20260810_003`

## Inputs And Identity

Both source tensors use the same ordered 192 pair IDs and have shape
`[192, 4, 4096]`.

| Target | Source updates | Checkpoint SHA256 | Normalized DeltaE SHA256 | Metric reproduction max delta |
|---|---:|---|---|---:|
| u112 | 112 | `4d4e971fde7b4ab57fa629735f0304fd9eb3859f62414f4d4f8c33506a883210` | `35046ae87aaf2a51dc2ec07b37a234a05fff7cad85cfe613d4e458ee969be0ab` | 0 |
| u128 | 128 | `a31d53f426aeea9f01ea9def68c66004f49143d68e37f215efe0e9d2564e27f4` | `bf501c69bc7f800ed7f03debf65bf9a84f8e043c7acd76426797d2486d31b2b8` | 0 |

The primary manifold target is u112 because it is the best observed
Stage-5F-B sequence-utility checkpoint. u128 is the preregistered Stage-5F-B
stopping checkpoint and is used as the robustness target. Stage-5F-B
artifacts were read only and were not rewritten.

## Spectrum

The primary analysis is an uncentered float64 SVD of the flattened
`192 x 16384` DeltaE matrix. This preserves the no-bias decoder contract
`z = 0 -> DeltaE = 0`.

| Target | Uncentered effective rank | Uncentered stable rank | Centered effective rank | Centered stable rank | Pairwise cosine mean / std |
|---|---:|---:|---:|---:|---:|
| u112 | 179.6761 | 21.7346 | 179.3327 | 24.5705 | 0.019862 / 0.067321 |
| u128 | 179.8787 | 21.7234 | 179.5249 | 24.4919 | 0.019560 / 0.066791 |

Top uncentered singular values:

| Target | Top eight singular values |
|---|---|
| u112 | 9.930821, 8.378436, 7.080145, 6.708403, 5.433949, 5.317780, 5.102806, 5.029933 |
| u128 | 9.936100, 8.299825, 6.977815, 6.679028, 5.460745, 5.308412, 5.156827, 5.083860 |

Cumulative explained squared norm:

| Target | rank 16 | rank 32 | rank 64 | rank 128 | rank 192 |
|---|---:|---:|---:|---:|---:|
| u112 | 0.251374 | 0.370471 | 0.558688 | 0.845709 | 1.000000 |
| u128 | 0.250452 | 0.369814 | 0.556258 | 0.844404 | 1.000000 |

## Interpretation

- The direct DeltaE solutions are high-rank in tensor space: effective rank is
  about 180 of 192, and rank 128 retains about 84.5% of squared norm.
- Near-zero pairwise cosine means the rows are not dominated by one common
  DeltaE direction.
- Tensor-space rank is not the same as behavioral rank. Despite a roughly 39%
  rank-128 relative Frobenius error, the global rank-128 projection preserves
  sequence utility well enough to pass the preregistered low-rank gate on both
  source targets.
- Centered PCA is diagnostic only and was not used to define a deployable
  decoder.

Full machine-readable geometry:

- `geometry/u112_geometry.json`
- `geometry/u128_geometry.json`

under the Lambda artifact root above.
