#!/usr/bin/env bash
set -Eeuo pipefail

PERSIST="${RCMF_PERSIST:-/lambda/nfs/rcmf-persist}"
PROJECT="${PROJECT:-$PERSIST/project}"
PYTHON="${PYTHON:-/home/ubuntu/venvs/rcmf-py311/bin/python}"
DATA_DIR="${DATA_DIR:-runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1}"
BASELINE_CONFIG="${BASELINE_CONFIG:-configs/baseline/appworld_qwen_full_prompt_context40.yaml}"
RCMF_CONFIG="${RCMF_CONFIG:-configs/benchmark/appworld_rcmf_full_prompt.yaml}"
STAMP="${1:-$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="$PERSIST/runs/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT"
source "$PERSIST/env.sh"
source /home/ubuntu/venvs/rcmf-py311/bin/activate

RUN_PREFIX="weekend_${STAMP}"
BASE10_OUT="runs/experiments/qwen_appworld_full_prompt_baseline_test10_${STAMP}"
BASE10_EXP="qwen_appworld_full_prompt_baseline_test10_${STAMP}"
BASEFULL_OUT="runs/experiments/qwen_appworld_full_prompt_baseline_full_${STAMP}"
BASEFULL_EXP="qwen_appworld_full_prompt_baseline_full_${STAMP}"
TRAIN_OUT="runs/experiments/appworld_qwen_repr_full_prompt_official_${STAMP}"
RCMF_EXP="rcmf_appworld_full_prompt_test10_${STAMP}"
RCMF_OUT="$TRAIN_OUT"
LENGTH_JSON="runs/experiments/${RUN_PREFIX}_query_token_lengths.json"
SUMMARY_JSON="runs/experiments/${RUN_PREFIX}_summary.json"

log_step() {
  echo
  echo "===== $(date --iso-8601=seconds) :: $* ====="
}

write_summary() {
  "$PYTHON" - "$SUMMARY_JSON" "$STAMP" "$BASE10_OUT" "$BASEFULL_OUT" "$TRAIN_OUT" "$LENGTH_JSON" <<'PY'
from pathlib import Path
import json
import sys

summary_path, stamp, base10, basefull, train, lengths = sys.argv[1:]
payload = {
    "stamp": stamp,
    "base10_output": base10,
    "basefull_output": basefull,
    "train_output": train,
    "length_json": lengths,
}
for name, path in [
    ("base10_summary", Path(base10) / "evaluate" / "test" / "summary.json"),
    ("basefull_summary", Path(basefull) / "evaluate" / "test" / "summary.json"),
    ("train_summary", Path(train) / "train" / "train_summary.json"),
    ("rcmf_test10_summary", Path(train) / "evaluate" / "test" / "summary.json"),
]:
    if path.exists():
        payload[name] = json.loads(path.read_text(encoding="utf-8"))
summary = Path(summary_path)
summary.parent.mkdir(parents=True, exist_ok=True)
summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(summary)
PY
}

log_step "repo"
git log --oneline -3
git status --short
"$PYTHON" -V
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader || true

log_step "baseline test10"
"$PYTHON" scripts/evaluate.py \
  --config "$BASELINE_CONFIG" \
  --benchmark appworld \
  --split test \
  --limit 10 \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory \
  --output-dir "$BASE10_OUT" \
  --experiment-name "$BASE10_EXP"

log_step "query token length check"
"$PYTHON" scripts/check_training_query_lengths.py \
  --config "$RCMF_CONFIG" \
  --data "$DATA_DIR" \
  --output "$LENGTH_JSON"

log_step "full no-truncation train attempt"
set +e
"$PYTHON" scripts/train.py \
  --config "$RCMF_CONFIG" \
  --data "$DATA_DIR" \
  --output-dir "$TRAIN_OUT" \
  --epochs 1 \
  --batch-size 1 \
  --grad-accumulation-steps 1 \
  --support-mode all_except_current_task \
  --representation-batch-size 1 \
  --save-every 100 \
  --log-every 10
TRAIN_STATUS=$?
set -e
echo "TRAIN_STATUS=$TRAIN_STATUS"

if [[ "$TRAIN_STATUS" == "0" ]]; then
  log_step "compile memory"
  "$PYTHON" scripts/compile_memory.py \
    --config "$RCMF_CONFIG" \
    --records "$DATA_DIR/memory_records.jsonl" \
    --compiler checkpoint \
    --checkpoint "$TRAIN_OUT/train/checkpoint.pt" \
    --representation-cache "$TRAIN_OUT/train/representation_cache/memory_record_representations.pt" \
    --output "$TRAIN_OUT/memory.safetensors" \
    --ledger-dir "$TRAIN_OUT/memory_ledger"

  log_step "rcmf test10 same prompt flow"
  "$PYTHON" scripts/evaluate.py \
    --config "$RCMF_CONFIG" \
    --benchmark appworld \
    --split test \
    --limit 10 \
    --max-steps 50 \
    --max-new-tokens 512 \
    --temperature 0.0 \
    --top-p 1.0 \
    --checkpoint "$TRAIN_OUT/train/checkpoint.pt" \
    --memory-snapshot "$TRAIN_OUT/memory.safetensors" \
    --output-dir "$RCMF_OUT" \
    --experiment-name "$RCMF_EXP"
else
  log_step "train did not complete; running full baseline while preserving failure logs"
fi

log_step "baseline full test_normal"
"$PYTHON" scripts/evaluate.py \
  --config "$BASELINE_CONFIG" \
  --benchmark appworld \
  --split test \
  --max-steps 50 \
  --max-new-tokens 512 \
  --temperature 0.0 \
  --top-p 1.0 \
  --no-memory \
  --output-dir "$BASEFULL_OUT" \
  --experiment-name "$BASEFULL_EXP"

log_step "summary"
write_summary
