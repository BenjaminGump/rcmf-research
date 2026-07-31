from __future__ import annotations

from pathlib import Path
from typing import Any

from rcmf.eval.metrics import summarize_results
from rcmf.schemas import BenchmarkResult
from rcmf.utils.serialization import atomic_write_json, write_jsonl


class EvaluationRunner:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self, adapter: Any, policy: Any, task_ids: list[str]) -> dict[str, float]:
        results: list[BenchmarkResult] = []
        for index, task_id in enumerate(task_ids, start=1):
            print(f"evaluate {index}/{len(task_ids)} task_id={task_id}", flush=True)
            result = adapter.run_episode(policy, task_id, None)
            results.append(result)
            atomic_write_json(self.output_dir / f"{task_id}.json", result.to_dict())
            print(
                f"result {task_id} success={result.success} score={result.score:.1f} "
                f"steps={result.steps} wall_time_s={result.wall_time_s:.1f}",
                flush=True,
            )
        write_jsonl(self.output_dir / "results.jsonl", (result.to_dict() for result in results))
        summary = summarize_results(results)
        atomic_write_json(self.output_dir / "summary.json", summary)
        return summary
