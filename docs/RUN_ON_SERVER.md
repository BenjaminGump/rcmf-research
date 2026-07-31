# RCMF 服务器运行指南

如果你使用 Lambda Cloud 且环境是空白状态，建议优先看新版 quickstart：
[LAMBDA_CLOUD_QUICKSTART.md](LAMBDA_CLOUD_QUICKSTART.md)。本文档保留为通用
Linux 服务器参考。

这份文档说明：把整个 `RCMF_codex` 文件夹复制到 Linux 服务器后，如何安装环境、做 smoke test、准备 AppWorld 数据、编译 memory snapshot，并理解当前代码中哪些入口已经可直接运行，哪些入口还处在需要继续接入真实训练数据的阶段。

当前仓库的目标不是只跑一个 demo，而是逐步形成完整实验工程。现在已经可运行的是 M0-M2/M3 starter：配置、schema、memory algebra、ledger、prefix injection、mock 训练图、hashing smoke 编译、ablation/scaling job 生成、AppWorld adapter 字段探测。真正的 Qwen3-8B 多 GPU 训练入口已经保留，但非 smoke 训练还没有完成真实 dataset tokenization/collate 接入，因此文档会明确标出边界。

## 1. 复制前建议

如果只是验证代码，不需要把本地运行产物一起传到服务器。推荐复制这些内容：

```bash
configs/
docs/
rcmf/
scripts/
tests/
pyproject.toml
README.md
RCMF_Codex_Implementation_Plan.md
agent.py
main.py
model.py
prompt.py
test_qwen3_8b.py
```

这些可以不复制，复制了也没关系：

```bash
runs/
__pycache__/
.pytest_cache/
```

如果你用 `rsync`，可以这样传：

```bash
rsync -av \
  --exclude runs \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  /local/path/RCMF_codex/ user@server:/data/projects/RCMF_codex/
```

服务器上进入项目目录：

```bash
cd /data/projects/RCMF_codex
```

## 2. 服务器硬件与软件假设

最低 smoke test：

```text
Linux
Conda or Miniconda
Python 3.10+
CPU 即可
```

真实 Qwen3-8B 推理/训练建议：

```text
Linux
NVIDIA driver + CUDA
8 GPUs for planned multi-GPU training
足够的 Hugging Face cache 磁盘空间
AppWorld official package and data
```

注意：本仓库新代码不依赖 Windows API。旧的根目录 `agent.py/main.py/model.py/prompt.py` 只是 legacy reference，不建议作为服务器主入口。

## 3. 创建 Conda 环境

建议服务器上单独建环境：

```bash
conda create -n rcmf python=3.11 -y
conda activate rcmf
python -m pip install --upgrade pip
```

先安装和服务器 CUDA 匹配的 PyTorch。下面以 CUDA 12.1 wheel 为例；如果你的服务器 CUDA/PyTorch 版本不同，请按服务器标准安装：

```bash
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

安装本项目。注意：如果你是在已有的 `appworld_env` 里安装，不要把
DeepSpeed 和 AppWorld 放在同一个环境里强行解决；较新的 DeepSpeed 会拉
`pydantic>=2`，而 AppWorld 依赖 `pydantic<2`。默认 `server` extra 不安装
DeepSpeed：

```bash
python -m pip install -e ".[dev,server]"
```

如果你需要 DeepSpeed，推荐单独创建训练环境：

```bash
conda create -n rcmf_train python=3.11 -y
conda activate rcmf_train
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
python -m pip install -e ".[dev,server,deepspeed]"
```

环境分工建议：

```text
appworld_env:
  AppWorld 数据准备、AppWorld 评测、pytest、RCMF smoke

rcmf_train:
  大模型多 GPU 训练、DeepSpeed/FSDP/Accelerate
```

验证导入：

```bash
python - <<'PY'
import torch
import transformers
import safetensors
import yaml
import rcmf
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
print("gpu count:", torch.cuda.device_count())
print("rcmf:", rcmf.__version__)
PY
```

## 4. Hugging Face / Qwen3-8B 准备

默认模型配置在 `configs/base.yaml`：

```yaml
model:
  backend: hf_qwen
  name: Qwen/Qwen3-8B
  dtype: bfloat16
  freeze_backbone: true
  enable_thinking: false
  device_map: null
