from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _iter_payloads(path: Path) -> Iterable[dict[str, Any]]:
    if path.is_dir():
        for child in sorted(path.glob("*.json")):
            if child.name == "summary.json":
                continue
            yield from _iter_payloads(child)
        jsonl = path / "results.jsonl"
        if jsonl.exists():
            yield from _iter_payloads(jsonl)
        return
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    if path.suffix == ".json":
        yield json.loads(path.read_text(encoding="utf-8"))


def _field(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return metrics[key]
    extra = payload.get("extra_metrics")
    if isinstance(extra, dict) and key in extra:
        return extra[key]
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize AppWorld evaluation result JSON/JSONL files.")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in args.paths:
        for payload in _iter_payloads(path):
            task_id = str(_field(payload, "task_id"))
            if not task_id:
                continue
            source_key = f"{path}:{task_id}:{_field(payload, 'steps')}:{_field(payload, 'wall_time_s')}"
            if source_key in seen:
                continue
            seen.add(source_key)
            rows.append(payload)

    print("task_id\tsuccess\tscore\tsteps\tprompt_tokens\tgenerated_tokens\twall_time_s")
    for payload in rows:
        print(
            "\t".join(
                str(_field(payload, key))
                for key in (
                    "task_id",
                    "success",
                    "score",
                    "steps",
                    "prompt_tokens",
                    "generated_tokens",
                    "wall_time_s",
                )
            )
        )
    if rows:
        successes = sum(1 for row in rows if bool(_field(row, "success")))
        print(f"# success_rate={successes}/{len(rows)}={successes / len(rows):.4f}")


if __name__ == "__main__":
    main()
