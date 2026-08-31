#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 ]]; then
  echo "usage: $0 <python-executable> <entrypoint> [args ...]" >&2
  exit 64
fi

export PYTHONHASHSEED=25101
export RCMF_DETERMINISM_LAUNCHER_PATH="$0"
printf -v RCMF_DETERMINISM_LAUNCH_COMMAND '%q ' "$@"
export RCMF_DETERMINISM_LAUNCH_COMMAND

exec "$@"