```

建议设置模型缓存目录：

```bash
export HF_HOME=/data/hf_cache
export TRANSFORMERS_CACHE=/data/hf_cache/transformers
```

如果服务器需要登录 Hugging Face：

```bash
huggingface-cli login
```

只验证 tokenizer，不加载大模型：

```bash
python - <<'PY'
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3-8B", trust_remote_code=True)
print(type(tok).__name__)
PY
```

加载本地 Qwen 后端会占用显存：

```bash
python - <<'PY'
from rcmf.config import load_config
from rcmf.factory import build_backend
cfg = load_config("configs/base.yaml")
backend = build_backend(cfg, load_model=True)
print("loaded:", cfg.model.name)
print("device:", backend.device)
PY
```

推理显存不够时，可以临时把 `configs/base.yaml` 里的 `model.device_map` 改成 `auto`。正式多 GPU 训练时不要用 `device_map: auto`，应交给 `torchrun`、Accelerate、FSDP 或 DeepSpeed 管理。

## 5. AppWorld 准备

如果服务器已经安装 AppWorld，只需要验证：

```bash
python - <<'PY'
from appworld import load_task_ids
print("train sample:", load_task_ids("train")[:3])
PY
```

如果没有安装，请先按 AppWorld 官方仓库说明安装 package 和数据。常见形式是把 AppWorld 官方 repo clone 到服务器，然后在同一个 conda 环境里：

```bash
python -m pip install -e /data/projects/appworld
```

再按官方说明下载/初始化数据。最终必须让下面命令通过：

```bash
python - <<'PY'
from appworld import AppWorld, load_task_ids
task_id = load_task_ids("train")[0]
print("task_id:", task_id)
with AppWorld(task_id=task_id, experiment_name="rcmf_install_check", load_ground_truth=True) as world:
    print(world.task.instruction[:200])
PY
```

## 6. 第一轮：纯代码 smoke test

这一步不需要 Qwen3-8B 权重，也不需要 AppWorld 数据。它验证 RCMF memory algebra、ledger、injector、mock 训练图。

```bash
python -m pytest -q
```

期望结果类似：

```text
12 passed
```

然后跑四个命令级 smoke：

```bash
python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records tests/fixtures/memory_records.jsonl \
  --output runs/smoke/memory.safetensors \
  --compiler hashing

python scripts/train.py \
  --config configs/base.yaml \
  --smoke \
  --output-dir runs/smoke

python scripts/run_ablations.py \
  --config configs/ablation/mvp.yaml \
  --dry-run \
  --output runs/smoke/ablation_jobs.json

python scripts/run_scaling.py \
  --config configs/base.yaml \
  --counts 0,10,100,1000 \
  --output runs/smoke/read_scaling.jsonl
```

这些命令会产生：

```text
runs/smoke/memory.safetensors
runs/smoke/memory_ledger/
runs/smoke/train/checkpoint.pt
runs/smoke/ablation_jobs.json
runs/smoke/read_scaling.jsonl
```

## 7. 准备 AppWorld 数据

确认 AppWorld 安装后，先从很小的 split 开始。当前 `prepare_appworld.py` 会读取配置里的 split 和 task_limit。为了只跑少量任务，可以先临时复制一份配置：

```bash
cp configs/benchmark/appworld.yaml configs/benchmark/appworld_smoke.yaml
```

把 `configs/benchmark/appworld_smoke.yaml` 中的：

```yaml
benchmark:
  task_limit: null
```

改成：

```yaml
benchmark:
  task_limit: 3
```

然后运行：

```bash
python scripts/prepare_appworld.py \
  --config configs/benchmark/appworld_smoke.yaml \
  --split train \
  --output runs/appworld/smoke_train
```

输出应该包括：

```text
runs/appworld/smoke_train/memory_records.jsonl
runs/appworld/smoke_train/decision_examples.jsonl
runs/appworld/smoke_train/splits.json
runs/appworld/smoke_train/resolved_config.yaml
```

如果这一步失败，优先检查：

```text
AppWorld 是否能 import
AppWorld 数据目录是否初始化
dataset 名称是否存在，例如 train/dev/test_normal
服务器是否允许创建 experiments/outputs
```

重要边界：这一步不是正式训练数据准备。正式训练需要完整 agent trajectory，并从每条
trajectory 生成 step-level 监督样本。第 `t` 步样本的输入应包含 system prompt、
query、以及第 `0..t-1` 步的 response/observation history；第 `t` 步 response 才是
训练目标。不能用 final answer fallback 来代替每步 response。

如果你已有形如 `docs/0a9d82a_1.json` 的完整 trace，使用：

```bash
python scripts/prepare_appworld_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --input /data/appworld_traces/train \
  --output runs/appworld/trace_train
```

然后正式训练应使用：

```bash
python scripts/train.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --data runs/appworld/trace_train \
  --output-dir runs/experiments/appworld_trace_mvp
```

## 8. 编译 AppWorld memory snapshot

先用 hashing smoke compiler 编译，这不是论文训练后的 RCMF compiler，但可以验证 Ledger、snapshot、删除/重建路径：

```bash
python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records runs/appworld/smoke_train/memory_records.jsonl \
  --output runs/appworld/smoke_memory.safetensors \
  --compiler hashing
