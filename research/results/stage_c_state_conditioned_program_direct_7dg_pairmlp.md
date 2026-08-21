# EXP-025D-Direct PairMLP Behavioral Upper Bound

The observation-excluded PairMLP and its private decoder were trained directly
through frozen Qwen with global seed `25101`. The decoder started from clean
SHA256 `71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.
Qwen and the selector remained frozen, the student prompt contained no raw
transition, K=4 `last_user_k` was unchanged, and all 479 A-train pairs received
exactly the same update count.

u8 validation was Spearman `0.570923`, Huber `0.170581`. u16 was Spearman
`0.544400`, Huber `0.162811`. The Huber improvement was `4.5553%`, below the
fixed `5%` continuation-selection threshold, and Spearman changed by
`-0.026523`; u8 was therefore restored for final evaluation.

Final correct Spearman on A/B/C/D/E was
`0.570923/0.386353/0.478483/0.406490/0.395259`. Huber reductions versus zero
were `33.52%/23.62%/28.70%/27.04%/22.67%`. A and E both beat state and
transition shuffles, and B had positive correlation and Huber reduction.
Every preregistered PairMLP gate check passed.

Selected checkpoint SHA256:
`80506a5d9b1c3031b5468fb59c0b6d9e01d7d50ddc1fee49115a88eb8b8b429d`.
Trained private decoder SHA256:
`4d4b84970d1c6c9818790cada49e2554fc1c3b58d298175deac4ca70c837a6cd`.

Conclusion: direct behavior is learnable by a pair-specific upper bound from
the existing observation-excluded representations. This result unlocked the
single factorized run; it does not itself define a fixed-cost compiled field.
