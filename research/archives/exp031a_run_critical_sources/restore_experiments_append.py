from pathlib import Path
import subprocess
path = Path("research/experiments.jsonl")
current = path.read_bytes().splitlines()
needle = b'"run_id":"rcmf_joint_full_bank_9a_20260826_001"'
rows = [row for row in current if needle in row]
if len(rows) != 1:
    raise SystemExit(f"expected one new row, found {len(rows)}")
base = subprocess.check_output(["git", "show", "HEAD:research/experiments.jsonl"])
path.write_bytes(base.rstrip(b"\r\n") + b"\n" + rows[0] + b"\n")