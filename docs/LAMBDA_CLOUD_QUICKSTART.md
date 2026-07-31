# Lambda Cloud 从零开始运行 RCMF

这份文档假设你新开了一台 Lambda Cloud Ubuntu GPU 实例，机器上没有现成的
AppWorld 数据、没有本项目 conda 环境，也还没有 Hugging Face/Qwen cache。

目标是先把项目跑到一个可靠的工程状态：

```text
1. RCMF core tests 通过
2. AppWorld 能 import、能读取 task ids、能打开一个 task
3. RCMF smoke 编译、mock training、scaling 能跑
4. AppWorld smoke 数据能准备出来
5. 用 hashing compiler 编译 AppWorld memory snapshot
```

注意：当前代码已经能做 smoke 和 AppWorld 数据 starter；真实 Qwen3-8B
RCMF 训练的非 smoke path 还没有完全接通。这个边界不是云环境问题。

## 0. 选择实例

为了少折腾，建议全程用 Lambda Cloud。

推荐：

```text
初始开发机:
  1x H100 SXM 80GB

后续正式训练:
  8x H100 SXM 80GB
```

如果只是验证 AppWorld 和 RCMF core，1x A100/H100 都可以。为了之后少换机器，
我建议直接用 1x H100 SXM 做开发机。

创建实例时选 Ubuntu / Lambda Stack 镜像即可。磁盘建议至少：

```text
filesystem / persistent storage: 500GB+
```

Qwen3-8B、AppWorld 数据、checkpoints 和 Hugging Face cache 会逐渐占空间。

## 1. 登录实例

```bash
ssh ubuntu@<lambda-instance-ip>
```

建议启动一个 tmux：

```bash
tmux new -s rcmf
```

之后如果断线：

```bash
tmux attach -t rcmf
```

## 2. 准备持久化目录

本项目建议把代码、数据、cache、日志和 checkpoint 都放在 Lambda filesystem：

```bash
export RCMF_PERSIST=/lambda/nfs/rcmf-persist
mountpoint -q "$RCMF_PERSIST"

mkdir -p "$RCMF_PERSIST"/{project,data,hf-cache/{hub,datasets,assets},cache/{pip,torch},runs/{checkpoints,logs,results,tensorboard},artifacts,bootstrap,secrets}
```

如果你从本地上传：

```powershell
$archive = Join-Path $env:TEMP "rcmf_project_upload.tar.gz"
if (Test-Path $archive) { Remove-Item $archive }

tar `
  --exclude='./runs' `
  --exclude='./data' `
  --exclude='./__pycache__' `
  --exclude='./.pytest_cache' `
  --exclude='*/__pycache__' `
  --exclude='*.pyc' `
  --exclude='./.env' `
  --exclude='./.env.*' `
  --exclude='./appworld_installation.txt' `
  -czf $archive `
  -C "C:\path\to\RCMF_codex" .

scp -i "$env:USERPROFILE\.ssh\lambda_rcmf" `
  $archive `
  ubuntu@<lambda-instance-ip>:/lambda/nfs/rcmf-persist/bootstrap/rcmf_project_upload.tar.gz
```

远端解压：

```bash
tar -xzf /lambda/nfs/rcmf-persist/bootstrap/rcmf_project_upload.tar.gz \
  -C /lambda/nfs/rcmf-persist/project
```

在云机器上：

```bash
cd /lambda/nfs/rcmf-persist/project
```

不要上传真实 API key。`appworld_installation.txt` 里如果有旧 key，上传前应删除或
rotate。

## 3. Python 3.11 venv 初始化

Lambda Stack 通常已经带 Python/CUDA/PyTorch，但默认 `python3` 可能是 Python
3.10。AppWorld 当前会用到 `typing.Self`，所以这个项目的 AppWorld 环境使用
Python 3.11+。

先准备 `env.sh`：

```bash
cat > /lambda/nfs/rcmf-persist/env.sh <<'EOF'
export RCMF_PERSIST=/lambda/nfs/rcmf-persist
export RCMF_PROJECT="$RCMF_PERSIST/project"
export RCMF_DATA="$RCMF_PERSIST/data"
export RCMF_RUNS="$RCMF_PERSIST/runs"
export RCMF_CHECKPOINTS="$RCMF_RUNS/checkpoints"
export RCMF_LOGS="$RCMF_RUNS/logs"
export RCMF_RESULTS="$RCMF_RUNS/results"
export RCMF_TENSORBOARD="$RCMF_RUNS/tensorboard"
export RCMF_ARTIFACTS="$RCMF_PERSIST/artifacts"
export RCMF_BOOTSTRAP="$RCMF_PERSIST/bootstrap"

