from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.utils.serialization import atomic_write_json


def _read_eval_dir(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.json")):
        if file.name == "summary.json":
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        rows.append(
            {
                "task_id": str(data.get("task_id") or file.stem),
                "success": bool(data.get("success")),
                "score": data.get("score"),
                "steps": data.get("steps"),
                "path": str(file),
            }
        )
    return rows


def _compare(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    limit: int | None,
) -> dict[str, Any]:
    if limit is not None:
        candidate_rows = candidate_rows[:limit]
    baseline_map = {row["task_id"]: row for row in baseline_rows}
    paired: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        baseline = baseline_map.get(candidate["task_id"])
        row = {
            "task_id": candidate["task_id"],
            "candidate_success": candidate["success"],
            "candidate_steps": candidate["steps"],
            "candidate_path": candidate["path"],
        }
        if baseline is None:
            row["baseline_missing"] = True
        else:
            row.update(
                {
                    "baseline_success": baseline["success"],
                    "baseline_steps": baseline["steps"],
                    "baseline_path": baseline["path"],
                }
            )
        paired.append(row)

    def has(row: dict[str, Any], key: str) -> bool:
        return bool(row.get(key))

    summary = {
        "baseline_total": len(baseline_rows),
        "candidate_total": len(candidate_rows),
        "paired": len(paired),
        "baseline_success_on_paired": sum(1 for row in paired if has(row, "baseline_success")),
        "candidate_success_on_paired": sum(1 for row in paired if has(row, "candidate_success")),
        "retained_baseline_success": sum(
            1 for row in paired if has(row, "baseline_success") and has(row, "candidate_success")
        ),
        "lost_baseline_success": sum(
            1 for row in paired if has(row, "baseline_success") and not has(row, "candidate_success")
        ),
        "gained_over_baseline": sum(
            1 for row in paired if not has(row, "baseline_success") and has(row, "candidate_success")
        ),
        "both_failed": sum(
            1 for row in paired if not has(row, "baseline_success") and not has(row, "candidate_success")
        ),
        "baseline_success_ids": [row["task_id"] for row in paired if has(row, "baseline_success")],
        "candidate_success_ids": [row["task_id"] for row in paired if has(row, "candidate_success")],
        "lost_ids": [
            row["task_id"]
            for row in paired
            if has(row, "baseline_success") and not has(row, "candidate_success")
        ],
        "gained_ids": [
            row["task_id"]
            for row in paired
            if not has(row, "baseline_success") and has(row, "candidate_success")
        ],
        "paired_rows": paired,
    }
    return summary


def _write_markdown(path: Path, summary: dict[str, Any], baseline_dir: Path, candidate_dir: Path) -> None:
    lines = [
        "# AppWorld Paired Success-Set Comparison",
        "",
        f"- baseline dir: `{baseline_dir}`",
        f"- candidate dir: `{candidate_dir}`",
        f"- paired tasks: {summary['paired']}",
        f"- baseline success on paired: {summary['baseline_success_on_paired']}/{summary['paired']}",
        f"- candidate success on paired: {summary['candidate_success_on_paired']}/{summary['paired']}",
        f"- retained baseline successes: {summary['retained_baseline_success']}",
        f"- lost baseline successes: {summary['lost_baseline_success']}",
        f"- gained over baseline: {summary['gained_over_baseline']}",
        f"- both failed: {summary['both_failed']}",
        "",
        "## Success-Set Changes",
        "",
        f"- retained: {', '.join(sorted(set(summary['baseline_success_ids']).intersection(summary['candidate_success_ids']))) or '(none)'}",
        f"- lost: {', '.join(summary['lost_ids']) or '(none)'}",
        f"- gained: {', '.join(summary['gained_ids']) or '(none)'}",
        "",
        "## Paired Rows",
        "",
        "| task_id | baseline | candidate | baseline_steps | candidate_steps |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in summary["paired_rows"]:
        lines.append(
            f"| {row['task_id']} | {row.get('baseline_success')} | {row.get('candidate_success')} | "
            f"{row.get('baseline_steps')} | {row.get('candidate_steps')} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare AppWorld per-task evaluation success sets.")
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args()

    baseline_dir = Path(args.baseline_dir)
    candidate_dir = Path(args.candidate_dir)
    summary = _compare(
        baseline_rows=_read_eval_dir(baseline_dir),
        candidate_rows=_read_eval_dir(candidate_dir),
        limit=args.limit,
    )
    summary["baseline_dir"] = str(baseline_dir)
    summary["candidate_dir"] = str(candidate_dir)
    atomic_write_json(args.output_json, summary)
    _write_markdown(Path(args.output_md), summary, baseline_dir, candidate_dir)
    print(f"Wrote paired comparison to {args.output_json} and {args.output_md}")


if __name__ == "__main__":
    main()
