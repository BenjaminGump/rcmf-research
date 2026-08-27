from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys

RUN_UUID = "rcmf_joint_full_bank_9a_20260826_001"
ATTEMPT_ID = "audit-export-008"
HEAD = "86374713cdd5efc76c10487e10861bc19dfe728a"
PROJECT = Path("/lambda/nfs/rcmf-persist/project")
ROOT = PROJECT / "runs/stage_c" / RUN_UUID
LEDGER = ROOT / "attempts.jsonl"
COMMAND = [
    "/home/ubuntu/venvs/rcmf-py311/bin/python",
    "scripts/export_rcmf_joint_full_bank_audit_9a.py",
    "--artifact-root",
    str(ROOT),
]

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def append(row: dict) -> None:
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]
if any(row.get("attempt_id") == ATTEMPT_ID for row in rows):
    raise SystemExit(f"duplicate attempt id: {ATTEMPT_ID}")
start = now()
common = {
    "format": "append_only_attempt_ledger_v1",
    "run_uuid": RUN_UUID,
    "attempt_id": ATTEMPT_ID,
    "phase": "joint_full_bank_detailed_audit_export",
    "parent_attempt_id": "audit-export-007",
    "resume_checkpoint": str(ROOT / "first37/final_summary.json"),
    "local_head": HEAD,
    "github_head": HEAD,
    "lambda_head": HEAD,
    "config_sha256": "b52d5681f6c10c1f5c0362476770640ab73c2b7291310dd0c3e4db7a51490870",
    "data_manifest_hashes": {
        "data_manifest": "196f9ec57674de202324bd34f0168cf4c0f6c5399fc9828939a8dcc730b214b2",
        "deployment": "5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e",
        "first37_final": "e352306e9aacfe231208520cbb782da31e5bd5d57ad50acf7a8b1e9390895ba6",
    },
    "process_command": COMMAND,
    "pid": os.getpid(),
    "tmux_session": "none_postrun_audit",
    "scientific_parameter_changed": False,
    "start_timestamp_utc": start,
}
append({**common, "event": "start", "timestamp_utc": start})
try:
    completed = subprocess.run(COMMAND, cwd=PROJECT, check=True)
except Exception as exc:
    end = now()
    append({
        **common,
        "event": "end",
        "timestamp_utc": end,
        "end_timestamp_utc": end,
        "exit_code": 1,
        "stop_reason": f"{type(exc).__name__}: {exc}",
        "latest_validated_checkpoint": str(ROOT / "first37/final_summary.json"),
    })
    raise
end = now()
append({
    **common,
    "event": "end",
    "timestamp_utc": end,
    "end_timestamp_utc": end,
    "exit_code": completed.returncode,
    "stop_reason": "normal_completion",
    "latest_validated_checkpoint": "research/audits/rcmf_joint_full_bank_9a_20260826_001/index.json",
})
print(json.dumps({"attempt_id": ATTEMPT_ID, "status": "complete"}, sort_keys=True))