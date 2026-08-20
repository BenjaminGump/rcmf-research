# EXP-025D-Fast Observation Isolation

The primary program input provenance is exactly six vectors: `token_mean` and
`final_token` for `source_task_goal`, `pre_action_state`, and
`complete_action`. It accesses neither `post_action_observation` nor
`full_transition_global`.

Hard tests and the final artifact verify that an observation permutation
leaves the primary static program, conditional basis, and pair latent
unchanged. Action modification changes the primary program. The explicit
`full_factorized_r16_action_plus_outcome` diagnostic is permitted to respond
to observation changes. Student-prompt raw-transition count is zero.

The outcome diagnostic did not rescue tensor generalization: validation cosine
was `0.158854` and MSE reduction versus zero was `-0.116772`, compared with
`0.169363/-0.079540` for the observation-excluded primary. The result therefore
does not support moving post-action outcome into the primary method.

