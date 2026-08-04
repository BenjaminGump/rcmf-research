# Bare Qwen AppWorld Baseline

Run:

`/lambda/nfs/rcmf-persist/project/runs/experiments/qwen_appworld_full_prompt_context40_newline_full_20260731_235900`

Verified result:

- Full `test_normal`: `53/168 = 31.55%`.
- Fixed first-10: `3/10 = 30%`.
- First-10 successes: `325d6ec_1`, `325d6ec_3`, `29a7b7e_1`.

Contract:

- Original full AppWorld prompt.
- `max_steps=50`.
- `max_new_tokens=512`.
- Bare Qwen3-8B, no RCMF memory.

This is the locked comparison baseline for current RCMF AppWorld experiments.
