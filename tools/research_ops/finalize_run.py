from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    append_jsonl,
    git_info,
    load_per_task,
    metric_from_file,
    read_json,
    repo_root,
    success_task_ids,
    utc_now,
    write_json,
    write_text,
)


def _artifact(path: str | None, kind: str) -> dict[str, object] | None:
    if not path:
        return None
    p = Path(path)
    return {
        "type": kind,
        "path": str(p),
        "exists": p.exists(),
        "size_bytes": p.stat().st_size if p.exists() and p.is_file() else None,
    }


def _summary_markdown(entry: dict[str, object], artifacts: list[dict[str, object]]) -> str:
    metric = entry.get("primary_metric") or {}
    successes = entry.get("successes") or []
    lines = [
        f"# {entry['run_id']}",
        "",
        f"- Status: `{entry['status']}`",
        f"- Commit: `{entry['commit']}`",
        f"- Branch: `{entry['branch']}`",
        f"- Benchmark: `{entry['benchmark']}`",
        f"- Split: `{entry['split']}`",
        f"- Config: `{entry.get('config')}`",
        f"- Command: `{entry.get('command')}`",
        "",
        "## Metric",
        "",
        f"- {metric.get('name', 'metric')}: {metric.get('numerator')}/{metric.get('denominator')} = {metric.get('value')}",
        "",
        "## Successes",
        "",
        ", ".join(successes) if successes else "No per-task successes recorded.",
        "",
        "## Notes",
        "",
        str(entry.get("notes") or ""),
        "",
        "## Artifacts",
        "",
    ]
    if artifacts:
        for artifact in artifacts:
            lines.append(f"- `{artifact['type']}`: `{artifact['path']}`")
    else:
        lines.append("No artifacts recorded.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize an experiment into research ledger files.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", required=True, choices=["completed", "failed", "aborted", "stopped"])
    parser.add_argument("--metrics-file")
    parser.add_argument("--per-task-file")
    parser.add_argument("--checkpoint", action="append", default=[])
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--config")
    parser.add_argument("--command")
    parser.add_argument("--baseline-run")
    parser.add_argument("--notes", default="")
    parser.add_argument("--result-summary")
    parser.add_argument("--artifact-manifest")
    args = parser.parse_args()

    root = repo_root()
    info = git_info(root)
    per_task = load_per_task(Path(args.per_task_file)) if args.per_task_file else []
    successes = success_task_ids(per_task)
    total = len(per_task)
    metric = metric_from_file(Path(args.metrics_file) if args.metrics_file else None, successes, total)

    artifact_entries = []
    for checkpoint in args.checkpoint:
        value = _artifact(checkpoint, "checkpoint")
        if value:
            artifact_entries.append(value)
    for log_path in args.log:
        value = _artifact(log_path, "log")
        if value:
            artifact_entries.append(value)

    result_summary = args.result_summary or f"research/results/{args.run_id}.md"
    artifact_manifest = args.artifact_manifest or f"research/manifests/{args.run_id}/artifact_index.json"
    entry = {
        "run_id": args.run_id,
        "status": args.status,
        "commit": info["commit"],
        "branch": info["branch"],
        "date_utc": utc_now(),
        "hypothesis": "",
        "change": "",
        "benchmark": "unknown",
        "split": "unknown",
        "config": args.config,
        "command": args.command,
        "baseline_run": args.baseline_run,
        "primary_metric": metric,
        "baseline_metric": None,
        "successes": successes,
        "retained": None,
        "gained": None,
        "lost": None,
        "both_failed": None,
        "artifact_manifest": artifact_manifest,
        "result_summary": result_summary,
        "notes": args.notes,
    }

    if args.metrics_file and Path(args.metrics_file).exists():
        metrics_payload = read_json(Path(args.metrics_file))
        if isinstance(metrics_payload, dict):
            entry["metrics_file_payload"] = metrics_payload

    write_json(root / artifact_manifest, artifact_entries)
    write_text(root / result_summary, _summary_markdown(entry, artifact_entries))
    append_jsonl(root / "research" / "experiments.jsonl", entry)
    print(root / result_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