export HF_HOME="$RCMF_PERSIST/hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export HF_ASSETS_CACHE="$HF_HOME/assets"

export PIP_CACHE_DIR="$RCMF_PERSIST/cache/pip"
export TORCH_HOME="$RCMF_PERSIST/cache/torch"

export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

export RCMF_VENV=/home/ubuntu/venvs/rcmf-py311
export RCMF_LOCAL_SCRATCH=/tmp/rcmf-$USER
mkdir -p "$RCMF_LOCAL_SCRATCH"
EOF

source /lambda/nfs/rcmf-persist/env.sh
```

安装 Python 3.11 并创建 venv：

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential git git-lfs tmux rsync unzip

mkdir -p "$(dirname "$RCMF_VENV")"
if [ ! -x "$RCMF_VENV/bin/python" ]; then
  python3.11 -m venv "$RCMF_VENV"
fi
source "$RCMF_VENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel
```

项目上传后，也可以直接把维护好的 bootstrap 脚本复制到 persistent bootstrap 目录：

```bash
cp "$RCMF_PROJECT/scripts/lambda_bootstrap_instance.sh" "$RCMF_BOOTSTRAP/bootstrap_instance.sh"
chmod +x "$RCMF_BOOTSTRAP/bootstrap_instance.sh"
```

## 4. 安装 PyTorch

先检查系统 CUDA/驱动：

```bash
nvidia-smi
```

如果 Lambda 镜像里已有可用 PyTorch，可以先试：

```bash
python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available(), torch.cuda.device_count())
PY
```

如果当前 venv 没有 torch，Lambda 当前 H100/新驱动优先试 CUDA 12.8 wheel：

```bash
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

## 5. 安装 Rust

AppWorld 某些依赖可能需要 Rust：

```bash
rustc --version || true
```

如果没有：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustc --version
```

## 6. 安装 RCMF

在项目根目录：

```bash
cd "$RCMF_PROJECT"
python -m pip install -e ".[dev,server]"
```

这里不会安装 DeepSpeed，原因是 AppWorld 依赖 `pydantic<2`，新版 DeepSpeed 会拉
`pydantic>=2`。后续如果要 DeepSpeed，单独建 `rcmf_train` 环境。

## 7. 安装 AppWorld

先安装 AppWorld 和兼容约束：

```bash
python -m pip install appworld
python -m pip install -c constraints/appworld.txt \
  "pydantic>=1.9,<2" \
  "click==8.1.7" \
  "huggingface_hub>=0.36,<1" \
  "transformers>=4.57,<5"
python -m pip check
```

如果需要官方 AppWorld baseline，再 clone 官方仓库：

```bash
cd ~/projects
git clone https://github.com/StonyBrookNLP/appworld.git appworld-main
cd appworld-main
python -m pip install -e .
python -m pip install -c /lambda/nfs/rcmf-persist/project/constraints/appworld.txt \
  "pydantic>=1.9,<2" \
  "click==8.1.7" \
  "huggingface_hub>=0.36,<1" \
  "transformers>=4.57,<5"
```

RCMF 自己的 AppWorld adapter 不要求你先跑官方 baseline；先装 pip 包和数据即可。

## 8. 初始化 AppWorld 代码和数据

先试正常命令：

```bash
cd "$RCMF_PROJECT"
appworld install
appworld download data
```

不要把 `$RCMF_PROJECT/data` 做成 symlink。AppWorld downloader 会先删除并重建
`./data`；使用真实目录即可，因为 `$RCMF_PROJECT` 本身已经在 persistent filesystem
上。

如果 `appworld download data` 遇到 SSL verify 报错，再用 fallback：

```bash
cd "$RCMF_PROJECT"
python scripts/download_appworld_data_no_ssl.py
```

验证 AppWorld：

```bash
python - <<'PY'
from appworld import AppWorld, load_task_ids
task_id = load_task_ids("train")[0]
print("task_id:", task_id)
with AppWorld(task_id=task_id, experiment_name="rcmf_lambda_check", load_ground_truth=True) as world:
    print(world.task.instruction[:300])
PY
```

## 9. 一键环境检查

回到 RCMF 项目根目录：

```bash
cd "$RCMF_PROJECT"
python scripts/check_environment.py --require-gpu --require-appworld-data --run-pytest
```

