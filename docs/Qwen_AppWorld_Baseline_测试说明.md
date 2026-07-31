# Qwen AppWorld Baseline 测试说明

本文档记录“原版 Qwen 模型，不使用 RCMF memory/injector”的 AppWorld baseline 跑法。

## 测试入口

实际测试脚本是：

```text
scripts/evaluate.py
```

它的调用链是：

```text
scripts/evaluate.py
  -> rcmf.benchmarks.appworld.agent.RCMFAppWorldAgent.run_task
  -> rcmf.model.backends.hf_qwen.HFQwenBackend.generate
```

裸 Qwen baseline 必须传 `--no-memory`，并且不要传 `--checkpoint` 或
`--memory-snapshot`。这样模型就是 `configs/base.yaml` 里指定的
`Qwen/Qwen3-8B`，没有 RCMF 创新框架介入。

## Prompt 路径

baseline 配置文件是：

```text
configs/baseline/appworld_qwen_full_prompt.yaml
```

这个配置显式设置：

```yaml
benchmark:
  prompt_profile: full_demo
  max_steps: 50
  max_context_turns: null
```

`full_demo` 会使用项目根目录 `prompt.py` 中的原始 AppWorld prompt：

```text
prompt.py::AGENT_SYSTEM_PROMPT_TEMPLATE_AW
prompt.py::AGENT_QUERY_PROMPT_TEMPLATE_AW
```

实现位置在：

```text
rcmf/benchmarks/appworld/prompt.py
```

其中 `AGENT_SYSTEM_PROMPT_TEMPLATE_AW` 会按照原始 `agent.py` 的逻辑，把
`USER:`、`ASSISTANT:`、`SYSTEM:` 标记拆成多条 chat messages，而不是塞进一条
system message。`AGENT_QUERY_PROMPT_TEMPLATE_AW` 会用当前 `AppWorld` 的
`world.task.supervisor` 和 `world.task.instruction` 渲染。

AppWorld 每一步返回的 observation 会原样喂回模型，以保证 token、password、id
等后续 API 调用所需字段不会被破坏；写入结果 JSON 的 trace 仍然会脱敏。

## 单题诊断命令

优先用已知可交互的 `0a9d82a_1` 做单题诊断：

```bash
source /lambda/nfs/rcmf-persist/env.sh
source /home/ubuntu/venvs/rcmf-py311/bin/activate
cd /lambda/nfs/rcmf-persist/project

STAMP=$(date +%Y%m%d_%H%M%S)
python scripts/evaluate.py \
  --config configs/baseline/appworld_qwen_full_prompt.yaml \
  --benchmark appworld \
  --split test \
  --task-id 0a9d82a_1 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory \
  --output-dir runs/experiments/qwen_appworld_full_prompt_baseline_$STAMP \
  --experiment-name qwen_appworld_full_prompt_baseline_0a9d82a_$STAMP
```

## test_normal 前 10 题命令

```bash
source /lambda/nfs/rcmf-persist/env.sh
source /home/ubuntu/venvs/rcmf-py311/bin/activate
cd /lambda/nfs/rcmf-persist/project

STAMP=$(date +%Y%m%d_%H%M%S)
python scripts/evaluate.py \
  --config configs/baseline/appworld_qwen_full_prompt.yaml \
  --benchmark appworld \
  --split test \
  --limit 10 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory \
  --output-dir runs/experiments/qwen_appworld_full_prompt_baseline_$STAMP \
  --experiment-name qwen_appworld_full_prompt_baseline_test10_$STAMP
```

## 结果位置

`scripts/evaluate.py` 写出的汇总结果在：

```text
runs/experiments/qwen_appworld_full_prompt_baseline_<STAMP>/evaluate/test/summary.json
runs/experiments/qwen_appworld_full_prompt_baseline_<STAMP>/evaluate/test/results.jsonl
```

每题的 AppWorld 原始执行日志和官方评测文件在：

```text
experiments/outputs/<experiment-name>/logs/<task-id>/environment_io.md
experiments/outputs/<experiment-name>/evaluations/<task-id>.json
experiments/outputs/<experiment-name>/evaluations/on_only_<task-id>.json
```

## 2026-07-30 Lambda 运行记录

单题诊断：

```text
experiment-name: qwen_appworld_full_prompt_baseline_0a9d82a_20260730_104715
output-dir: runs/experiments/qwen_appworld_full_prompt_baseline_20260730_104715
task-id: 0a9d82a_1
result: failed, 13 steps
```

这题失败不是环境或 prompt 问题。裸 Qwen 成功登录 Simple Note 并读取了第一页
habit notes，但没有翻页继续读取更早日志，因此把最长 streak 算成 2；环境诊断中
这题的正确答案是 4。

`test_normal` 前 10 题：

```text
experiment-name: qwen_appworld_full_prompt_baseline_test10_20260730_104912
output-dir: runs/experiments/qwen_appworld_full_prompt_baseline_test10_20260730_104912
success_rate: 0.3
average_score: 30.0
avg_steps: 26.2
avg_prompt_tokens: 413388.9
avg_generated_tokens: 2554.9
avg_wall_time_s: 88.54025759059877
```

逐题结果：

