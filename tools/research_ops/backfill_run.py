from __future__ import annotations

import argparse

from common import append_jsonl, metric_from_counts, repo_root, utc_now, write_text


def _split_successes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill a historical experiment ledger row.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", default="completed")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--branch", default="unknown")
    parser.add_argument("--date-utc", default=None)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument("--benchmark", default="appworld")
    parser.add_argument("--split", required=True)
    parser.add_argument("--config")
    parser.add_argument("--command")
    parser.add_argument("--baseline-run")
    parser.add_argument("--numerator", type=int, required=True)
    parser.add_argument("--denominator", type=int, required=True)
    parser.add_argument("--baseline-numerator", type=int)
    parser.add_argument("--baseline-denominator", type=int)
    parser.add_argument("--successes")
    parser.add_argument("--retained", type=int)
    parser.add_argument("--gained", type=int)
    parser.add_argument("--lost", type=int)
    parser.add_argument("--both-failed", type=int)
    parser.add_argument("--result-summary")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    root = repo_root()
    result_summary = args.result_summary or f"research/results/{args.run_id}.md"
    baseline_metric = None
    if args.baseline_numerator is not None and args.baseline_denominator is not None:
        baseline_metric = metric_from_counts(
            "success_rate", args.baseline_numerator, args.baseline_denominator
        )

    entry = {
        "run_id": args.run_id,
        "status": args.status,
        "commit": args.commit,
        "branch": args.branch,
        "date_utc": args.date_utc or utc_now(),
        "hypothesis": args.hypothesis,
        "change": args.change,
        "benchmark": args.benchmark,
        "split": args.split,
        "config": args.config,
        "command": args.command,
        "baseline_run": args.baseline_run,
        "primary_metric": metric_from_counts("success_rate", args.numerator, args.denominator),
        "baseline_metric": baseline_metric,
        "successes": _split_successes(args.successes),
        "retained": args.retained,
        "gained": args.gained,
        "lost": args.lost,
        "both_failed": args.both_failed,
        "artifact_manifest": None,
        "result_summary": result_summary,
        "notes": args.notes,
    }

    append_jsonl(root / "research" / "experiments.jsonl", entry)
    if not (root / result_summary).exists():
        write_text(
            root / result_summary,
            f"# {args.run_id}\n\nBackfilled historical run.\n\n{args.notes}\n",
        )
    print(root / result_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
