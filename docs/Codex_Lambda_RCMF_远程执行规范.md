# Codex 远程使用 Lambda Cloud RCMF 实例的执行规范

> 本文用于让 Codex 安全地接入用户已经创建的 Lambda Cloud Ubuntu GPU 环境，完成代码上传、安装、测试、调试和运行。  
> 核心目标：所有重要文件跨 Terminate / Launch 保留。

---

## 1. 环境事实

```text
Cloud provider: Lambda Cloud
Remote OS: Ubuntu / Lambda Stack
SSH user: ubuntu
GPU: 单卡 H100 80GB SXM5（当前实例；以后可能更换）
Persistent filesystem mount: /lambda/nfs/rcmf-persist
Python: AppWorld 需要 Python 3.11+；Lambda Stack 默认 python3 可能是 Python 3.10
```

实例Public IP在Terminate后失效，新实例通常获得新IP。每次连接前必须让用户从Lambda控制台确认。

填写：

```text
REMOTE_HOST=<当前Public IP>
REMOTE_USER=ubuntu
REMOTE_MOUNT=/lambda/nfs/rcmf-persist
```

Windows本地私钥通常位于：

```text
C:\Users\<用户名>\.ssh\lambda_rcmf
```

**禁止读取、上传、复制、打印或提交私钥内容。** 只能把私钥路径作为`ssh`/`scp`参数。

---

## 2. 最高优先级规则

1. 所有重要文件必须位于：

   ```text
   /lambda/nfs/rcmf-persist
   ```

2. 任何下载、上传或训练之前必须确认：

   ```bash
   mountpoint -q /lambda/nfs/rcmf-persist
   ```

3. 挂载检查失败时立即停止。
4. 不得把模型、数据、checkpoint或唯一代码副本只放在`/home/ubuntu`、`/tmp`或根盘。
5. 不得擅自Terminate、Restart、shutdown或poweroff。
6. 不得擅自修改Lambda firewall。
7. 不得对整个filesystem执行`rm -rf`、`chmod -R 777`或不受控清理。
8. 不得把token、API key、SSH key或`.env`提交Git或打印到日志。
9. 长任务必须输出到持久化日志，并通过tmux运行。
10. 完成后报告修改、测试、路径、未解决问题及仍在运行的进程。

---

## 3. 持久化目录契约

```text
/lambda/nfs/rcmf-persist/
├── project/
├── data/
├── hf-cache/
│   ├── hub/
│   ├── datasets/
│   └── assets/
├── cache/
│   ├── pip/
│   └── torch/
├── runs/
│   ├── checkpoints/
│   ├── logs/
│   ├── results/
│   └── tensorboard/
├── artifacts/
├── bootstrap/
├── secrets/
└── env.sh
```

| 路径 | 用途 |
|---|---|
| `project` | RCMF Git仓库和源码 |
| `data` | AppWorld、EvoMemBench、MemoryAgentBench等数据 |
| `hf-cache` | Hugging Face模型和数据缓存 |
| `cache/pip` | pip下载缓存 |
| `cache/torch` | PyTorch Hub缓存 |
| `runs/checkpoints` | checkpoint |
| `runs/logs` | 文本日志 |
| `runs/results` | JSON、CSV等结果 |
| `runs/tensorboard` | TensorBoard events |
| `artifacts` | 导出图表、压缩包和交付物 |
| `bootstrap` | 新实例初始化脚本 |
| `secrets` | 可选敏感配置；不入Git |
| `env.sh` | 环境变量入口 |

---

## 4. 第一次连接后的强制预检

本地连接：

```powershell
ssh -i "$env:USERPROFILE\.ssh\lambda_rcmf" ubuntu@<REMOTE_HOST>
```

远程先执行：

```bash
set -e

nvidia-smi

test -d /lambda/nfs/rcmf-persist
mountpoint -q /lambda/nfs/rcmf-persist

touch /lambda/nfs/rcmf-persist/.codex_write_test
rm /lambda/nfs/rcmf-persist/.codex_write_test

echo "Persistent filesystem is mounted and writable."
```

任何一步失败：

- 不上传代码；
- 不下载模型；
- 不安装项目；
- 不训练；
- 向用户报告挂载问题。

---

## 5. 初始化Persistent Filesystem

如果目录尚未建立：

