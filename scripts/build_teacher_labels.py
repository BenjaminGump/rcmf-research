from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.teacher_labels import cheap_utility_label
from rcmf.utils.serialization import write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Build fixed offline utility labels.")
    parser.add_argument("--records", required=True)
    parser.add_argument("--examples", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--labeler", choices=["cheap"], default="cheap")
    args = parser.parse_args()
    records = load_memory_records(args.records)
    examples = load_decision_examples(args.examples)
    labels = [
        cheap_utility_label(record, example).__dict__
        for record in records
        for example in examples
        if record.episode_id != example.episode_id
    ]
    write_jsonl(Path(args.output), labels)
    print(f"Wrote {len(labels)} teacher labels to {args.output}")


if __name__ == "__main__":
    main()
