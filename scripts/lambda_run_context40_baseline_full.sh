#!/usr/bin/env bash
set -Eeuo pipefail

PERSIST="${RCMF_PERSIST:-/lambda/nfs/rcmf-persist}"
PROJECT="${PROJECT:-$PERSIST/project}"
PYTHON="${PYTHON:-/home/ubuntu/venvs/rcmf-py311/bin/python}"
STAMP="${1:-$(date +%Y%m%d_%H%M%S)}"
CONFIG="${BASELINE_CONFIG:-configs/baseline/appworld_qwen_full_prompt_context40.yaml}"
OUT="runs/experiments/qwen_appworld_full_prompt_context40_newline_full_${STAMP}"
EXP="qwen_appworld_full_prompt_context40_newline_full_${STAMP}"
LOG_DIR="$PERSIST/runs/logs"
LOG="$LOG_DIR/qwen_context40_full_${STAMP}.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT"
source "$PERSIST/env.sh"
source /home/ubuntu/venvs/rcmf-py311/bin/activate

{
  echo "===== $(date --iso-8601=seconds) :: context40 full baseline ====="
  git rev-parse --short HEAD
  git status --short
  "$PYTHON" -V
  "$PYTHON" scripts/evaluate.py \
    --config "$CONFIG" \
    --benchmark appworld \
    --split test \
    --max-steps 50 \
    --max-new-tokens 512 \
    --temperature 0.0 \
    --top-p 1.0 \
    --no-memory \
    --output-dir "$OUT" \
    --experiment-name "$EXP"
  echo "===== $(date --iso-8601=seconds) :: done ====="
  echo "$OUT/evaluate/test/summary.json"
} 2>&1 | tee "$LOG"