```bash
export RCMF_PERSIST=/lambda/nfs/rcmf-persist

mkdir -p "$RCMF_PERSIST"/{project,data,hf-cache/{hub,datasets,assets},cache/{pip,torch},runs/{checkpoints,logs,results,tensorboard},artifacts,bootstrap,secrets}
```

若`env.sh`不存在：

```bash
cat > "$RCMF_PERSIST/env.sh" <<'EOF'
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
```

加载：

```bash
source /lambda/nfs/rcmf-persist/env.sh
```

如果`env.sh`已存在，先读取比较，只做必要的最小修改，不得直接覆盖。
如果旧实例已经创建了 `/home/ubuntu/venvs/rcmf`，先检查它的 Python 版本；若是
Python 3.10，不要继续在该 venv 内安装 AppWorld，改建
`/home/ubuntu/venvs/rcmf-py311`，并把 `RCMF_VENV` 更新到新路径。

---

## 6. 新实例bootstrap

系统包和Python venv是可重建的本地状态。

若脚本存在：

```text
/lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh
```

运行：

```bash
source /lambda/nfs/rcmf-persist/env.sh
bash "$RCMF_BOOTSTRAP/bootstrap_instance.sh"
```

项目内也维护了一份同内容脚本：

```bash
cp /lambda/nfs/rcmf-persist/project/scripts/lambda_bootstrap_instance.sh \
  /lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh
chmod +x /lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh
```

若不存在，创建：

```bash
cat > /lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

PERSIST=/lambda/nfs/rcmf-persist

if ! mountpoint -q "$PERSIST"; then
    echo "ERROR: persistent filesystem is not mounted"
    exit 1
fi

source "$PERSIST/env.sh"

sudo apt-get update
sudo apt-get install -y \
    git \
    git-lfs \
    tmux \
    htop \
    rsync \
    unzip \
    build-essential \
    python3.11 \
    python3.11-venv \
    python3.11-dev

git lfs install

mkdir -p "$(dirname "$RCMF_VENV")"
if [ ! -x "$RCMF_VENV/bin/python" ]; then
    python3.11 -m venv "$RCMF_VENV"
fi
source "$RCMF_VENV/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python - <<'PY' || python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
import torch
raise SystemExit(0 if torch.cuda.is_available() else 1)
PY

grep -qxF 'source /lambda/nfs/rcmf-persist/env.sh' ~/.bashrc \
  || echo 'source /lambda/nfs/rcmf-persist/env.sh' >> ~/.bashrc
EOF

chmod +x /lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh
```

运行后：

```bash
source /lambda/nfs/rcmf-persist/env.sh
source "$RCMF_VENV/bin/activate"

python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
PY
```

### PyTorch约束

不得在未检查时升级或替换Lambda镜像自带的PyTorch/CUDA。若requirements将覆盖PyTorch，先报告风险并确认兼容性。

---

## 7. 上传代码

### 首选：Git

```bash
source /lambda/nfs/rcmf-persist/env.sh

if [ -d "$RCMF_PROJECT/.git" ]; then
    cd "$RCMF_PROJECT"
    git status
    git pull --ff-only
else
    git clone <REPOSITORY_URL> "$RCMF_PROJECT"
fi
```

如果远程目录存在未提交改动，不得强制reset或覆盖。

### Windows直接上传

```powershell
scp -i "$env:USERPROFILE\.ssh\lambda_rcmf" -r `
  "C:\path\to\rcmf-project\*" `
  ubuntu@<REMOTE_HOST>:/lambda/nfs/rcmf-persist/project/
```

### Codex推荐：排除缓存和敏感文件后归档上传

当本地目录包含 `runs/`、`__pycache__/`、`.pytest_cache/`、`.env*` 或带密钥记录的
安装笔记时，优先创建临时 `tar.gz` 后上传，再在远端持久化 project 目录解压。这样
避免把运行产物、缓存和敏感文件带到云端。

本地 PowerShell 示例：

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
  ubuntu@<REMOTE_HOST>:/lambda/nfs/rcmf-persist/bootstrap/rcmf_project_upload.tar.gz
```

远端解压前仍必须检查 project 目录；如果 project 中已有未提交或唯一文件，停止并询问
用户。空目录或确认可覆盖的目录才可执行：

```bash
tar -xzf /lambda/nfs/rcmf-persist/bootstrap/rcmf_project_upload.tar.gz \
  -C /lambda/nfs/rcmf-persist/project
