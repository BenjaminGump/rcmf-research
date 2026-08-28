#!/usr/bin/env bash
set -euo pipefail

ROOT=/lambda/nfs/rcmf-persist/project
PY=/home/ubuntu/venvs/rcmf-py311/bin/python
CFG=configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml
ART="$ROOT/runs/stage_c/rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
LOG=/lambda/nfs/rcmf-persist/runs/logs/exp032a_rollouts_formal.log
EXPECTED_HEAD="$1"

cd "$ROOT"
ACTUAL_HEAD="$(git rev-parse HEAD)"
if [[ -z "$EXPECTED_HEAD" || "$ACTUAL_HEAD" != "$EXPECTED_HEAD" ]]; then
    echo "Lambda HEAD differs: expected=$EXPECTED_HEAD actual=$ACTUAL_HEAD" >&2
    exit 2
fi
mountpoint -q /lambda/nfs/rcmf-persist
mkdir -p "$(dirname "$LOG")"

echo "START $(date -u +%FT%TZ) exp032a-rollout-t0-003" >>"$LOG"
"$PY" scripts/run_rcmf_onpolicy_rollouts_10a.py \
    --config "$CFG" --artifact-dir "$ART" --phase run --condition T0 \
    --attempt-id exp032a-rollout-t0-003 \
    --parent-attempt-id exp032a-determinism-002 \
    --local-head "$EXPECTED_HEAD" --github-head "$EXPECTED_HEAD" \
    --lambda-head "$EXPECTED_HEAD" --tmux-session exp032a_rollouts_formal \
    >>"$LOG" 2>&1
echo "END $(date -u +%FT%TZ) exp032a-rollout-t0-003" >>"$LOG"

echo "START $(date -u +%FT%TZ) exp032a-rollout-t1-004" >>"$LOG"
"$PY" scripts/run_rcmf_onpolicy_rollouts_10a.py \
    --config "$CFG" --artifact-dir "$ART" --phase run --condition T1 \
    --attempt-id exp032a-rollout-t1-004 \
    --parent-attempt-id exp032a-rollout-t0-003 \
    --local-head "$EXPECTED_HEAD" --github-head "$EXPECTED_HEAD" \
    --lambda-head "$EXPECTED_HEAD" --tmux-session exp032a_rollouts_formal \
    >>"$LOG" 2>&1
echo "END $(date -u +%FT%TZ) exp032a-rollout-t1-004" >>"$LOG"

echo "START $(date -u +%FT%TZ) exp032a-rollout-t2-005" >>"$LOG"
"$PY" scripts/run_rcmf_onpolicy_rollouts_10a.py \
    --config "$CFG" --artifact-dir "$ART" --phase run --condition T2 \
    --attempt-id exp032a-rollout-t2-005 \
    --parent-attempt-id exp032a-rollout-t1-004 \
    --local-head "$EXPECTED_HEAD" --github-head "$EXPECTED_HEAD" \
    --lambda-head "$EXPECTED_HEAD" --tmux-session exp032a_rollouts_formal \
    >>"$LOG" 2>&1
echo "END $(date -u +%FT%TZ) exp032a-rollout-t2-005" >>"$LOG"

echo "START $(date -u +%FT%TZ) exp032a-rollout-finalize-006" >>"$LOG"
"$PY" scripts/run_rcmf_onpolicy_rollouts_10a.py \
    --config "$CFG" --artifact-dir "$ART" --phase finalize \
    --attempt-id exp032a-rollout-finalize-006 \
    --parent-attempt-id exp032a-rollout-t2-005 \
    --local-head "$EXPECTED_HEAD" --github-head "$EXPECTED_HEAD" \
    --lambda-head "$EXPECTED_HEAD" --tmux-session exp032a_rollouts_formal \
    >>"$LOG" 2>&1
echo "END $(date -u +%FT%TZ) exp032a-rollout-finalize-006" >>"$LOG"
