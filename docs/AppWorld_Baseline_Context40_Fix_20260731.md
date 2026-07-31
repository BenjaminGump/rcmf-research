# AppWorld Baseline Context40 修复记录（2026-07-31）

## 结论

用户提醒“baseline 如果也跑满 50 步失败，需要检查是否流程出问题”之后，检查结果表明：之前的 full baseline 确实没有严格复刻原始 `agent.py` 流程。

至少有一个明确复现样例：

- Task: `29a7b7e_1`
- 旧 full baseline: `qwen_appworld_full_prompt_baseline_full_20260731_094940`
- 结果: `steps=50`, `success=False`
- 原始 `agent.py` 风格 debug: `max_context=40`, `steps=36`, `success=True`
- 修复后的正式 `scripts/evaluate.py`: `max_context_turns=40`, `steps=36`, `success=True`

所以旧 full baseline 的 `51/168 = 30.36%` 不能作为后续 RCMF 的可靠对照。

## 发现的流程差异

### 1. Context policy 不一致

原始 `agent.py` 默认：

```text
max_context = 40
```

旧 baseline config：

```yaml
benchmark:
  prompt_profile: full_demo
  max_context_turns: null
```

`null` 会把全部历史 message 都送回模型。对于会打印长 observation 的 AppWorld 任务，这会让后期 prompt 变得非常大，也更容易让模型陷入重复动作。

已新增 corrected baseline config：

```text
configs/baseline/appworld_qwen_full_prompt_context40.yaml
```

该 config 只覆盖：

```yaml
benchmark:
  max_context_turns: 40
```

其他 generation 参数仍由命令行控制。

### 2. 原始 few-shot prompt 最后一条 message 的尾部换行没有保留

原始 `agent.py::_system_prompt_messages()` 会保留最后一条 message 的尾部换行。

旧的 `rcmf/benchmarks/appworld/prompt.py::split_role_prompt()` 对最后一条 message 做了 `.rstrip()`，导致最终 chat template token 序列和原始 agent 有一 token 级差异。这个差异足以让 Qwen3 在确定性生成中走到不同分支。

已在 commit `023b583` 修复，并增加测试：

```text
tests/test_appworld_traces.py::test_split_role_prompt_preserves_final_message_trailing_newline
```

## 验证命令

旧 baseline 中 `29a7b7e_1` 跑满 50 步失败：

```text
runs/experiments/qwen_appworld_full_prompt_baseline_full_20260731_094940/evaluate/test/29a7b7e_1.json
```

原始 agent debug 成功：

```bash
python scripts/run_original_appworld_agent_debug.py \
  --task-id 29a7b7e_1 \
  --experiment-name qwen_original_agent_context40_debug_29a7b7e_1_20260731 \
  --output runs/experiments/qwen_original_agent_context40_debug_29a7b7e_1_20260731/debug.json \
  --root . \
  --max-context 40 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0
```

结果：

```text
success=True
task_completed=True
steps=36
prompt_tokens=421297
generated_tokens=4673
```

修复后的正式 evaluate 成功：

```bash
python scripts/evaluate.py \
  --config configs/baseline/appworld_qwen_full_prompt_context40.yaml \
  --benchmark appworld \
  --split test \
  --task-id 29a7b7e_1 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory \
  --output-dir runs/experiments/qwen_appworld_full_prompt_context40_newline_single_29a7b7e_1_20260731 \
  --experiment-name qwen_appworld_full_prompt_context40_newline_single_29a7b7e_1_20260731
```

结果：

```text
success=True
score=100.0
steps=36
avg_prompt_tokens=421297
avg_generated_tokens=4673
```

## 后续实验要求

后续裸 Qwen3 baseline 和 RCMF evaluation 应统一使用：

```text
configs/baseline/appworld_qwen_full_prompt_context40.yaml
```

同时 RCMF evaluation config 也应使用相同的 AppWorld prompt/history policy，即：

```yaml
benchmark:
  prompt_profile: full_demo
  max_steps: 50
  max_context_turns: 40
```

后续如果比较 baseline 与 RCMF，唯一差异应是：

- baseline: `--no-memory`
- RCMF: 加载 `--checkpoint` 与 `--memory-snapshot`

不要再使用旧的 all-history `max_context_turns: null` full baseline 作为可靠对照。
