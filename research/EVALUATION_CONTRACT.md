# AppWorld Evaluation Contract

This file defines the fair-comparison contract for AppWorld RCMF experiments.

## Locked Baseline

- Model family: bare Qwen3-8B local model, no RCMF memory.
- Baseline config: `configs/baseline/appworld_qwen_full_prompt_context40.yaml`.
- Full baseline run:
  `/lambda/nfs/rcmf-persist/project/runs/experiments/qwen_appworld_full_prompt_context40_newline_full_20260731_235900`.
- Full baseline score: `53/168 = 31.55%`.
- Fixed first-10 score: `3/10 = 30%`.
- Fixed first-10 successes:
  `325d6ec_1`, `325d6ec_3`, `29a7b7e_1`.

## Required Shared Settings

When comparing RCMF against the baseline, keep these fixed unless a change is
explicitly recorded as an ablation:

- AppWorld split: `test_normal`.
- Task order: official AppWorld task order.
- Prompt profile: original full AppWorld prompt.
- Agent loop: same `agent.py`/AppWorld ReAct execution semantics.
- Max steps: `50`.
- Max new tokens: `512`.
- Generation should allow EOS; low generation caps must not be used to hide
  slow generation.
- Environment observations must come from a working AppWorld environment.

## Training Data Contract

- Training examples must be per-step trajectory examples.
- Input = system prompt + task query + prior trace through the previous step.
- Target = current-step model response.
- EOS is appended to the target.
- Only target tokens are supervised; prompt tokens use label `-100`.
- No prompt, trace, or target truncation is allowed without user approval.

## Required Preflight

Before training on any new AppWorld prepared dataset or subset:

1. Run `scripts/check_training_query_lengths.py` with the exact tokenizer and
   effective context limit.
2. If over-limit examples exist, record task ids, episode ids, JSONL line
   numbers, token counts, and source trace paths.
3. Stop and ask the user whether to filter.
4. Do not silently truncate, downsample, or filter.

## Required Reporting

Every evaluation should report:

- aggregate success rate;
- exact numerator and denominator;
- success set;
- retained, gained, lost, and both-failed counts versus the locked baseline
  when comparable;
- average steps;
- average prompt tokens;
- average generated tokens;
- average wall time;
- checkpoint path;
- config path;
- command or runner script;
- Lambda log and result paths.
