# Research Ops Tools

These utilities maintain the GitHub-safe research workflow.

- `audit_workspace.sh`: non-destructive Lambda/local audit into
  `research/migration/`.
- `start_run.py`: create a start manifest before launching an experiment.
- `finalize_run.py`: create a result summary and append `experiments.jsonl`.
- `collect_snapshot.py`: collect the current state, latest handoff, and result
  summaries for ChatGPT.
- `backfill_run.py`: add historical runs to the ledger.
- `validate_research_state.py`: validate docs, JSONL, and tracked-file hygiene.

They intentionally avoid copying large Lambda artifacts into Git. Store large
files on Lambda and reference their paths from manifests and summaries.
