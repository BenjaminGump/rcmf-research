# EXP-025D-Fast Decoder, Target, and Program Results

## Decoder

The direct row-repair SVD route failed (`rho=0.4941`, Huber reduction `2.26%`).
The bounded fallback no-bias decoder passed on 16 grouped-heldout pairs at u32
with Spearman `0.9382`, sign agreement `1.0`, Huber `0.000788`, and `99.54%`
Huber reduction versus zero. Decoder SHA256:
`71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.

## Canonical targets

The 224-pair primary targets reached u64 with utility Spearman `0.989921`,
sequence Huber `0.035762`, and exact equal update counts. The second-seed
subset passed decoded cosine (`0.965719`), repeat utility Spearman (`0.997059`),
and sign agreement (`1.0`). Strict final perturbation ratios are `<=1.0`.

## Program gate

The A-only grouped split is 75 train / 53 validation. PairMLP achieved cosine
`0.214108` and MSE reduction `0.000180`, failing required `0.25/0.10` gates.
The observation-excluded factorized primary achieved cosine `0.169363` and
negative MSE reduction `-0.079540`; static-only was better. Action-plus-outcome
also failed.

Decision: `state_transition_representations_insufficient`. B/C/D/E Qwen
validation and H1-H4 one-step conditions were correctly skipped.

