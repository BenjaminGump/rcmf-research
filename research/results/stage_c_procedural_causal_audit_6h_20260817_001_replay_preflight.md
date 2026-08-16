# EXP-024A Replay Preflight

Exact AppWorld replay failed for all 45 selected states. Only 2 states matched
their complete history, 23 matched the target observation in isolation, and
81/372 prior observations matched. The two zero-history states both failed at
the target step.

All nine source trajectories record AppWorld code/data/evaluation version
0.1.0. Lambda runs AppWorld 0.2.0.dev0 from upstream commit
`a072b7a86e7c1d5b1d7175659d750ebb9b79f10a`. This mismatch is verified; its
causal role remains an inference pending a matched-version replay.

Decision: `appworld_one_step_replay_invalid`. Candidate generation was not
started.
