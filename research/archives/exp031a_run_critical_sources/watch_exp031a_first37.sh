#!/usr/bin/env bash
set -u
RUN=/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_joint_full_bank_9a_20260826_001
LOG=/lambda/nfs/rcmf-persist/runs/logs/exp031a_first37_watchdog.log
while tmux has-session -t exp031a_first37_formal 2>/dev/null; do
  now=$(date +%s)
  if ! mountpoint -q /lambda/nfs/rcmf-persist; then
    printf '%s NFS_MOUNT_MISSING\n' "$(date -u +%FT%TZ)" >> "$LOG"
  fi
  if [ -f "$RUN/heartbeat.json" ]; then
    modified=$(stat -c %Y "$RUN/heartbeat.json")
    age=$((now-modified))
    if [ "$age" -gt 720 ]; then
      printf '%s HEARTBEAT_STALE age_seconds=%s\n' "$(date -u +%FT%TZ)" "$age" >> "$LOG"
    fi
  fi
  sleep 60
done
if [ -f "$RUN/first37/formal_tmux_complete" ]; then
  printf '%s FORMAL_SEQUENCE_COMPLETE\n' "$(date -u +%FT%TZ)" >> "$LOG"
else
  printf '%s FORMAL_SEQUENCE_EXITED_WITHOUT_COMPLETE_MARKER\n' "$(date -u +%FT%TZ)" >> "$LOG"
fi