如果只想快速看版本，不跑 pytest：

```bash
python scripts/check_environment.py --require-gpu --require-appworld-data
```

## 10. 跑 RCMF core smoke

```bash
python -m pytest -q

python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records tests/fixtures/memory_records.jsonl \
  --output runs/smoke/memory.safetensors \
  --compiler hashing

python scripts/train.py \
  --config configs/base.yaml \
  --smoke \
  --output-dir runs/smoke

python scripts/run_scaling.py \
  --config configs/base.yaml \
  --counts 0,10,100,1000 \
  --output runs/smoke/read_scaling.jsonl
```

## 11. 准备 AppWorld smoke 数据

项目已经提供了 `configs/benchmark/appworld_smoke.yaml`，不需要手动复制/改 YAML：

```bash
python scripts/prepare_appworld.py \
  --config configs/benchmark/appworld_smoke.yaml \
  --split train \
  --output runs/appworld/smoke_train
```

检查输出：

```bash
ls -lh runs/appworld/smoke_train
head -n 1 runs/appworld/smoke_train/memory_records.jsonl
head -n 1 runs/appworld/smoke_train/decision_examples.jsonl
```

注意：`scripts/prepare_appworld.py` 只能从 AppWorld task/ground-truth 字段准备
memory records 和字段探测结果；如果 AppWorld 数据里没有逐步 trajectory，它不能
生成正式训练所需的 step-level action labels。正式训练不能把 final answer 当作
每步 response 的 fallback target。

正式 AppWorld 训练数据应来自完整 agent trajectory。每个 step 的训练样本定义为：

```text
input:
  system prompt
  query
  response/observation history up to step t-1

target:
  response at step t
```

对于形如 `docs/0a9d82a_1.json` 的 trace 文件，使用：

```bash
python scripts/prepare_appworld_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --input /path/to/trace_json_or_directory \
  --output runs/appworld/trace_train
```

输出：

```text
runs/appworld/trace_train/memory_records.jsonl
runs/appworld/trace_train/decision_examples.jsonl
runs/appworld/trace_train/summary.json
```

## 12. 编译 AppWorld memory snapshot

```bash
python scripts/compile_memory.py \
  --config configs/base.yaml \
  --records runs/appworld/smoke_train/memory_records.jsonl \
  --output runs/appworld/smoke_memory.safetensors \
  --compiler hashing
```

验证：

```bash
python - <<'PY'
from rcmf.memory.state import MemoryState
state = MemoryState.load("runs/appworld/smoke_memory.safetensors")
print(state.V.shape, state.c.shape, state.V.dtype, state.c.dtype)
PY
```

## 13. Hugging Face / Qwen3-8B

建议设置 cache：

```bash
source /lambda/nfs/rcmf-persist/env.sh
echo "$HF_HOME"
```

如果要让新 shell 自动加载：

```bash
grep -qxF 'source /lambda/nfs/rcmf-persist/env.sh' ~/.bashrc \
  || echo 'source /lambda/nfs/rcmf-persist/env.sh' >> ~/.bashrc
```

验证 tokenizer：

```bash
python - <<'PY'
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("Qwen/Qwen3-8B", trust_remote_code=True)
print(type(tok).__name__)
PY
```

加载模型会占用显存：

```bash
python - <<'PY'
from rcmf.config import load_config
from rcmf.factory import build_backend
cfg = load_config("configs/base.yaml")
backend = build_backend(cfg, load_model=True)
print("loaded", cfg.model.name, "on", backend.device)
PY
```

## 14. 当前不能误解的地方

下面可以跑：

```text
pytest
compile_memory hashing smoke
train.py --smoke
run_scaling
prepare_appworld smoke
AppWorld memory snapshot hashing compile
```

下面还不是完整论文训练：

```bash
python scripts/train.py --config configs/base.yaml --data runs/appworld/smoke_train
```

它会提示真实 dataset tokenization/collate 和多 GPU trainer loop 还要继续实现。

后续要补齐：

```text
1. AppWorld ground-truth replay -> step-level DecisionExample
2. tokenization/collator
3. train.py 非 smoke 训练循环
4. checkpoint 恢复 compiler/state_encoder/injector
5. compile_memory.py 支持 neural checkpoint compiler
6. evaluate.py 接 trained state_encoder + injector + memory snapshot
```

## 15. 建议保留的目录结构

