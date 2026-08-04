# Semantic Retrieval RCMF First-10 Result

Run:

`/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_full_prompt_filtered_no_2a163ab3_semretr_test10_20260803_172000`

Training output:

`/lambda/nfs/rcmf-persist/project/runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_semretr_20260803_172000`

Code version:

`75eb6c0`

Config:

`configs/benchmark/appworld_rcmf_full_prompt_semantic_retrieval.yaml`

Result:

- First-10 score: `4/10 = 40%`.
- Baseline first-10: `3/10 = 30%`.
- Successes: `325d6ec_1`, `325d6ec_2`, `325d6ec_3`, `29a7b7e_1`.
- Retained baseline successes: 3.
- Gained successes: 1 (`325d6ec_2`).
- Lost baseline successes: 0.
- Average steps: 18.3.
- Average prompt tokens: 201,445.7.
- Average generated tokens: 1,853.
- Average wall time: 68.6 seconds.

Interpretation:

This is the best fixed first-10 RCMF result so far, but it does not prove full
distribution improvement.
