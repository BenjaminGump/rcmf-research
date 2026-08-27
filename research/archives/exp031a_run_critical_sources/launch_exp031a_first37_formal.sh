#!/usr/bin/env bash
set -euo pipefail
cd /lambda/nfs/rcmf-persist/project
PY=/home/ubuntu/venvs/rcmf-py311/bin/python
RUN=/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_joint_full_bank_9a_20260826_001
LOG=/lambda/nfs/rcmf-persist/runs/logs
HEAD=f6808b6457bf2f91db9466e1f1b2f831edf39a76
COMMON=(--artifact-dir "$RUN" --local-head "$HEAD" --github-head "$HEAD" --lambda-head "$HEAD" --tmux-session exp031a_first37_formal)
"$PY" scripts/run_rcmf_joint_full_bank_first37_9a.py "${COMMON[@]}" --phase run --condition D0 --attempt-id first37-d0-001 --parent-attempt-id first37-smoke-v2-d2-001 --resume-checkpoint "$RUN/first37/smoke_v2/D2/summary.json" > "$LOG/exp031a_first37_d0.log" 2>&1
"$PY" scripts/run_rcmf_joint_full_bank_first37_9a.py "${COMMON[@]}" --phase run --condition D1 --attempt-id first37-d1-001 --parent-attempt-id first37-d0-001 --resume-checkpoint "$RUN/first37/conditions/D0/summary.json" > "$LOG/exp031a_first37_d1.log" 2>&1
"$PY" scripts/run_rcmf_joint_full_bank_first37_9a.py "${COMMON[@]}" --phase run --condition D2 --attempt-id first37-d2-001 --parent-attempt-id first37-d1-001 --resume-checkpoint "$RUN/first37/conditions/D1/summary.json" > "$LOG/exp031a_first37_d2.log" 2>&1
touch "$RUN/first37/formal_tmux_complete"