```text
3d9a636_1: failed, 50 steps
3d9a636_2: failed, 16 steps
3d9a636_3: failed, 50 steps
fd1f8fa_1: failed, 10 steps
fd1f8fa_2: failed, 16 steps
fd1f8fa_3: failed, 10 steps
325d6ec_1: success, 23 steps
325d6ec_2: success, 17 steps
325d6ec_3: success, 20 steps
29a7b7e_1: failed, 50 steps
```

第 10 题出现了 Qwen 的 40960 token 最大长度提醒。这不是数据截断参数造成的；
baseline 配置中 `max_context_turns: null`，Agent 会保留完整历史。它说明 full
demo prompt 加 50 步 AppWorld observation 可能超过 Qwen3-8B 的上下文窗口，后续做
正式长程评测时需要单独决定历史管理策略，不能静默截断。

## original.zip 对照诊断

`docs/original.zip` 中的代码文件和当前项目根目录中的原始文件 hash 一致：

```text
agent.py
main.py
model.py
prompt.py
test_qwen3_8b.py
```

`original/main.py` 不能直接完整运行，因为它依赖 zip 中没有包含的 `dataset.py`。
核心可复现部分是 `agent.py::AppWorldAgent.execute()`。

为逐步检查“每一步给 LLM 什么输入、LLM 输出什么”，新增诊断脚本：

```text
scripts/run_original_appworld_agent_debug.py
```

它复刻原始 agent loop：

```text
system prompt: prompt.py::AGENT_SYSTEM_PROMPT_TEMPLATE_AW
query prompt: prompt.py::AGENT_QUERY_PROMPT_TEMPLATE_AW
system prompt splitting: 按 USER:/ASSISTANT:/SYSTEM: 拆成 chat messages
max_context: 40
max_steps: 50
temperature: 0.3
backend: 本地 Qwen/Qwen3-8B
```

每一步会保存完整 `messages`、Qwen chat template 渲染文本、prompt tokens、原始模型
输出、提取到的代码、AppWorld observation 和完成状态。debug JSON 中包含完整
AppWorld observation，可能有 AppWorld 内部模拟账号凭据，不要公开分享。

单题命令示例：

```bash
source /lambda/nfs/rcmf-persist/env.sh
source /home/ubuntu/venvs/rcmf-py311/bin/activate
cd /lambda/nfs/rcmf-persist/project

python scripts/run_original_appworld_agent_debug.py \
  --task-id 0a9d82a_1 \
  --experiment-name original_loop_local_qwen_0a9d82a_seed1_<STAMP> \
  --output runs/debug/original_loop_local_qwen_0a9d82a_seed1_<STAMP>.json \
  --root /lambda/nfs/rcmf-persist/project \
  --model-name Qwen/Qwen3-8B \
  --max-context 40 \
  --max-steps 50 \
  --max-new-tokens 1024 \
  --temperature 0.3 \
  --top-p 1.0 \
  --seed 1
```

### 0a9d82a_1 逐步诊断

```text
experiment-name: original_loop_local_qwen_0a9d82a_seed1_20260730_111023
debug-json: runs/debug/original_loop_local_qwen_0a9d82a_seed1_20260730_111023.json
result: task_completed=True, official success=False, 12 steps
```

关键失败链路：

```text
step 5: search_notes(query="good posture OR posture")
        只拿到第一页结果，其中 habit-tracker note 只有 2023-05-17 和 2023-05-16。

step 6: 只 show_note 了这两条 habit-tracker note。

step 7: 根据两条记录算出 longest streak = 1。

step 8-11: 尝试用类似搜索引擎的 query 语法继续找更早日志，
           例如 "habit tracker AND created_at:<2023-05-16"，
           但 Simple Note search_notes 不支持这种过滤语义，返回无关笔记。

step 12: 误以为已经检查完全部相关日志，complete_task(answer=1)。
```

根因：模型没有查看 `simple_note.search_notes` 的详细 API doc，也没有使用
`page_index/page_limit` 翻页。AppWorld 的 `search_notes` 默认只返回有限条数并且
pinned notes 优先，所以第一页不等于全集。正确做法应当遍历所有 habit-tracker
notes 或按 `page_index` 翻页后再计算 streak。

### 325d6ec_1 对照诊断

```text
experiment-name: original_loop_local_qwen_325d6ec_1_seed1_20260730_111518
debug-json: runs/debug/original_loop_local_qwen_325d6ec_1_seed1_20260730_111518.json
result: task_completed=True, official success=False, 13 steps
```

这题在 deterministic baseline 中曾成功，但 exact-original-loop 的采样 run 失败。
失败机制不是 prompt 缺失，而是状态型 API 恢复错误：

```text
step 9: 正确分页读取 liked songs，共 19 首。

step 10: 循环调用 previous_song()，当 previous_song_result 的 song_id 在 liked_song_ids
         中时，尝试打印 current_song['title']。但 previous_song() 只返回 message/song_id，
         没有 title，于是 KeyError。

step 12: 发现 previous_song() 返回不含 title 后，又先调用了一次 previous_song()，
         再 show_current_song() 检查。这会在已经到达 liked song 后额外后退一步，
         可能越过任务要求的“第一个已经 liked 的 previous song”。

step 13: complete_task()，但官方评测失败。
```

根因：模型知道查 API docs，也知道 liked songs 要分页，但没有意识到
`previous_song()` 是改变播放器状态的动作。报错恢复时不应再次调用
`previous_song()`，而应先 `show_current_song()` 检查当前状态。
