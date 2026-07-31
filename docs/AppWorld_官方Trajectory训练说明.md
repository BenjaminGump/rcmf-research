# AppWorld 官方 Trajectory 训练说明

本文档记录当前 RCMF AppWorld 实验应使用的数据来源和 Lambda Cloud 命令。核心原则：

- 不使用 `docs/0a9d82a_1.json` 这种单条示例 trace 训练。
- 不把 `compiled_solution.py` 拆成 step 后作为正式训练轨迹。
- 正式训练数据来自 AppWorld 官方下载的 train trajectory 日志：
  `experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train/tasks/*/logs/environment_io.md`。

## 1. 确认 AppWorld 官方数据

```bash
cd /lambda/nfs/rcmf-persist/project

/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli install
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download data --root .
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download experiment-outputs --root .
```

当前实际验证版本：

```text
AppWorld package: 0.2.0.dev0
AppWorld data: 0.2.0
Experiment outputs path:
  experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train
```

## 1.1 真实交互诊断

如果怀疑 AppWorld 环境没有装好，先运行下面的脚本。它会初始化
`0a9d82a_1`，通过正常 agent 可见的 `apis` 调用 supervisor 和 Simple Note，搜索
habit tracking notes，读取 note 内容，计算 `practiced_good_posture` 最长 streak，最后
调用 `complete_task` 并让 AppWorld 评测。

```bash
cd /lambda/nfs/rcmf-persist/project

/home/ubuntu/venvs/rcmf-py311/bin/python scripts/check_appworld_interaction.py \
  --task-id 0a9d82a_1 \
  --experiment-name rcmf_appworld_interaction_check_0a9d82a \
  --output runs/appworld/interaction_check_0a9d82a.json \
  --expected-streak 4
```

2026-07-30 在 Lambda 上实测 `all_passed=true`，关键结果如下：

```text
password_found: true
login_success: true
habit note search count: 5
all habit notes: 34
posture entries: 24
max_streak: 4
complete_task observation: Execution successful.
evaluation_success: true
```

脚本不会在控制台打印密码或 access token，只打印是否存在和长度。

如果 `data/version.txt` 是 `0.1.0`，当前 AppWorld 代码会拒绝加载 task。先备份旧数据，再下载新数据：

```bash
mv data data_appworld_0.1.0_backup_20260730
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download data --root .
```

## 2. 生成逐步监督数据

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/prepare_appworld_official_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --experiment-output experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train \
  --dataset-name train \
  --output runs/appworld/official_react_gpt4o_train_success_<STAMP>
```

脚本会把每个 step 转为：

```text
input = system prompt + query + previous responses/observations
target = current official response/action
```

默认只保留 `evaluation/report.md` 中 `Num Failed Tests : 0` 的官方轨迹。2026-07-30 的 Lambda 运行结果：

```text
candidate train tasks: 90
successful official trajectories: 47
per-step examples: 710
candidate steps before filtering: 1981
```

## 3. Token 长度和数据完整性

Qwen3-8B tokenizer 在 710 个 official-success target 上的统计：

```text
target tokens: median=26, p95=91, p99=159, max=186
```

因此评测时 `--max-new-tokens 512` 足够，不应通过缩短生成长度来回避慢生成问题。

重要：state/history 由于包含完整 observation，可能非常长。当前代码里的
`--max-query-tokens`、`encoder.max_state_tokens` 和
`encoder.max_experience_tokens` 都会在训练 batch 构造时触发截断。任何正式实验如果
需要截断、过滤、压缩、采样或 summary 化 trajectory，必须先确认实验设定；不要静默
改变训练信息量。

2026-07-30 的
`appworld_official_react_gpt4o_train_20260730_170000` 使用了
`--max-query-tokens 4096`，它应标记为“截断上下文诊断实验”，不能视作完整
trajectory 训练结果。

## 4. 训练

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/train.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_<STAMP> \
  --output-dir runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP> \
  --epochs 1 \
  --batch-size 1 \
  --grad-accumulation-steps 1 \
  --support-size 4 \
  --max-query-tokens 4096 \
  --save-every 100 \
  --log-every 10
```

当前实际运行中的命名：

```text
data:
  runs/appworld/official_react_gpt4o_train_success_20260730_170000
train output:
  runs/experiments/appworld_official_react_gpt4o_train_20260730_170000
log:
  /lambda/nfs/rcmf-persist/runs/logs/rcmf_official_train_20260730_170000.log
tmux:
  rcmf_official_train_20260730_170000
```

## 4.1 生成速度修复

如果 512-token 生成在 H100 上需要几分钟，先检查 attention kernel，而不是降低
`max_new_tokens`。2026-07-30 实测发现默认 PyTorch SDPA 在 Qwen3-8B 长上下文生成
上只有约 3 tokens/s；强制 PyTorch Flash Attention 后，同样 3533-token prompt +
512-token generation 从约 144 秒降到约 10.4 秒。

当前 `rcmf/model/backends/hf_qwen.py` 在 CUDA generation 时会优先使用
`torch.nn.attention.sdpa_kernel([SDPBackend.FLASH_ATTENTION])`，仅当 flash kernel 不可用
时回退。

修复后，`test_normal` 前 10 题诊断评测仍保持 `--max-new-tokens 512` 和
`--max-steps 50`，平均耗时从 133 秒/题降到 53 秒/题。正确率仍为 0/10，原因转为模型
行为问题：输出没有稳定形成可执行 fenced Python code block。

监控：

```bash
tmux list-sessions
tail -100 /lambda/nfs/rcmf-persist/runs/logs/rcmf_official_train_20260730_170000.log
nvidia-smi
```

## 5. 编译 Memory Snapshot

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/compile_memory.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --records runs/appworld/official_react_gpt4o_train_success_<STAMP>/memory_records.jsonl \
  --compiler checkpoint \
  --checkpoint runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/train/checkpoint.pt \
  --output runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/memory.safetensors \
  --ledger-dir runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/memory_ledger
```

## 6. 测试前 10 题

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/evaluate.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --benchmark appworld \
  --split test \
  --limit 10 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --checkpoint runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/train/checkpoint.pt \
  --memory-snapshot runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/memory.safetensors \
  --output-dir runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP> \
  --experiment-name rcmf_appworld_test10_<EVAL_STAMP>
```

AppWorld 的 `--max-steps` 不要设成 1 或 3；当前测试默认用 50。
