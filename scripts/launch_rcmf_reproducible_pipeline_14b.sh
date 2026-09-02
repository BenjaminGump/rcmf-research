#!/usr/bin/env bash
set -euo pipefail

PERSIST=/lambda/nfs/rcmf-persist
PROJECT="$PERSIST/project"
RUN_ROOT="$PROJECT/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001"
PYTHON=/home/ubuntu/venvs/rcmf-py311/bin/python
SESSION=exp037a_repro_14b
WATCHDOG=exp037a_watchdog_14b

mountpoint -q "$PERSIST"
cd "$PROJECT"
test -f "$RUN_ROOT/preflight/preflight_summary.json"
test -f "$RUN_ROOT/preflight/stage_dag.json"

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" \
  "PYTHONHASHSEED=25101 CUBLAS_WORKSPACE_CONFIG=:4096:8 $PYTHON scripts/supervise_rcmf_reproducible_pipeline_14b.py --contract '$RUN_ROOT/preflight/stage_dag.json' --run-root '$RUN_ROOT' --maximum-parent-attempts 3 --retry-delay-seconds 60 >> '$RUN_ROOT/orchestrator.log' 2>&1"

tmux has-session -t "$WATCHDOG" 2>/dev/null || tmux new-session -d -s "$WATCHDOG" \
  "$PYTHON scripts/monitor_rcmf_reproducible_pipeline_14b.py --run-root '$RUN_ROOT' >> '$RUN_ROOT/watchdog.log' 2>&1"

tmux list-sessions
