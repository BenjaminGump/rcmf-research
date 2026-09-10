from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from rcmf.benchmarks.alfworld.trajectories import build_corpus_manifest, read_corpus_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge append-only ALFWorld replay attempts")
    parser.add_argument("--primary", required=True)
    parser.add_argument("--retry", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    primary_path = Path(args.primary).resolve(strict=True)
    primary = read_corpus_jsonl(primary_path)
    if len(primary) != 3553:
        raise ValueError("primary corpus attempt must contain the full 3,553 TRAIN population")
    selected = {str(row["task_id"]): row for row in primary}
    replacements = []
    retry_inputs = []
    for raw_path in args.retry:
        retry_path = Path(raw_path).resolve(strict=True)
        retry_rows = read_corpus_jsonl(retry_path)
        retry_inputs.append(
            {"path": str(retry_path), "rows": len(retry_rows), "sha256": sha256_file(retry_path)}
        )
        for row in retry_rows:
            task_id = str(row["task_id"])
            if task_id not in selected:
                raise ValueError("retry task is outside the full primary TRAIN population")
            prior = selected[task_id]
            if prior["status"] == "SUCCESS":
                if row["status"] == "SUCCESS" and row["sequence_sha256"] != prior["sequence_sha256"]:
                    raise ValueError("successful retry sequence differs from prior official success")
                continue
            if row["status"] == "SUCCESS":
                selected[task_id] = row
                replacements.append(
                    {
                        "task_id": task_id,
                        "prior_status": prior["status"],
                        "prior_sequence_sha256": prior["sequence_sha256"],
                        "retry_status": row["status"],
                        "retry_sequence_sha256": row["sequence_sha256"],
                    }
                )
    ordered = [selected[str(row["task_id"])] for row in primary]
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in ordered:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    manifest = build_corpus_manifest(output, ordered)
    manifest.update(
        {
            "schema_version": "alfworld_official_expert_merged_corpus_manifest_v1",
            "source_commit": args.source_commit,
            "run_uuid": args.run_uuid,
            "complete_train_population": len(ordered) == 3553,
            "primary_attempt": {
                "path": str(primary_path),
                "rows": len(primary),
                "sha256": sha256_file(primary_path),
            },
            "retry_attempts": retry_inputs,
            "successful_retry_replacement_count": len(replacements),
            "successful_retry_replacements": replacements,
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
