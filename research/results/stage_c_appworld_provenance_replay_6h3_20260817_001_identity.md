# EXP-024R3 Corpus Identity Consistency

The complete audit accounts for `46/46` task trajectories and `638/638`
decision examples. Task identity matches `44/46`. The two mismatches are
`b0a8eae_2` and `b0a8eae_3`; both disagree with the official capsule and
backup on supervisor first name, last name, email, and phone hashes. All
decision queries agree with their parent raw trajectory query.

`b0a8eae_2` is validation-only. `b0a8eae_3` is a train task and EXP-017
transition parent. The mismatch is corpus-wide rather than limited to the
original five audit states.
