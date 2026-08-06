from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.student_labels import (
    StudentLabelThresholds,
    compile_stage_b_student_labels,
    write_compiled_student_labels,
)
from rcmf.utils.serialization import atomic_write_text, maybe_git_commit


def _report_md(summary: dict) -> str:
    label_counts = summary["label_counts"]
    train = label_counts.get("train", {})
    validation = label_counts.get("validation", {})
    special = summary.get("special_memory") or {}
    lines = [
        "# Stage-B Addressing Student Labels",
        "",
        f"- format: `{summary['format']}`",
        f"- teacher cache: `{summary['teacher_cache_version']}`",
        f"- scoring definition: `{summary['teacher_scoring_definition']}`",
        f"- split seed: `{summary['split_seed']}`",
        f"- train tasks: {summary['train_task_count']}",
        f"- validation tasks: {summary['validation_task_count']}",
        f"- train states: {summary['train_state_count']}",
        f"- validation states: {summary['validation_state_count']}",
        f"- effective train-memory bank size: {summary['stage_b_effective_memory_count']}",
        f"- validation passed: {summary['validation']['passed']}",
        "",
        "## Special Memory",
        "",
        f"- memory id: `{special.get('memory_id')}`",
        f"- task id: `{special.get('task_id')}`",
        f"- eligible_for_stage_b: `{special.get('eligible_for_stage_b')}`",
        f"- exclusion_reason: `{special.get('exclusion_reason')}`",
        f"- valid_stage_b_train_label_count: `{special.get('valid_stage_b_train_label_count')}`",
        "",
        "## Label Counts",
        "",
        "| split | states | valid rows | positive | neutral | negative | strong+ | strong- | no-positive | all-missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split, counts in (("train", train), ("validation", validation)):
        lines.append(
            "| "
            + " | ".join(
                [
                    split,
                    str(counts.get("states", 0)),
                    str(counts.get("valid_rows", 0)),
                    str(counts.get("positive", 0)),
                    str(counts.get("neutral", 0)),
                    str(counts.get("negative", 0)),
                    str(counts.get("strong_positive", 0)),
                    str(counts.get("strong_negative", 0)),
                    str(counts.get("no_positive_states", 0)),
                    str(counts.get("all_missing_states", 0)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Threshold Coverage",
            "",
            "Coverage uses fixed thresholds 0.01, 0.05, and 0.10. Thresholds were not selected using validation labels.",
            "",
            f"```json\n{summary['threshold_coverage']}\n```",
            "",
            "## Excluded Memory Counts",
            "",
            f"- excluded memories: {len(summary['excluded_memories'])}",
            f"- masked own-task pair count: {summary['masked_own_task_pair_count']}",
            f"- masked over-context pair count: {summary['masked_over_context_pair_count']}",
            f"- missing teacher pair count: {summary['missing_teacher_pair_count']}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile Stage-B addressing student labels.")
    parser.add_argument("--data", required=True)
    parser.add_argument("--teacher-cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--neutral-eps", type=float, default=0.01)
    parser.add_argument("--strong-positive", type=float, default=0.05)
    parser.add_argument("--strong-negative", type=float, default=-0.05)
    args = parser.parse_args()

    data_dir = Path(args.data)
    teacher_dir = Path(args.teacher_cache_dir)
    output_dir = Path(args.output_dir)
    examples = load_decision_examples(data_dir / "decision_examples.jsonl")
    records = load_memory_records(data_dir / "memory_records.jsonl")
    compiled = compile_stage_b_student_labels(
        examples=examples,
        records=records,
        teacher_cache_jsonl=teacher_dir / "teacher_cache_full_rows.jsonl",
        teacher_summary_json=teacher_dir / "summary.json",
        split_manifest_json=teacher_dir / "student_split_manifest.json",
        data_dir=data_dir,
        thresholds=StudentLabelThresholds(
            neutral_eps=args.neutral_eps,
            strong_positive=args.strong_positive,
            strong_negative=args.strong_negative,
        ),
    )
    write_compiled_student_labels(output_dir, compiled)
    summary = dict(compiled.summary)
    summary["git_commit"] = maybe_git_commit()
    from rcmf.utils.serialization import atomic_write_json

    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _report_md(summary))
    if not compiled.validation["passed"]:
        raise SystemExit(f"Stage-B label validation failed: {compiled.validation['errors_first_50']}")
    print(f"Wrote Stage-B labels to {output_dir}")


if __name__ == "__main__":
    main()
