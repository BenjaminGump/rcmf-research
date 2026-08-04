# Semantic Retrieval RCMF Partial Full Result

Run:

`/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_full_prompt_filtered_no_2a163ab3_semretr_full_20260803_172000`

Status:

Stopped early after 37 completed tasks.

Result:

- Partial full score: `7/37 = 18.9%`.
- Successes: `325d6ec_1`, `325d6ec_2`, `325d6ec_3`, `29a7b7e_1`,
  `634f342_1`, `0d01c76_1`, `0d01c76_2`.

Reason stopped:

To match the corrected full baseline `53/168 = 31.55%`, the remaining 131 tasks
would have needed 46 additional successes, about `35.1%`, after the observed
`18.9%` opening. Continuing this checkpoint was not a good use of GPU time.

Interpretation:

The final semantic-retrieval checkpoint improves the fixed first-10 slice but is
not yet competitive on the broader AppWorld test distribution.
