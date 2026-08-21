# EXP-025D-Direct Quick Failure Audit

The two-hour CPU timebox found no implementation or grouped-split bug in the
saved EXP-025D-Fast latent-distillation result.

| Model / split | Cosine | MSE reduction vs zero | Huber |
| --- | ---: | ---: | ---: |
| PairMLP train | 0.370794 | 0.140510 | 0.162092 |
| PairMLP validation | 0.214108 | 0.000180 | 0.167881 |
| Factorized train | 0.423527 | 0.178183 | 0.158385 |
| Factorized validation | 0.169363 | -0.079540 | 0.175456 |
| Mean-target validation | 0.195907 | 0.033176 | 0.163247 |

The saved PairMLP classification is
`failed_to_fit_training_data_and_validation_collapsed_to_zero`. The 128 x 128
target matrix has effective rank `58.30046`; target norm mean/median is
`3.018957/3.387326`; nearest-neighbor cosine mean/median is
`0.409839/0.359835`. The old split has `75/53` train/validation pairs with
zero query-task and transition-parent overlap.

Direct behavioral training subsequently made the PairMLP pass A/B/E. Thus the
old failure should be interpreted as an amortization-target failure, not as
proof that the frozen representations contain no pair-specific behavioral
signal.
