from __future__ import annotations
import hashlib
import json
from pathlib import Path

PROJECT = Path("/lambda/nfs/rcmf-persist/project")
RUN = "rcmf_joint_full_bank_9a_20260826_001"
AUDIT = PROJECT / "research/audits" / RUN
LEDGER = PROJECT / "runs/stage_c" / RUN / "attempts.jsonl"

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

index = json.loads((AUDIT / "index.json").read_text(encoding="utf-8"))
mismatches = []
for entry in index["files"]:
    path = PROJECT / entry["path"]
    actual = sha(path)
    if actual != entry["sha256"] or path.stat().st_size != entry["bytes"]:
        mismatches.append(entry["path"])
verification = json.loads((AUDIT / "verification.json").read_text(encoding="utf-8"))
rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]
starts = {row["attempt_id"] for row in rows if row.get("event") == "start"}
ends = {row["attempt_id"] for row in rows if row.get("event") == "end"}
files = [path for path in AUDIT.rglob("*") if path.is_file()]
result = {
    "audit_root": str(AUDIT),
    "audit_index_sha256": sha(AUDIT / "index.json"),
    "index_file_entries": len(index["files"]),
    "physical_file_count": len(files),
    "indexed_hash_mismatches": mismatches,
    "audit_bytes": sum(path.stat().st_size for path in files),
    "largest_file": max(files, key=lambda path: path.stat().st_size).relative_to(PROJECT).as_posix(),
    "largest_file_bytes": max(path.stat().st_size for path in files),
    "registered_sensitive_observation_count": verification["registered_sensitive_observation_count"],
    "registered_sensitive_observation_leak_count": verification["registered_sensitive_observation_leak_count"],
    "raw_jwt_match_count": verification["raw_jwt_match_count"],
    "checked_text_files": verification["checked_text_files"],
    "attempt_event_rows": len(rows),
    "attempt_ids": len(starts | ends),
    "open_attempt_ids": sorted(starts - ends),
    "latest_attempt": rows[-1]["attempt_id"],
    "latest_attempt_status": rows[-1]["stop_reason"],
    "decision": index["decision"],
}
if mismatches or verification["registered_sensitive_observation_leak_count"] or verification["raw_jwt_match_count"] or starts != ends:
    raise SystemExit(json.dumps(result, sort_keys=True))
print(json.dumps(result, sort_keys=True, indent=2))