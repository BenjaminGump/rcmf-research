#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$ROOT/research/migration"
OUT="$OUT_DIR/AUDIT_$TS.md"

mkdir -p "$OUT_DIR"

{
  echo "# Workspace Audit"
  echo
  echo "- Date UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- Root: \`$ROOT\`"
  echo "- Hostname: \`$(hostname 2>/dev/null || true)\`"
  echo "- User: \`$(whoami 2>/dev/null || true)\`"
  echo
  echo "## Git"
  echo
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo '```text'
    git -C "$ROOT" status --short --branch || true
    git -C "$ROOT" rev-parse --short HEAD || true
    git -C "$ROOT" remote -v || true
    git -C "$ROOT" log --oneline -10 || true
    echo '```'
  else
    echo "Not a Git repository."
  fi
  echo
  echo "## Runtime"
  echo
  echo '```text'
  uname -a || true
  python --version 2>&1 || true
  nvidia-smi || true
  echo '```'
  echo
  echo "## Persistent Filesystem"
  echo
  echo '```text'
  findmnt /lambda/nfs/rcmf-persist || true
  df -h /lambda/nfs/rcmf-persist || true
  du -sh /lambda/nfs/rcmf-persist 2>/dev/null || true
  echo '```'
  echo
  echo "## Active Processes"
  echo
  echo '```text'
  pgrep -af "scripts/train.py|scripts/evaluate.py|lambda_train|lambda_eval" || true
  nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader || true
  echo '```'
  echo
  echo "## Top-Level Inventory"
  echo
  echo '```text'
  find "$ROOT" -maxdepth 2 -mindepth 1 -printf '%y %p\n' 2>/dev/null | sort | head -200 || true
  echo '```'
} > "$OUT"

echo "$OUT"
