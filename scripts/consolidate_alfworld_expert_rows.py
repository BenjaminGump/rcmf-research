from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rcmf.benchmarks.alfworld.task_manifest import load_sealed_task_manifest, portable_task_records
from rcmf.benchmarks.alfworld.training import sha256_file
from rcmf.benchmarks.alfworld.trajectories import build_corpus_manifest, read_corpus_jsonl, validate_corpus_row


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate append-only ALFWorld replay row evidence")
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--primary-row-dir", required=True)
    parser.add_argument("--overlay-corpus", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    tasks = list(portable_task_records(load_sealed_task_manifest(args.task_manifest))["train"])
    selected = {}
    primary_dir = Path(args.primary_row_dir).resolve(strict=True)
    for path in sorted(primary_dir.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        validate_corpus_row(row)
        task_id = str(row["task_id"])
        if task_id in selected:
            raise ValueError("primary replay row directory contains a duplicate task")
        selected[task_id] = row
    overlay_inputs = []
    overlay_replacements = []
    for raw_path in args.overlay_corpus:
        path = Path(raw_path).resolve(strict=True)
        rows = read_corpus_jsonl(path)
        overlay_inputs.append({"path": str(path), "rows": len(rows), "sha256": sha256_file(path)})
        for row in rows:
            task_id = str(row["task_id"])
            prior = selected.get(task_id)
            if prior is None or (prior["status"] != "SUCCESS" and row["status"] == "SUCCESS"):
                selected[task_id] = row
                overlay_replacements.append(
                    {
                        "task_id": task_id,
                        "prior_status": prior["status"] if prior else "MISSING",
                        "overlay_status": row["status"],
                    }
                )
    missing = [task.task_id for task in tasks if task.task_id not in selected]
    outside = sorted(set(selected) - {task.task_id for task in tasks})
    if missing or outside:
        raise ValueError(f"consolidated TRAIN row closure differs: missing={missing}, outside={outside}")
    ordered = [selected[task.task_id] for task in tasks]
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in ordered:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    manifest = build_corpus_manifest(output, ordered)
    manifest.update(
        {
            "schema_version": "alfworld_consolidated_official_expert_corpus_manifest_v1",
            "run_uuid": args.run_uuid,
            "source_commit": args.source_commit,
            "primary_row_dir": str(primary_dir),
            "primary_row_count": len(list(primary_dir.glob("*.json"))),
            "overlay_inputs": overlay_inputs,
            "overlay_replacements": overlay_replacements,
            "complete_train_population": len(ordered) == 3553,
        }
    )
    manifest_output = Path(args.manifest_output).resolve()
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_output.with_suffix(manifest_output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_output)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