```

覆盖式同步前必须检查：

```bash
cd /lambda/nfs/rcmf-persist/project
git status --short
```

发现远程未提交改动时停止并询问用户。

---

## 8. 安装项目依赖

```bash
source /lambda/nfs/rcmf-persist/env.sh
source "$RCMF_VENV/bin/activate"
cd "$RCMF_PROJECT"
```

先检查：

```bash
ls -lah
find . -maxdepth 2 -type f | sort | sed -n '1,240p'
```

按优先级：

1. README指定的安装命令；
2. `pip install -e .`；
3. `pip install -r requirements.txt`；
4. 项目自带setup脚本。

安装后：

```bash
python --version
pip --version
pip freeze > "$RCMF_ARTIFACTS/pip-freeze-latest.txt"
```

CUDA检查：

```bash
python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.version.cuda)
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
PY
```

---

## 9. 修改代码的规则

1. 优先最小范围修改。
2. 不随意改现有变量名、类名、函数名和格式。
3. 新增路径配置时优先读取环境变量，不硬编码`/home/ubuntu`。
4. 推荐：

```python
import os
from pathlib import Path

PERSIST = Path(os.environ.get("RCMF_PERSIST", "/lambda/nfs/rcmf-persist"))
PROJECT = Path(os.environ.get("RCMF_PROJECT", PERSIST / "project"))
DATA = Path(os.environ.get("RCMF_DATA", PERSIST / "data"))
CHECKPOINTS = Path(os.environ.get("RCMF_CHECKPOINTS", PERSIST / "runs/checkpoints"))
LOGS = Path(os.environ.get("RCMF_LOGS", PERSIST / "runs/logs"))
RESULTS = Path(os.environ.get("RCMF_RESULTS", PERSIST / "runs/results"))
```

5. 正式训练入口必须验证mount：

```python
import subprocess
subprocess.run(["mountpoint", "-q", str(PERSIST)], check=True)
```

6. checkpoint、评测结果和配置写入持久化路径。
7. 启动训练时保存resolved config、Git commit、seed和环境信息。
8. 不把model cache或dataset提交Git。

---

## 10. 调试顺序

不得直接启动完整训练。

### A. 静态检查

```bash
python -m compileall -q .
```

### B. 单元测试

```bash
pytest -q
```

### C. 定向测试

优先：

- MemoryState add/remove；
- Ledger rebuild；
- prefix injector；
- Qwen backbone冻结；
- 单batch forward；
- 单batch backward；
- checkpoint save/load。

### D. 模型smoke test

确认Qwen3-8B从持久化HF cache加载。

### E. Benchmark smoke test

先运行：

- 1道AppWorld；
- 3–5道小规模任务；
- 再运行完整split。

### F. 训练smoke test

只跑少量step。命令按项目配置系统调整，例如：

```bash
python scripts/train.py \
  --config configs/train/appworld_rcmf.yaml \
  trainer.max_steps=5
```

---

## 11. 运行长任务

使用tmux，并把日志写入Filesystem：

```bash
source /lambda/nfs/rcmf-persist/env.sh

tmux new-session -d -s rcmf_train \
  "bash -lc '
    source /lambda/nfs/rcmf-persist/env.sh
    source /home/ubuntu/venvs/rcmf-py311/bin/activate
    cd /lambda/nfs/rcmf-persist/project
    python scripts/train.py \
      --config configs/train/appworld_rcmf.yaml \
      2>&1 | tee /lambda/nfs/rcmf-persist/runs/logs/appworld_rcmf.log
  '"