```

输出：

```text
runs/appworld/smoke_memory.safetensors
runs/appworld/smoke_memory_ledger/
runs/appworld/smoke_memory.summary.json
runs/appworld/smoke_memory.config.yaml
```

检查 snapshot：

```bash
python - <<'PY'
from rcmf.memory.state import MemoryState
state = MemoryState.load("runs/appworld/smoke_memory.safetensors")
print(state.V.shape, state.c.shape, state.V.dtype, state.c.dtype)
PY
```

## 9. 当前训练入口怎么理解

可以直接跑的是 mock smoke 训练：

```bash
python scripts/train.py \
  --config configs/base.yaml \
  --smoke \
  --output-dir runs/smoke
```

这会验证：

```text
support episodes -> temporary V,c
query state -> b(s)
b(s), V,c -> z(s)
z(s) -> latent prefix
frozen mock LM -> action CE
checkpoint save/load surface
```

当前不应直接跑下面这个命令期待真实训练完成：

```bash
python scripts/train.py --config configs/base.yaml --data runs/appworld/smoke_train
```

原因：非 smoke 训练入口现在会明确抛出 `NotImplementedError`。还需要接入真实数据 tokenization/collate、checkpoint 中神经 compiler 的加载、以及服务器训练 launcher 配置。

真实训练完成后，预期服务器命令形态会是：

```bash
torchrun --nproc_per_node=8 scripts/train.py \
  --config configs/base.yaml \
  --data runs/appworld/prepared \
  --output-dir runs/train/appworld_mvp
```

或者用 Accelerate/DeepSpeed：

```bash
accelerate launch --num_processes 8 scripts/train.py \
  --config configs/base.yaml \
  --data runs/appworld/prepared \
  --output-dir runs/train/appworld_mvp
```

但这属于下一步实现工作，不是当前文件夹复制后即可完成的真实训练路径。

## 10. 当前评测入口怎么理解

`scripts/evaluate.py` 已经能搭 AppWorld adapter 和 Qwen backend，但当前还没有把训练好的 `state_encoder`、`injector` checkpoint 自动恢复接进去。因此有两种现实用法：

无记忆 AppWorld/Qwen 评测：

```bash
python scripts/evaluate.py \
  --config configs/base.yaml \
  --benchmark appworld \
  --split dev \
  --limit 3 \
  --no-memory \
  --output-dir runs/eval/no_memory_dev3
```

带 memory snapshot 的完整 RCMF 评测需要下一步补齐：

```text
load trained compiler/state_encoder/injector checkpoint
load memory snapshot V,c
each ReAct turn: render state -> state_encoder -> z -> prefix injector -> Qwen generate
```

换言之，现在的 `--memory-snapshot` 可以加载 `V,c`，但没有训练好的 state_encoder/injector checkpoint 时，它还不能代表正式 RCMF 结果。

## 11. Baseline 和 ablation

生成 baseline 计划：

```bash
python scripts/run_baselines.py \
  --config configs/base.yaml \
  --benchmark appworld \
  --methods no_memory,bm25,full_context,fast_weight,dense_rag,lora_memory,awm,ace \
  --memory-corpus runs/appworld/smoke_train/memory_records.jsonl \
  --dry-run
```

当前状态：

```text
ready:
  no_memory
  full_context
  bm25
  fast_weight

requires_official_impl:
  dense_rag
  lora_memory
  awm
  ace
  amem/mem0
```

生成 ablation 矩阵：

```bash
python scripts/run_ablations.py \
  --config configs/ablation/mvp.yaml \
  --dry-run \
  --output runs/ablations/mvp_jobs.json
```

## 12. 推荐的服务器执行顺序

第一次复制后，建议按这个顺序：

```bash
cd /data/projects/RCMF_codex
conda activate rcmf

python -m pytest -q

python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records tests/fixtures/memory_records.jsonl \
  --output runs/smoke/memory.safetensors \
  --compiler hashing

python scripts/train.py --config configs/base.yaml --smoke --output-dir runs/smoke

python scripts/run_scaling.py \
  --config configs/base.yaml \
  --counts 0,10,100,1000 \
  --output runs/smoke/read_scaling.jsonl

python - <<'PY'
from appworld import load_task_ids
print(load_task_ids("train")[:3])
PY

python scripts/prepare_appworld.py \
  --config configs/benchmark/appworld_smoke.yaml \
  --split train \
  --output runs/appworld/smoke_train

python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records runs/appworld/smoke_train/memory_records.jsonl \
  --output runs/appworld/smoke_memory.safetensors \
  --compiler hashing