```text
/lambda/nfs/rcmf-persist/project
/lambda/nfs/rcmf-persist/data
/lambda/nfs/rcmf-persist/hf-cache
/lambda/nfs/rcmf-persist/runs
~/projects/appworld-main           # 可选，只有官方 baseline 需要
```

`runs/` 会越来越大，建议保留在 Lambda filesystem 上，不要每次传回本地。
## 2026-07-30 Updated AppWorld Official Trajectory Runbook

Use this path on Lambda Cloud for the current RCMF AppWorld experiment. Do not
train from final-answer fallback data, the single example trace in
`docs/0a9d82a_1.json`, or synthesized `compiled_solution` replay data.

The official source for supervised AppWorld trajectories is the downloaded
AppWorld experiment output:

```text
experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train
```

This directory is produced by:

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli install
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download data --root .
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download experiment-outputs --root .
```

The current Lambda setup uses AppWorld `0.2.0.dev0` and data `0.2.0`. If an old
`data/version.txt` says `0.1.0`, move it aside before downloading the current
data, for example:

```bash
cd /lambda/nfs/rcmf-persist/project
mv data data_appworld_0.1.0_backup_20260730
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download data --root .
```

Prepare official per-step train data from `environment_io.md`. By default this
keeps only trajectories whose per-task `evaluation/report.md` has
`Num Failed Tests : 0`.

```bash
cd /lambda/nfs/rcmf-persist/project

/home/ubuntu/venvs/rcmf-py311/bin/python scripts/prepare_appworld_official_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --experiment-output experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train \
  --dataset-name train \
  --output runs/appworld/official_react_gpt4o_train_success_<STAMP>
```

For the 2026-07-30 Lambda run, this produced 47 successful official train
trajectories and 710 per-step examples out of 90 candidate train tasks. The
candidate official trajectories contain 1981 total steps, but 43 tasks have
failed tests and are skipped unless `--include-failed` is explicitly passed.

Target token stats with the Qwen3-8B tokenizer on this official-success set:
median=26, p95=91, p99=159, max=186. `max_new_tokens=512` is enough for the
first evaluation runs. Do not shorten generation length just to hide slow
generation; if 512-token generation is slow, first inspect attention kernels and
KV cache behavior.

State/history strings can be very long because observations contain full API
responses. `--max-query-tokens`, `encoder.max_state_tokens`, and
`encoder.max_experience_tokens` all truncate text during training batch
construction. Any formal experiment that truncates, filters, compresses,
samples, or summarizes trajectory data must be approved first. The 2026-07-30
run with `--max-query-tokens 4096` should be treated as a truncated-context
diagnostic run, not a full-trajectory training result.

Generation speed note: default PyTorch SDPA on Qwen3-8B long-context generation
was about 3 tokens/s on the Lambda H100. `rcmf/model/backends/hf_qwen.py` now
forces PyTorch Flash Attention during CUDA generation when available; a
3533-token prompt + 512-token generation improved from about 144s to about
10.4s.

Train:

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

For a persistent run, use `tmux list-sessions` rather than relying on `tmux ls`:

```bash
tmux new-session -d -s rcmf_official_train_<STAMP> \
  "bash -lc 'cd /lambda/nfs/rcmf-persist/project; \
  /home/ubuntu/venvs/rcmf-py311/bin/python scripts/train.py \
    --config configs/benchmark/appworld_mvp_experiment.yaml \
    --data runs/appworld/official_react_gpt4o_train_success_<STAMP> \
    --output-dir runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP> \
    --epochs 1 --batch-size 1 --grad-accumulation-steps 1 \
    --support-size 4 --max-query-tokens 4096 --save-every 100 --log-every 10 \
    2>&1 | tee /lambda/nfs/rcmf-persist/runs/logs/rcmf_official_train_<TRAIN_STAMP>.log'"
```

Compile the trained memory snapshot before evaluation:

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/compile_memory.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --records runs/appworld/official_react_gpt4o_train_success_<STAMP>/memory_records.jsonl \
  --compiler checkpoint \
  --checkpoint runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/train/checkpoint.pt \
  --output runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/memory.safetensors \
  --ledger-dir runs/experiments/appworld_official_react_gpt4o_train_<TRAIN_STAMP>/memory_ledger
```

Evaluate with `max_steps=50`; use `--limit 10` for the first `test_normal` slice.

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

`scripts/prepare_appworld_ground_truth_traces.py` is now a diagnostic helper
only. Its `compiled_solution` mode requires `--allow-compiled-solution-replay`
and should not be used as the formal training source for this experiment.