```

检查：

```bash
tmux ls
tmux capture-pane -pt rcmf_train | tail -100
tail -100 "$RCMF_LOGS/appworld_rcmf.log"
nvidia-smi
```

SSH命令返回不代表训练成功，必须检查进程、日志和GPU。

---

## 12. 模型和数据缓存

下载前：

```bash
source /lambda/nfs/rcmf-persist/env.sh
echo "$HF_HOME"
echo "$HF_HUB_CACHE"
echo "$HF_DATASETS_CACHE"
```

输出必须位于`/lambda/nfs/rcmf-persist/...`。

若代码显式传`cache_dir`，应使用相同持久化路径，不能覆盖为`~/.cache/huggingface`。

AppWorld pip 包默认使用当前 root 下的 `data/`，并且 `appworld download data` 会先
删除并重建这个目录。不要把 `/lambda/nfs/rcmf-persist/project/data` 做成 symlink；
应使用真实目录。因为 project 本身位于 persistent filesystem，所以
`/lambda/nfs/rcmf-persist/project/data` 仍然会跨实例保留。

---

## 13. 结果和日志

每次实验建议：

```text
/lambda/nfs/rcmf-persist/runs/
├── checkpoints/<experiment_name>/
├── logs/<experiment_name>/
├── results/<experiment_name>/
└── tensorboard/<experiment_name>/
```

实验名至少包含：

```text
method_benchmark_seed_timestamp
```

例如：

```text
rcmf_appworld_seed1_20260729_180000
```

保存：

- resolved YAML；
- 命令行；
- Git commit；
- pip freeze；
- GPU信息；
- stdout/stderr；
- checkpoint；
- final metrics；
- per-task JSON；
- wall-clock和显存指标。

---

## 14. 下载结果

Windows：

```powershell
scp -i "$env:USERPROFILE\.ssh\lambda_rcmf" -r `
  ubuntu@<REMOTE_HOST>:/lambda/nfs/rcmf-persist/runs/results/<EXPERIMENT_NAME> `
  "C:\path\to\local-results\"
```

可压缩：

```bash
tar -czf \
  "$RCMF_ARTIFACTS/<EXPERIMENT_NAME>.tar.gz" \
  -C "$RCMF_RESULTS" \
  "<EXPERIMENT_NAME>"
