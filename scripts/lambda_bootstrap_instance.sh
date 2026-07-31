#!/usr/bin/env bash
set -euo pipefail

PERSIST="${RCMF_PERSIST:-/lambda/nfs/rcmf-persist}"

if ! mountpoint -q "$PERSIST"; then
    echo "ERROR: persistent filesystem is not mounted at $PERSIST"
    exit 1
fi

mkdir -p \
    "$PERSIST/project" \
    "$PERSIST/data" \
    "$PERSIST/hf-cache/hub" \
    "$PERSIST/hf-cache/datasets" \
    "$PERSIST/hf-cache/assets" \
    "$PERSIST/cache/pip" \
    "$PERSIST/cache/torch" \
    "$PERSIST/runs/checkpoints" \
    "$PERSIST/runs/logs" \
    "$PERSIST/runs/results" \
    "$PERSIST/runs/tensorboard" \
    "$PERSIST/artifacts" \
    "$PERSIST/bootstrap" \
    "$PERSIST/secrets"

if [ ! -f "$PERSIST/env.sh" ]; then
    cat > "$PERSIST/env.sh" <<'EOF'
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

echo "Bootstrap complete: $RCMF_VENV"
