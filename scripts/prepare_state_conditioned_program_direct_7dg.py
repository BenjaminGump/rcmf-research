from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.state_conditioned_program_direct_7dg import (
    GLOBAL_SEED,
    require_global_seed,
    role_distribution,
    runtime_projection,
    target_geometry,
    task_grouped_split,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
    validate_or_record_run_manifest_config_supersession,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)
from scripts.run_state_conditioned_program_fast_7df import (
    _program_split,
    _row_file,
    _tensor_metrics,
    _validate_cached_teacher_row,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_state_conditioned_program_direct_7dg.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--parent-attempt-id", default="exp025df-finalize-001")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--tmux-session", default="exp025dg")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any]) -> dict[str, Path]:
    parent_fast = Path(str(settings["parent_exp025df"]))
    parent_preflight = Path(str(settings["parent_exp025d_preflight"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    return {
        "parent_fast_summary": parent_fast / "final_exp025df_summary.json",
        "parent_fast_validation": parent_fast / "postrun_validation.json",
        "parent_fast_program": parent_fast / "program/summary.json",
        "parent_fast_targets": parent_fast / "pair_latents/canonical_targets.pt",
        "parent_fast_a_pairs": parent_fast / "preflight/pairs_A.jsonl",
        "parent_fast_teacher_rows": parent_fast / "teacher_cache/rows",
        "clean_decoder": parent_fast / "decoder/repaired_rank128_decoder.pt",
        "clean_decoder_summary": parent_fast / "decoder/summary.json",
        "selector": parent_c / "selector/ensemble_scores.pt",
        **{
            f"pairs_{cell}": parent_preflight / f"preflight/pairs_{cell}.jsonl"
            for cell in "ABCDE"
        },
    }


def _validate_immutable(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    missing = {name: str(path) for name, path in paths.items() if not path.exists()}
    if missing:
        raise FileNotFoundError(f"Missing immutable EXP-025D-Direct inputs: {missing}")
    summary = _json(paths["parent_fast_summary"])
    validation = _json(paths["parent_fast_validation"])
    decoder = _json(paths["clean_decoder_summary"])
    selector_sha = sha256_file(paths["selector"])
    checks = {
        "parent_decision_is_latent_representation_failure": summary.get("decision_branch")
        == "state_transition_representations_insufficient",
        "parent_postrun_validation_passed": bool(validation.get("passed")),
        "clean_decoder_passed": bool(decoder.get("passed")),
        "clean_decoder_state_hash": str(decoder.get("decoder_sha256"))
        == str(settings["expected_clean_decoder_sha256"]),
        "selector_hash": selector_sha
        == str(settings["expected_selector_ensemble_sha256"]),
    }
    if not all(checks.values()):
        raise ValueError(f"Immutable EXP-025D-Direct validation failed: {checks}")
    return {
        "checks": checks,
        "selector_sha256": selector_sha,
        "clean_decoder_state_sha256": str(decoder["decoder_sha256"]),
        "clean_decoder_file_sha256": sha256_file(paths["clean_decoder"]),
        "passed": True,
    }


def _failure_audit(paths: Mapping[str, Path]) -> dict[str, Any]:
    program = _json(paths["parent_fast_program"])
    targets = torch.load(paths["parent_fast_targets"], map_location="cpu", weights_only=False)
    old_a = _rows(paths["parent_fast_a_pairs"])
    target_position = {
        str(pair_id): index for index, pair_id in enumerate(targets["pair_ids"])
    }
    a_targets = torch.stack(
        [targets["latents"][target_position[str(row["pair_id"])]] for row in old_a]
    ).to(torch.float32)
    train_indices, validation_indices, split = _program_split(old_a)
    mean_target = a_targets[train_indices].mean(dim=0, keepdim=True).expand(
        len(validation_indices), -1
    )
    mean_baseline = _tensor_metrics(mean_target, a_targets[validation_indices])
    architectures = program["architectures"]
    pairmlp = architectures["pair_mlp_observation_excluded"]
    factorized = architectures["full_factorized_r16_observation_excluded"]
    pair_train = pairmlp["train"]
    pair_validation = pairmlp["validation"]
    if float(pair_train["mse_reduction_vs_zero"]) < 0.20:
        fit_classification = "failed_to_fit_training_data_and_validation_collapsed_to_zero"
    elif float(pair_validation["mse_reduction_vs_zero"]) < 0.05:
        fit_classification = "fit_training_but_failed_generalization"
    else:
        fit_classification = "noncollapsed_generalization"
    return {
        "format": "latent_distillation_failure_audit_7dg_v1",
        "timebox_hours": 2,
        "pairmlp": {
            "train": pair_train,
            "validation": pair_validation,
            "fit_classification": fit_classification,
            "validation_close_to_zero": abs(
                float(pair_validation["mse_reduction_vs_zero"])
            )
            < 0.01,
        },
        "factorized": {
            "train": factorized["train"],
            "validation": factorized["validation"],
        },
        "target_geometry": target_geometry(a_targets),
        "old_split": {
            **split,
            "train_role_distribution": role_distribution(old_a, train_indices),
            "validation_role_distribution": role_distribution(
                old_a, validation_indices
            ),
        },
        "mean_target_baseline_validation": mean_baseline,
        "implementation_or_split_bug_verified": False,
        "proceed_to_direct_behavior": True,
    }


def _scoreable_manifests(
    settings: Mapping[str, Any], paths: Mapping[str, Path], output_root: Path
) -> dict[str, list[dict[str, Any]]]:
    expected = settings["expected"]
    output: dict[str, list[dict[str, Any]]] = {}
    for cell in "ABCDE":
        rows = [
            row
            for row in _rows(paths[f"pairs_{cell}"])
            if str(row.get("score_status")) == "scoreable"
            and bool(row.get("valid_for_teacher_cache"))
            and not bool(row.get("truncated"))
        ]
        expected_count = int(expected[f"scoreable_{cell}_pairs"])
        if len(rows) != expected_count:
            raise ValueError(
                f"Cell {cell} scoreable count {len(rows)} != {expected_count}"
            )
        pair_ids = [str(row["pair_id"]) for row in rows]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError(f"Cell {cell} contains duplicate pair IDs")
        write_jsonl(output_root / f"pairs_{cell}.jsonl", rows)
        output[cell] = rows
    return output


def _teacher_reuse(
    *,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    manifests: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    unique = {
        str(row["pair_id"]): dict(row)
        for rows in manifests.values()
        for row in rows
    }
    reusable = []
    for pair_id, pair in sorted(unique.items()):
        path = _row_file(paths["parent_fast_teacher_rows"], pair_id)
        if not path.exists():
            continue
        row = _json(path)
        _validate_cached_teacher_row(row, pair, settings)
        reusable.append(pair_id)
    return {
        "format": "direct_behavior_teacher_reuse_preflight_7dg_v1",
        "logical_scoreable_rows": sum(len(rows) for rows in manifests.values()),
        "unique_scoreable_rows": len(unique),
        "reusable_top64_rows": len(reusable),
        "new_top64_rows": len(unique) - len(reusable),
        "reusable_pair_ids": reusable,
        "reuse_source": str(paths["parent_fast_teacher_rows"]),
    }


def _report(summary: Mapping[str, Any]) -> str:
    split = summary["a_split"]
    reuse = summary["teacher_cache"]
    runtime = summary["runtime_projection"]
    audit = summary["quick_failure_audit"]
    return "\n".join(
        [
            "# EXP-025D-Direct preflight",
            "",
            f"- run UUID: `{summary['run_uuid']}`",
            f"- global seed: `{summary['global_seed']}`",
            f"- A train/validation pairs: `{split['train_pair_count']}/{split['validation_pair_count']}`",
            f"- A train/validation tasks: `{split['train_task_count']}/{split['validation_task_count']}`",
            f"- unique teacher rows reusable/new: `{reuse['reusable_top64_rows']}/{reuse['new_top64_rows']}`",
            f"- PairMLP old-fit classification: `{audit['pairmlp']['fit_classification']}`",
            f"- expected H100 hours: `{runtime['scenarios']['expected']['h100_hours']:.4f}`",
            f"- automatic launch allowed: `{summary['automatic_launch_allowed']}`",
            f"- projected artifact bytes: `{summary['projected_artifact_bytes']}`",
            "",
        ]
    )


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7dg"]
    seed = require_global_seed(int(settings["global_seed"]))
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = _paths(settings)
    data_hashes = {
        name: sha256_file(path)
        for name, path in paths.items()
        if path.is_file()
    }
    config_sha256 = sha256_file(args.config)
    run_manifest_path = args.artifact_dir / "run_manifest.json"
    command_scope = ["preflight", "pairmlp", "factorized", "one_step", "finalize"]
    if run_manifest_path.exists():
        current_manifest = _json(run_manifest_path)
        initial_config_sha256 = str(current_manifest["config_sha256"])
        if initial_config_sha256 != config_sha256:
            supersession_path = args.artifact_dir / "run_manifest_supersessions.jsonl"
            supersessions = _rows(supersession_path) if supersession_path.exists() else []
            previous_config_sha256 = (
                str(supersessions[-1]["replacement_config_sha256"])
                if supersessions
                else initial_config_sha256
            )
            validate_or_record_run_manifest_config_supersession(
                run_manifest_path,
                run_uuid=str(settings["run_uuid"]),
                previous_config_sha256=previous_config_sha256,
                replacement_config_sha256=config_sha256,
                data_manifest_hashes=data_hashes,
                source_commit=args.lambda_head,
                command_scope=command_scope,
                parent_attempt_id=args.parent_attempt_id,
                reason=(
                    "Corrected a 65-character selector identity typo to the "
                    "verified immutable 64-character SHA256; no scientific "
                    "parameter or artifact changed."
                ),
                supersession_path=supersession_path,
            )
        else:
            initialize_or_validate_run_manifest(
                run_manifest_path,
                run_uuid=str(settings["run_uuid"]),
                config_sha256=config_sha256,
                data_manifest_hashes=data_hashes,
                source_commit=args.lambda_head,
                command_scope=command_scope,
            )
    else:
        initialize_or_validate_run_manifest(
            run_manifest_path,
            run_uuid=str(settings["run_uuid"]),
            config_sha256=config_sha256,
            data_manifest_hashes=data_hashes,
            source_commit=args.lambda_head,
            command_scope=command_scope,
        )
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="cpu_preflight_and_failure_audit",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_sha256,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        immutable = _validate_immutable(settings, paths)
        quick_audit = _failure_audit(paths)
        preflight_root = args.artifact_dir / "preflight"
        preflight_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(preflight_root / "quick_failure_audit.json", quick_audit)
        manifests = _scoreable_manifests(settings, paths, preflight_root)
        split = task_grouped_split(
            manifests["A"],
            seed=seed,
            validation_fraction=float(settings["split"]["validation_fraction"]),
        )
        split["train_role_distribution"] = role_distribution(
            manifests["A"], split["train_indices"]
        )
        split["validation_role_distribution"] = role_distribution(
            manifests["A"], split["validation_indices"]
        )
        for partition, indices in (
            ("train", split["train_indices"]),
            ("validation", split["validation_indices"]),
        ):
            split[f"{partition}_transition_count"] = len(
                {str(manifests["A"][index]["transition_id"]) for index in indices}
            )
            split[f"{partition}_parent_count"] = len(
                {
                    str(manifests["A"][index]["transition_parent_id"])
                    for index in indices
                }
            )
        atomic_write_json(preflight_root / "a_task_split.json", split)
        reuse = _teacher_reuse(
            settings=settings, paths=paths, manifests=manifests
        )
        atomic_write_json(preflight_root / "teacher_cache_reuse.json", reuse)
        evaluation_pairs = sum(len(manifests[cell]) for cell in "BCDE")
        runtime = runtime_projection(
            train_pairs=int(split["train_pair_count"]),
            validation_pairs=int(split["validation_pair_count"]),
            evaluation_pairs=evaluation_pairs,
            new_teacher_rows=int(reuse["new_top64_rows"]),
            one_step_conditions=int(settings["one_step"]["total_new_conditions"]),
            rates=settings["runtime"]["rates"],
        )
        automatic = (
            float(runtime["scenarios"]["expected"]["h100_hours"])
            <= float(settings["runtime"]["review_threshold_h100_hours"])
        )
        projected_bytes = (
            int(reuse["new_top64_rows"])
            * int(settings["runtime"]["projected_bytes_per_teacher_row"])
            + 4 * int(settings["runtime"]["projected_bytes_per_checkpoint"])
        )
        summary = {
            "format": "state_conditioned_program_direct_preflight_7dg_v1",
            "status": "completed_ready_for_gpu" if automatic else "runtime_review_required",
            "run_uuid": str(settings["run_uuid"]),
            "global_seed": seed,
            "source_commit": args.lambda_head,
            "immutable_validation": immutable,
            "quick_failure_audit": quick_audit,
            "cell_pair_counts": {cell: len(rows) for cell, rows in manifests.items()},
            "a_split": split,
            "teacher_cache": reuse,
            "runtime_projection": runtime,
            "review_threshold_h100_hours": float(
                settings["runtime"]["review_threshold_h100_hours"]
            ),
            "automatic_launch_allowed": automatic,
            "projected_artifact_bytes": projected_bytes,
            "qwen_loaded": False,
            "qwen_forward_count": 0,
            "gpu_used": False,
        }
        atomic_write_json(args.artifact_dir / "preflight_summary.json", summary)
        atomic_write_text(args.artifact_dir / "preflight_report.md", _report(summary))
        attempt.progress(
            status=summary["status"],
            latest_validated_checkpoint=str(args.artifact_dir / "preflight_summary.json"),
        )
        print(json.dumps({
            "status": summary["status"],
            "run_uuid": summary["run_uuid"],
            "global_seed": seed,
            "cell_pair_counts": summary["cell_pair_counts"],
            "a_train_pairs": split["train_pair_count"],
            "a_validation_pairs": split["validation_pair_count"],
            "reusable_teacher_rows": reuse["reusable_top64_rows"],
            "new_teacher_rows": reuse["new_top64_rows"],
            "expected_h100_hours": runtime["scenarios"]["expected"]["h100_hours"],
            "projected_artifact_bytes": projected_bytes,
        }, indent=2), flush=True)


if __name__ == "__main__":
    main()