```

---

## 15. Secrets

允许放在：

```text
/lambda/nfs/rcmf-persist/secrets/
```

权限：

```bash
chmod 700 /lambda/nfs/rcmf-persist/secrets
chmod 600 /lambda/nfs/rcmf-persist/secrets/*
```

`.gitignore`至少包含：

```gitignore
.env
*.pem
*.key
secrets/
hf-cache/
data/
runs/
artifacts/
```

不得读取或回显已有secret值。只检查是否存在：

```bash
test -n "${HF_TOKEN:-}" && echo "HF_TOKEN is set"
```

禁止：

```bash
echo "$HF_TOKEN"
```

---

## 16. 禁止操作

未经用户明确授权，禁止：

```bash
rm -rf /lambda/nfs/rcmf-persist
sudo shutdown
sudo poweroff
sudo reboot
```

也禁止：

- Lambda控制台/API Terminate；
- 删除filesystem；
- 修改付款、实例类型或firewall；
- 强制覆盖远程未提交代码；
- 清空模型cache；
- 删除checkpoint；
- 公网开放Jupyter/TensorBoard；
- 安装来源不明的系统级脚本；
- 把token写入源码。

---

## 17. Terminate前的交接

Codex完成工作后向用户提供：

```text
Remote host:
Project path:
Git branch / commit:
Modified files:
Commands executed:
Tests passed:
Tests failed:
Active tmux sessions:
Active training processes:
Checkpoint path:
Log path:
Result path:
Filesystem usage:
Known issues:
Safe to terminate now: yes/no
```

并执行只读检查：

```bash
tmux ls || true
ps -u "$USER" -f
nvidia-smi
du -sh /lambda/nfs/rcmf-persist
git -C /lambda/nfs/rcmf-persist/project status --short
```

不得自行Terminate；由用户在控制台执行。

---

## 18. 每次新实例的最短恢复

```bash
nvidia-smi
mountpoint -q /lambda/nfs/rcmf-persist

source /lambda/nfs/rcmf-persist/env.sh
bash /lambda/nfs/rcmf-persist/bootstrap/bootstrap_instance.sh

source /home/ubuntu/venvs/rcmf-py311/bin/activate
cd /lambda/nfs/rcmf-persist/project

pip install -r requirements.txt
# 或按README使用pip install -e .

pytest -q
```

---

## 19. 推荐远程命令模板

只读GPU检查：

```powershell
ssh -i "$env:USERPROFILE\.ssh\lambda_rcmf" ubuntu@<REMOTE_HOST> `
  "source /lambda/nfs/rcmf-persist/env.sh && nvidia-smi"
```

运行测试：

```powershell
ssh -i "$env:USERPROFILE\.ssh\lambda_rcmf" ubuntu@<REMOTE_HOST> `
  "bash -lc '
    source /lambda/nfs/rcmf-persist/env.sh
    source /home/ubuntu/venvs/rcmf-py311/bin/activate
    cd /lambda/nfs/rcmf-persist/project
    pytest -q
  '"
```

查看日志：

```powershell
ssh -i "$env:USERPROFILE\.ssh\lambda_rcmf" ubuntu@<REMOTE_HOST> `
  "tail -100 /lambda/nfs/rcmf-persist/runs/logs/appworld_rcmf.log"
```

---

## 20. 成功标准

- Filesystem已挂载且可写；
- 项目位于`.../project`；
- HF缓存位于`.../hf-cache`；
- checkpoint、日志、结果位于`.../runs`；
- 新实例可通过bootstrap恢复；
- pytest或smoke test可运行；
- Qwen识别GPU；
- 没有唯一重要文件只在实例本地；
- 用户可安全Terminate并在下一次Launch继续。

---

## 21. 2026-07-30 AppWorld 官方 Trajectory 实测更新

本项目当前正式训练数据源应使用 AppWorld 官方下载的 train trajectory 输出：

```text
/lambda/nfs/rcmf-persist/project/experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train
```

不要用以下内容作为正式训练数据：

- `docs/0a9d82a_1.json` 单条示例 trace；
- final-answer fallback 数据；
- 从 `compiled_solution.py` 拆出来的 synthetic step replay。

实际可执行的数据准备命令：

```bash
cd /lambda/nfs/rcmf-persist/project

/home/ubuntu/venvs/rcmf-py311/bin/python scripts/prepare_appworld_official_traces.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --experiment-output experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train \
  --dataset-name train \
  --output runs/appworld/official_react_gpt4o_train_success_<STAMP>
```

2026-07-30 实测结果：90 个 train candidate task 中，47 个官方 GPT-4o ReAct trajectory 的 per-task report 全通过，共 710 个逐步监督样本；其余 43 个失败轨迹默认跳过。

如果 AppWorld 代码版本为 `0.2.0.dev0`，`data/version.txt` 必须是 `0.2.0`。若远端仍是旧 `0.1.0` 数据，先移动备份再下载：

```bash
cd /lambda/nfs/rcmf-persist/project
mv data data_appworld_0.1.0_backup_20260730
/home/ubuntu/venvs/rcmf-py311/bin/python -m appworld.cli download data --root .
```

当前训练建议：

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/train.py \
  --config configs/benchmark/appworld_mvp_experiment.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_<STAMP> \
  --output-dir runs/experiments/appworld_qwen_repr_official_react_gpt4o_train_<TRAIN_STAMP> \
  --epochs 1 \
  --batch-size 1 \
  --grad-accumulation-steps 1 \
  --support-mode all_except_current_task \
  --representation-batch-size 1 \
  --save-every 100 \
  --log-every 10
```

远端 `tmux ls` 在当前实例上返回 `unknown command: ls`，监控 session 时使用：

```bash
tmux list-sessions
```

更完整的命令记录见 `docs/AppWorld_官方Trajectory训练说明.md`。

### 21.1 数据完整性与生成速度注意事项

- 不要未经确认就截断、过滤、压缩、采样或 summary 化 trajectory 训练数据。
- 当前默认 `encoder.type=qwen_hidden`：memory record 和当前 state 都经过冻结 Qwen3-8B，取最后 hidden representation 后再进入 RCMF；memory record representations 离线缓存在训练输出的 `train/representation_cache/`。
- 如果 memory record 超过 Qwen 上下文窗口，代码会在 token-id 层分成多个不重叠 chunk；每个 chunk 都过冻结 Qwen 并参与 memory bank。state text 使用同样的 chunk 编码后 mean pooling。
- 正式训练默认 `--support-mode all_except_current_task`，即使用除当前 task 外的全部 `memory_records.jsonl` 作为 memory bank；长 record 会展开为多个 representation chunk；`--support-size 4` 只可用于单独的采样诊断实验。
- `--max-query-tokens`、`encoder.max_state_tokens`、`encoder.max_experience_tokens` 不再静默截断文本；如果显式设置且超限，代码会直接报错。
- target 会追加 tokenizer EOS，训练 labels 会把 prompt token 置为 `-100`，只有当前 step 的 response/action target 参与 loss。
- 不要通过降低 `max_new_tokens` 来回避生成慢。AppWorld 测试应保留足够生成长度，例如当前诊断使用 `max_new_tokens=512`、`max_steps=50`。
- 2026-07-30 实测慢生成根因是默认 PyTorch SDPA 在 Qwen3-8B 长上下文 generation 上没有走到高效路径。`rcmf/model/backends/hf_qwen.py` 已在 CUDA generation 时优先强制 PyTorch Flash Attention。
