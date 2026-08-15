from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    build_two_axis_rows,
    summarize_two_axis_rows,
    utc_now,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.prepare_state_conditioned_transition_6b import (
    _state_representation_cache,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _ensure_backend_tokenizer(
    backend: Any,
    *,
    tokenizer_loader: Any | None = None,
) -> Any:
    """Load the canonical tokenizer without materializing the frozen 8B model."""
    if backend.tokenizer is None:
        if tokenizer_loader is None:
            from transformers import AutoTokenizer

            tokenizer_loader = AutoTokenizer.from_pretrained
        backend.tokenizer = tokenizer_loader(
            backend.model_name,
            trust_remote_code=True,
        )
    if backend.tokenizer is None:
        raise RuntimeError("Canonical Qwen tokenizer was not loaded")
    return backend.tokenizer


def _validate_transition_representation_reuse(
    *,
    source_path: Path,
    panel_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = torch.load(source_path, map_location="cpu", weights_only=False)
    expected_ids = sorted(str(row["transition_id"]) for row in panel_rows)
    actual_ids = [str(value) for value in payload["ordered_transition_ids"]]
    if actual_ids != expected_ids:
        raise ValueError("Immutable transition representation IDs differ from the panel")
    if tuple(payload["representations"].shape) != (len(panel_rows), 4096):
        raise ValueError("Immutable transition representation shape differs")
    panel_by_id = {str(row["transition_id"]): row for row in panel_rows}
    errors = []
    for row in payload["rows"]:
        transition_id = str(row["transition_id"])
        source = panel_by_id[transition_id]
        for key in (
            "transition_content_sha256",
            "teacher_section_sha256",
            "source_task_goal_sha256",
            "canonical_pre_action_state_sha256",
            "complete_action_sha256",
            "complete_post_action_observation_sha256",
        ):
            if str(row[key]) != str(source[key]):
                errors.append({"transition_id": transition_id, "key": key})
    if errors:
        raise ValueError(f"Transition representation source hashes differ: {errors[:20]}")
    return {
        "format": "expanded_transition_representation_reuse_6d_v1",
        "passed": True,
        "source_path": str(source_path),
        "source_sha256": sha256_file(source_path),
        "transition_count": len(actual_ids),
        "shape": list(payload["representations"].shape),
        "representation_tensor_sha256": payload["representation_tensor_sha256"],
        "source_hash_errors": 0,
    }


def _report(summary: dict[str, Any]) -> str:
    cells = summary["two_axis_summary"]
    lines = [
        "# EXP-020 Expanded Two-Axis Data and Frozen Base Representations",
        "",
        "## VERIFIED",
        "",
        f"- scoreable rows: `{summary['scoreable_pair_count']}`",
        f"- query state representations: `{summary['state_representation']['shape']}`",
        f"- transition representation reuse passed: `{summary['transition_representation']['passed']}`",
        f"- validation tasks absent from cell A: `{summary['validation_tasks_absent_from_cell_a']}`",
        "",
        "| Cell | Pairs | States | State tasks | Transitions | Parents |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cell, value in cells.items():
        lines.append(
            f"| {cell} | {value['pair_count']} | {value['state_count']} | "
            f"{value['state_task_count']} | {value['transition_count']} | "
            f"{value['transition_parent_count']} |"
        )
    lines.extend(
        [
            "",
            "The 4096D state cache is a deterministic subset of the previously validated "
            "638-row frozen-Qwen cache. The 148 transition vectors are referenced read-only "
            "from EXP-018.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare EXP-020 two-axis rows")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_all_task_interaction_6d.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp020")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6d"]
    run_manifest = _load_json(args.artifact_dir / "run_manifest.json")
    teacher_summary = _load_json(args.artifact_dir / "teacher_summary.json")
    if teacher_summary["status"] != "completed":
        raise ValueError("Expanded teacher cache is not complete")
    existing_attempt_ids = {
        str(row["attempt_id"])
        for row in read_jsonl(args.artifact_dir / "attempts.jsonl")
    }
    if args.attempt_id in existing_attempt_ids:
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="expanded_two_axis_and_base_representations",
        command=[str(value) for value in __import__("sys").argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=str(run_manifest["config_sha256"]),
        data_manifest_hashes=run_manifest["data_manifest_hashes"],
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        exp017 = Path(settings["exp017_artifact"])
        exp018 = Path(settings["exp018_artifact"])
        source_data = Path(settings["source_data"])
        query_manifest = _load_json(args.artifact_dir / "expanded_query_manifest.json")
        panel_rows = _load_rows(exp017 / "transition_panel.jsonl")
        teacher_rows = _load_rows(args.artifact_dir / "teacher_cache.jsonl")
        parent_split = _load_json(exp018 / "transition_parent_split_manifest.json")
        two_axis_rows = build_two_axis_rows(
            teacher_rows=teacher_rows,
            panel_rows=panel_rows,
            query_manifest=query_manifest,
            parent_split=parent_split,
        )
        if len(two_axis_rows) != int(teacher_summary["counts"]["scoreable_pairs"]):
            raise ValueError("Expanded two-axis scoreable count differs from teacher cache")
        write_jsonl(args.artifact_dir / "two_axis_pair_rows.jsonl", two_axis_rows)
        two_axis_summary = summarize_two_axis_rows(two_axis_rows)
        atomic_write_json(args.artifact_dir / "two_axis_summary.json", two_axis_summary)
        attempt.progress(
            status="two_axis_rows_validated",
            pair_count=len(two_axis_rows),
            latest_validated_checkpoint=str(args.artifact_dir / "two_axis_pair_rows.jsonl"),
        )
        examples = load_decision_examples(source_data / "decision_examples.jsonl")
        backend = build_backend(cfg, load_model=False)
        _ensure_backend_tokenizer(backend)
        representation_dir = args.artifact_dir / "representation_cache"
        representation_dir.mkdir(parents=True, exist_ok=True)
        state_values, state_ids, state_report = _state_representation_cache(
            backend=backend,
            examples=examples,
            query_manifest=query_manifest,
            teacher_rows=teacher_rows,
            source_cache_path=Path(settings["full_state_representation_cache"]),
            decision_examples_path=source_data / "decision_examples.jsonl",
            prompt_profile=cfg.benchmark.prompt_profile,
            output_path=representation_dir / "query_state_representations.pt",
        )
        source_old = torch.load(
            exp018 / "representation_cache/query_state_representations.pt",
            map_location="cpu",
            weights_only=False,
        )
        old_position = {
            str(value): index
            for index, value in enumerate(source_old["ordered_state_example_ids"])
        }
        new_position = {value: index for index, value in enumerate(state_ids)}
        old_mismatches = []
        for state_id, old_index in old_position.items():
            if state_id not in new_position or not torch.equal(
                source_old["representations"][old_index],
                state_values[new_position[state_id]],
            ):
                old_mismatches.append(state_id)
        if old_mismatches:
            raise ValueError(
                f"Expanded state cache does not exactly preserve EXP-018: {old_mismatches[:20]}"
            )
        state_report["immutable_exp018_rows_exact"] = True
        state_report["immutable_exp018_row_count"] = len(old_position)
        state_report["tensor_sha256_recomputed"] = tensor_state_sha256(
            {"representations": state_values}
        )
        atomic_write_json(
            representation_dir / "query_state_representation_report.json",
            state_report,
        )
        transition_report = _validate_transition_representation_reuse(
            source_path=exp018 / "representation_cache/transition_representations.pt",
            panel_rows=panel_rows,
        )
        atomic_write_json(
            representation_dir / "transition_representation_reference.json",
            transition_report,
        )
        validation_tasks = set(query_manifest["validation_task_ids"])
        rows_a = [
            row
            for row in two_axis_rows
            if row["cell"] == "train_state__train_transition"
        ]
        validation_absent = all(
            str(row["state_task_id"]) not in validation_tasks for row in rows_a
        )
        if not validation_absent:
            raise RuntimeError("Validation state task leaked into cell A")
        summary = {
            "format": "all_task_two_axis_data_summary_6d_v1",
            "status": "completed",
            "run_uuid": str(settings["run_uuid"]),
            "source_commit": args.lambda_head,
            "scoreable_pair_count": len(two_axis_rows),
            "two_axis_summary": two_axis_summary,
            "state_representation": state_report,
            "transition_representation": transition_report,
            "validation_tasks_absent_from_cell_a": validation_absent,
            "hashes": {
                "teacher_cache": sha256_file(args.artifact_dir / "teacher_cache.jsonl"),
                "two_axis_pair_rows": sha256_file(args.artifact_dir / "two_axis_pair_rows.jsonl"),
                "two_axis_summary": sha256_file(args.artifact_dir / "two_axis_summary.json"),
                "query_state_representations": sha256_file(
                    representation_dir / "query_state_representations.pt"
                ),
                "transition_representation_source": transition_report["source_sha256"],
            },
            "hard_scope": {
                "qwen_model_loaded": False,
                "qwen_forward_calls": 0,
                "target_action_used_in_state_representation": False,
                "behavioral_program_training": False,
                "injector_training": False,
                "selector_training": False,
            },
            "timestamp_utc": utc_now(),
        }
        atomic_write_json(args.artifact_dir / "data_preparation_summary.json", summary)
        atomic_write_text(args.artifact_dir / "data_preparation_report.md", _report(summary))
        attempt.progress(
            status="expanded_data_preparation_completed",
            latest_validated_checkpoint=str(args.artifact_dir / "data_preparation_summary.json"),
        )
        print(json.dumps(two_axis_summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