```

如果这些都通过，说明服务器环境、AppWorld、RCMF core 和脚本路径都通了。

## 13. 常见问题

### `ModuleNotFoundError: No module named 'rcmf'`

请确认你在项目根目录运行命令：

```bash
pwd
ls rcmf scripts configs
```

然后重新安装 editable package：

```bash
python -m pip install -e ".[dev,server]"
```

### `ModuleNotFoundError: No module named 'appworld'`

AppWorld 没装进当前 conda 环境。安装官方 AppWorld package 后再验证：

```bash
python - <<'PY'
import appworld
print(appworld)
PY
```

### 安装后出现 `pydantic` 依赖冲突

如果你看到：

```text
appworld requires pydantic<2.0.0,>=1.9.0, but you have pydantic 2.x
sqlmodel requires pydantic<2.0.0,>=1.9.0, but you have pydantic 2.x
```

这通常是因为在 `appworld_env` 里安装了新版 DeepSpeed。处理方法：

```bash
python -m pip uninstall -y deepspeed pydantic pydantic-core annotated-types typing-inspection
python -m pip install -c constraints/appworld.txt \
  "pydantic>=1.9,<2" \
  "click==8.1.7" \
  "huggingface_hub>=0.36,<1" \
  "transformers>=4.57,<5"
python -m pip install -e ".[dev,server]"
python -m pip check
```

然后确认 AppWorld 没坏：

```bash
python - <<'PY'
import pydantic
from appworld import load_task_ids
print("pydantic:", pydantic.VERSION)
print("train sample:", load_task_ids("train")[:3])
PY
```

如果确实需要 DeepSpeed，请使用单独的 `rcmf_train` 环境安装
`.[dev,server,deepspeed]`，不要污染 `appworld_env`。

### Qwen 下载慢或重复下载

设置稳定缓存目录：

```bash
export HF_HOME=/data/hf_cache
export TRANSFORMERS_CACHE=/data/hf_cache/transformers
```

### CUDA OOM

先跑不加载 Qwen 的 smoke test。加载 Qwen 推理时可临时设置 `model.device_map: auto`。正式训练时需要用 FSDP/DeepSpeed，而不是 `device_map: auto`。

### `scripts/train.py` 非 smoke 模式不能跑

这是当前实现边界，不是环境问题。下一步需要实现真实 AppWorld `DecisionExample` tokenization/collate 和多 GPU trainer loop。

## 14. 下一步开发建议

服务器环境验证通过后，优先补这几块：

```text
1. AppWorld replay 生成逐步 state-action DecisionExample
2. tokenizer/collator: support/query batch -> tensors
3. train.py 非 smoke 训练循环
4. checkpoint 中恢复 compiler/state_encoder/injector
5. compile_memory.py 支持 neural checkpoint compiler
6. evaluate.py 接入 trained state_encoder + injector + memory snapshot
7. no-memory/BM25/full-context/fast-weight 的可执行 AppWorld 对照
```

等这些完成后，再扩展 EvoMemBench、MemoryAgentBench 和 AWM/ACE 等官方 baseline。
## 2026-07-30 Updated AppWorld Training Runbook

The current formal AppWorld path is trajectory supervision, not final-answer
fallback supervision.

1. Prepare train trajectories by replaying AppWorld train `compiled_solution`
ground truth:

```bash
python scripts/prepare_appworld_ground_truth_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --split train \
  --output runs/appworld/gt_train_compiled_<STAMP> \
  --experiment-name rcmf_gt_train_compiled_<STAMP> \
  --save-raw-traces
```

Do not use `api_calls` replay for formal training; those logs may contain stale
access tokens. Do not use `prepare_appworld.py` final-answer fallback as formal
training data.

2. Train with enough query length to preserve the current response target:

```bash
python scripts/train.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --data runs/appworld/gt_train_compiled_<STAMP> \
  --output-dir runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP> \
  --epochs 1 \
  --batch-size 1 \
  --grad-accumulation-steps 1 \
  --support-size 4 \
  --max-query-tokens 2048 \
  --save-every 100 \
  --log-every 10
```

3. Compile memory from the trained checkpoint before evaluation:

```bash
python scripts/compile_memory.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --records runs/appworld/gt_train_compiled_<STAMP>/memory_records.jsonl \
  --compiler checkpoint \
  --checkpoint runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP>/train/checkpoint.pt \
  --output runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP>/memory.safetensors \
  --ledger-dir runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP>/memory_ledger
```

4. Evaluate with `max_steps=50`. For first diagnostics, use `--limit 10`,
`--task-id`, or `--start-index`.

```bash
python scripts/evaluate.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --benchmark appworld \
  --split test \
  --limit 10 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --checkpoint runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP>/train/checkpoint.pt \
  --memory-snapshot runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP>/memory.safetensors \
  --output-dir runs/experiments/appworld_gt_train_compiled_<TRAIN_STAMP> \
  --experiment-name rcmf_appworld_test10_<EVAL_STAMP>
```

For the 2026-07-30 Lambda run, train data had 90 records and 931 per-step
examples. Target tokens with Qwen were p95=113, p99=227, max=395, so
`max_new_tokens=512` is enough for this dataset.
