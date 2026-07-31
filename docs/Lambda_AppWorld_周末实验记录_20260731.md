# Lambda AppWorld 周末实验记录（2026-07-31）

本文记录 2026-07-31 在 Lambda Cloud 实例上启动的 AppWorld 周末实验状态。核心原则是：后续 RCMF 评测必须保留已经验证过的裸 Qwen3 baseline 流程，baseline 与 RCMF 的差别只应是模型是否加载 RCMF 记忆模块。

## 远端位置

- Project: `/lambda/nfs/rcmf-persist/project`
- Weekend tmux session: `rcmf_weekend_20260731_094940`
- Weekend log: `/lambda/nfs/rcmf-persist/runs/logs/rcmf_weekend_20260731_094940.log`
- Runner script: `scripts/lambda_weekend_experiment.sh`
- Code commit containing the prompt, preflight, and length-stat fixes: `34f2b82`

## 保留的裸 Qwen3 测试流程

固定测试命令使用：

```bash
python scripts/evaluate.py \
  --config configs/baseline/appworld_qwen_full_prompt.yaml \
  --benchmark appworld \
  --split test \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory
```

该流程的关键配置：

- `prompt_profile: full_demo`
- `max_context_turns: null`
- `max_steps: 50`
- `max_new_tokens: 512`
- 不加载 memory / RCMF checkpoint

## Baseline 结果

前 10 题复现结果：

- Output: `runs/experiments/qwen_appworld_full_prompt_baseline_test10_20260731_094940`
- Success rate: `3/10 = 30.0%`
- 成功 task: `325d6ec_1`, `325d6ec_2`, `325d6ec_3`

完整 `test_normal` 结果：

- Output: `runs/experiments/qwen_appworld_full_prompt_baseline_full_20260731_094940`
- Summary: `runs/experiments/qwen_appworld_full_prompt_baseline_full_20260731_094940/evaluate/test/summary.json`
- Success rate: `51/168 = 30.3571%`
- Average score: `30.3571`
- Average steps: `23.4702`
- Average prompt tokens per episode: `341556.5357`
- Average generated tokens per episode: `2669.4702`
- Average wall time per episode: `85.4735s`

这说明当前裸 Qwen3 full-demo evaluation flow 是稳定、可复用的；后续 RCMF 测试应继续使用同一脚本和同一组 generation 参数。

## 已修复的问题

### 1. 官方轨迹数据准备没有使用 full-demo query

旧的 `scripts/prepare_appworld_official_traces.py` 中，`load_task_query()` 调用 `build_task_message()` 时没有传入 `prompt_profile`，因此即使外层配置是 `full_demo`，生成的训练 query 仍会退回 minimal 口径。

修复提交：`a7be6f1`

修复后重新生成的数据：

- `runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1`
- Records: `47`
- Decision examples: `710`
- 只使用 AppWorld 官方 gpt-4o legacy react code agent 的成功轨迹，没有额外过滤或截断。

### 2. full-demo 训练 prompt 与评测 prompt 不一致

评测时 `full_demo` 使用 `get_initial_messages("full_demo")`，即把 `prompt.py::AGENT_SYSTEM_PROMPT_TEMPLATE_AW` 拆成原始 few-shot chat history，再追加当前 task 与后续 observation。

旧训练渲染器把 system prompt 和 state 放进两个 message，和评测流程不一致。

修复后，`rcmf/training/datasets.py::_render_training_prompt()` 对 AppWorld full-demo 样本执行：

1. 解析 `state_text` 中的 `[QUERY]` 与 `[TRACE SO FAR]`。
2. 使用 `get_initial_messages("full_demo")` 构造原始 few-shot chat history。
3. 追加当前 task user message。
4. 对每个历史 step 追加 assistant code 与 user observation。
5. 训练 target 仍然只包含当前 step response，并在末尾追加 EOS；labels 仍只训练 target 部分。

相关测试：

```bash
python -m pytest -q
# 25 passed
```

## 当前正式训练阻塞点

在不截断、不过滤、不压缩训练输入的前提下，full-demo 口径的官方成功轨迹数据无法完整用于 Qwen3-8B action loss 训练。

重新统计命令：

```bash
python scripts/check_training_query_lengths.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1 \
  --output runs/experiments/weekend_20260731_full_demo_a7be6f1_query_token_lengths_v3.json \
  --top-k 20
```

统计结果：

- Examples: `710`
- Qwen tokenizer model max length: `131072`
- Qwen model config max position embeddings: `40960`
- Effective context limit used by training preflight: `40960`
- Prompt + target token count:
  - min: `7334`
  - median: `10662`
  - p90: `34681`
  - p95: `1543832`
  - p99: `2316009`
  - max: `2318676`
- Target token count:
  - median: `27`
  - p95: `92`
  - max: `187`
- 超过 Qwen 40,960 token 有效上下文上限的样本数: `66`
- 超长样本全部来自同一个 episode: `appworld:trace:2a163ab_3`

最长样本：

- Episode: `appworld:trace:2a163ab_3`
- Step: `72`
- Prompt tokens: `2,318,489`
- Target tokens: `187`
- Total tokens: `2,318,676`
- `state_text` 字符数约 `5,539,001`

诊断脚本：

```bash
python scripts/inspect_appworld_training_example.py \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1 \
  --episode-id appworld:trace:2a163ab_3 \
  --step-id 72 \
  --snippet-chars 1000 \
  --no-tokenizer
```

直接原因：该官方成功轨迹多次把约 `600,851` 字符的 Venmo social feed observation 加入历史上下文。到后期 step 时，单个训练样本的完整 history 已经超过 230 万 token。

## 为什么没有继续正式 RCMF 训练

当前 RCMF 的 memory/state 表示可以用冻结 Qwen 分块离线编码，但 action loss 仍需要让 Qwen 对完整 `prompt + target` 做前向。对于超过 `40960` token 有效上下文上限的样本，Qwen3-8B 本身不支持完整注意力上下文；把 230 万 token 直接送入模型既不符合模型上下文限制，也不可计算。

已在 `405c52e` 给 `scripts/train.py` 增加训练前 query length preflight。对当前 full-demo 数据运行：

```bash
python scripts/train.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1 \
  --output-dir runs/experiments/preflight_full_demo_a7be6f1_405c52e \
  --max-steps 1
```

会在离线表示缓存和训练开始前失败，并写出：

- `runs/experiments/preflight_full_demo_a7be6f1_405c52e/train/query_length_preflight.json`
- Error: `66 training prompt+target sample(s) exceed context_limit=40960`

可行的下一步都涉及训练数据策略或训练目标定义：

- 排除 `appworld:trace:2a163ab_3` 或排除其中超过上下文上限的 66 个 step。
- 允许按原 agent 的上下文窗口策略限制历史 message 数。
- 允许对 observation 做结构化压缩或 API-aware 摘要。
- 改写训练目标，使 action loss 不再依赖完整 raw prompt 的一次性 Qwen 前向。

这些都属于“过滤、截断、压缩或重新定义训练输入/目标”，需要明确确认后才能作为正式实验策略使用。

## 下次继续时建议先决策的问题

推荐先决定：是否允许在训练阶段排除唯一的超长成功轨迹 `2a163ab_3`，先用剩余 `644/710` 个不超过 Qwen 上下文的 full-demo step 做一次正式 RCMF 训练和同流程测试。

这样做的优点是最小化策略变化：不截断任何保留样本，不改变评测流程，只是不训练 Qwen 无法完整前向的样本。缺点是它仍然是训练数据过滤，需要作为实验设定明确记录。
