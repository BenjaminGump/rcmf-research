# Experiment Ledger Schema

`research/experiments.jsonl` is append-only. Each line is a JSON object.

Required fields:

```json
{
  "run_id": "string",
  "status": "planned|running|completed|failed|aborted|stopped",
  "commit": "string",
  "branch": "string",
  "date_utc": "YYYY-MM-DDTHH:MM:SSZ",
  "hypothesis": "string",
  "change": "string",
  "benchmark": "appworld",
  "split": "test_normal",
  "config": "path or null",
  "command": "path or command summary",
  "baseline_run": "string or null",
  "primary_metric": {
    "name": "success_rate",
    "value": 0.0,
    "numerator": 0,
    "denominator": 0
  },
  "baseline_metric": {
    "name": "success_rate",
    "value": 0.0,
    "numerator": 0,
    "denominator": 0
  },
  "successes": ["task_id"],
  "retained": 0,
  "gained": 0,
  "lost": 0,
  "both_failed": 0,
  "artifact_manifest": "path or null",
  "result_summary": "research/results/<run_id>.md",
  "notes": "string"
}
```

Rules:

- Use `stopped` when a run is intentionally stopped based on an interim stop
  condition.
- Use `failed` for infrastructure or code failures.
- Use `aborted` for user- or operator-cancelled runs without an analytical stop
  condition.
- Denominators must be exact, not inferred from percentages.
- Artifact paths should point to Lambda for large files and GitHub paths for
  small summaries.
