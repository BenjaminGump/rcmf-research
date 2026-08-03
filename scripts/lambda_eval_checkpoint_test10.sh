#!/usr/bin/env bash
set -Eeuo pipefail

PERSIST="${RCMF_PERSIST:-/lambda/nfs/rcmf-persist}"
PROJECT="${PROJECT:-$PERSIST/project}"
PYTHON="${PYTHON:-/home/ubuntu/venvs/rcmf-py311/bin/python}"
DATA_DIR="${DATA_DIR:-runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803}"
RCMF_CONFIG="${RCMF_CONFIG:-configs/benchmark/appworld_rcmf_full_prompt.yaml}"
STAMP="${1:-$(date +%Y%m%d_%H%M%S)}"

if [[ -z "${CHECKPOINT:-}" ]]; then
  echo "ERROR: set CHECKPOINT to the RCMF checkpoint to evaluate." >&2
  exit 2
fi

TRAIN_DIR="${TRAIN_DIR:-$(dirname "$(dirname "$CHECKPOINT")")}"
REPRESENTATION_CACHE="${REPRESENTATION_CACHE:-$TRAIN_DIR/train/representation_cache/memory_record_representations.pt}"
MEMORY_OUT="${MEMORY_OUT:-$TRAIN_DIR/memory_${STAMP}.safetensors}"
LEDGER_DIR="${LEDGER_DIR:-${MEMORY_OUT%.safetensors}_ledger}"
EVAL_OUT="${EVAL_OUT:-runs/experiments/rcmf_appworld_checkpoint_test10_${STAMP}}"
EXPERIMENT_NAME="${EXPERIMENT_NAME:-$(basename "$EVAL_OUT")}"

cd "$PROJECT"
source "$PERSIST/env.sh"
source /home/ubuntu/venvs/rcmf-py311/bin/activate

log_step() {
  echo
  echo "===== $(date --iso-8601=seconds) :: $* ====="
}

log_step "compile memory for checkpoint"
"$PYTHON" scripts/compile_memory.py \
  --config "$RCMF_CONFIG" \
  --records "$DATA_DIR/memory_records.jsonl" \
  --compiler checkpoint \
  --checkpoint "$CHECKPOINT" \
  --representation-cache "$REPRESENTATION_CACHE" \
  --output "$MEMORY_OUT" \
  --ledger-dir "$LEDGER_DIR"

log_step "evaluate checkpoint test10"
"$PYTHON" scripts/evaluate.py \
  --config "$RCMF_CONFIG" \
  --benchmark appworld \
  --split test \
  --limit 10 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --memory-scale "${MEMORY_SCALE:-1.0}" \
  --checkpoint "$CHECKPOINT" \
  --memory-snapshot "$MEMORY_OUT" \
  --output-dir "$EVAL_OUT" \
  --experiment-name "$EXPERIMENT_NAME"

log_step "summary"
cat "$EVAL_OUT/evaluate/test/summary.json"
