# EXP-031B Stage 8A Calibration Lock

Status: VERIFIED and locked before calibration-candidate outcomes.

## Immutable identity

- Source commit: `7fa8019baac7cd7a7c2541a2c080a19fe1ed1ad2`
- Run UUID: `rcmf_benefit_preserving_calibration_9b_20260827_001`
- Global seed: `25101`
- EXP-031A checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- Deployment field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- Critical audit SHA256: `f56bd0f324aee69a3e0f5af21ce20169ed56086fc2bb50e3b252c21852bdc9e5`
- Field tensor bundle SHA256: `17c86f7085cbaeaed88a208ab65c16f7bfdd326e55c0aaf71633e71f92874d41`

## Exact equivalence gate

Attempt `exp031b-stage8a-equivalence-001` completed in `22.9771` seconds.
G100 original-versus-calibrated logits have maximum absolute difference `0.0`;
reader attention, generated token IDs, and executed code match exactly. The
zero-field path also reproduces bare logits, generated token IDs, and executed
code exactly.

## Unlabeled calibration

Attempt `exp031b-stage8a-profile-001` completed in `171.8282` seconds over 98
heldout states and 14 exact critical states. Route-C quantiles pool every causal
prompt token; target-continuation positions and paired outcome labels are
excluded. Route-D uses only the pre-RMS raw-field magnitude.

Layer order is `[7, 14, 21, 28]`.

| Lock | Layer 7 | Layer 14 | Layer 21 | Layer 28 |
|---|---:|---:|---:|---:|
| C50 | 0.0509615541 | 0.0207946487 | 0.0251163971 | 0.0902228504 |
| C75 | 0.0695108548 | 0.0268012378 | 0.0335493609 | 0.1356399208 |
| C90 | 0.1073048413 | 0.0345023572 | 0.0483336486 | 0.1722555161 |

| Lock | Tau |
|---|---:|
| Q50 | 41.4566192627 |
| Q75 | 13.8188730876 |
| Q90 | 4.60629102919 |

Raw-field RMS has median `41.4566`, coefficient of variation `0.803213`, and
p90/p10 ratio `12.2912`. It passes the preregistered Route-D spread gate, so
Route D is `PROCEED`.

## Artifact identity

- Calibration semantic SHA256: `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`
- Calibration file SHA256: `e97f94f2dfc9f5f587665ba1dabecc9e7db7cce5fdc63017dc707f2f917092f7`
- Token-ratio tensor SHA256: `48575167f11093e7bae77ce7462e9eb1948657c12a6605e11e60cee856878f35`
- Critical teacher SHA256: `cf8bd0c446e8c9e8cf476e8a96391595cd4113ed21d6fbbb9ff078c6ec92dbbc`
- Profile maximum slot reconstruction error: `0.0`

No calibration-candidate outcome or first37 candidate outcome had been
inspected when these values were frozen.
