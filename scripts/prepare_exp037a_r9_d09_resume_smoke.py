from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

import _bootstrap  # noqa: F401
import yaml

from rcmf.utils.serialization import atomic_write_json, sha256_file


EXPECTED_CHECKPOINT_SHA256 = (
    "60c40ca73ecdc7f8fea15ec50e87bca28ad8efcd4c33d98b91ea282273c2bd40"
)
DIAGNOSTIC_RUN_UUID = "exp037a_r9_d09_one_unit_resume_20260904_001"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    return parser.parse_args()


def _copy_file(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    shutil.copy2(source, destination)
    destination_hash = sha256_file(destination)
    if source_hash != destination_hash:
        raise RuntimeError(f"Diagnostic copy differs: {source} -> {destination}")
    return {
        "source": str(source),
        "destination": str(destination),
        "size_bytes": source.stat().st_size,
        "sha256": source_hash,
        "source_mtime_ns_before": source.stat().st_mtime_ns,
    }


def _source_files(formal_root: Path) -> list[tuple[Path, Path]]:
    arm = formal_root / "arms/3d"
    rows = [
        (path, Path("arm/data") / path.name)
        for path in sorted((arm / "data").iterdir())
        if path.is_file()
    ]
    rows.extend(
        [
            (arm / "runtime/formal_gpu_preflight.json", Path("arm/runtime/formal_gpu_preflight.json")),
            (arm / "joint_training/training_unit_manifest.json", Path("arm/joint_training/training_unit_manifest.json")),
            (arm / "joint_training/state_query_shuffle_manifest.json", Path("arm/joint_training/state_query_shuffle_manifest.json")),
            (arm / "joint_training/zero_policy_nll_summary.json", Path("arm/joint_training/zero_policy_nll_summary.json")),
            (arm / "paired_causal/paired_outcomes.json", Path("parent_exp028a/paired_causal/paired_outcomes.json")),
            (arm / "structured_compiler/policy_teacher_cache.pt", Path("parent_exp028a/structured_compiler/policy_teacher_cache.pt")),
            (formal_root / "shared/compat_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl", Path("parent_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl")),
            (formal_root / "preflight/initialization_snapshots/writer_initial.pt", Path("initialization/writer_initial.pt")),
            (formal_root / "preflight/initialization_snapshots/reader_initial.pt", Path("initialization/reader_initial.pt")),
            (arm / "joint_training/checkpoints/epoch_01.pt", Path("arm/joint_training/checkpoints/epoch_01.pt")),
        ]
    )
    rows.extend(
        (path, Path("arm/joint_training/zero_policy_nll") / path.name)
        for path in sorted((arm / "joint_training/zero_policy_nll").iterdir())
        if path.is_file()
    )
    return rows


def prepare(formal_root: Path, diagnostic_root: Path) -> dict[str, Any]:
    if diagnostic_root.exists():
        raise FileExistsError(f"Diagnostic root already exists: {diagnostic_root}")
    checkpoint = formal_root / "arms/3d/joint_training/checkpoints/epoch_01.pt"
    checkpoint_sha = sha256_file(checkpoint)
    if checkpoint_sha != EXPECTED_CHECKPOINT_SHA256:
        raise ValueError(f"D09 checkpoint identity differs: {checkpoint_sha}")
    diagnostic_root.mkdir(parents=True)
    copied = [_copy_file(source, diagnostic_root / relative) for source, relative in _source_files(formal_root)]
    source_config = formal_root / "resolved_configs/arm_3d.yaml"
    config = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    settings = config["stage_c_9a"]
    settings["run_uuid"] = DIAGNOSTIC_RUN_UUID
    settings["parent_exp025b"] = str(diagnostic_root / "parent_exp025b")
    settings["parent_exp028a"] = str(diagnostic_root / "parent_exp028a")
    settings["prompt_dependent_inputs"]["outcomes"] = str(diagnostic_root / "parent_exp028a/paired_causal/paired_outcomes.json")
    settings["prompt_dependent_inputs"]["teacher_cache"] = str(diagnostic_root / "parent_exp028a/structured_compiler/policy_teacher_cache.pt")
    config_path = diagnostic_root / "config/arm_3d_d09_resume.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    diagnostic_checkpoint = diagnostic_root / "arm/joint_training/checkpoints/epoch_01.pt"
    atomic_write_json(
        diagnostic_root / "arm/joint_training/latest_checkpoint.json",
        {"checkpoint": str(diagnostic_checkpoint), "checkpoint_sha256": sha256_file(diagnostic_checkpoint), "completed_units": 576, "epoch": 1},
    )
    source_unchanged = all(
        sha256_file(Path(row["source"])) == row["sha256"]
        and Path(row["source"]).stat().st_mtime_ns == row["source_mtime_ns_before"]
        for row in copied
    )
    manifest = {
        "format": "exp037a_r9_d09_resume_smoke_preparation_v1",
        "diagnostic_only": True,
        "scientific_checkpoint_input_for_14i": False,
        "formal_root_read_only": str(formal_root),
        "diagnostic_root": str(diagnostic_root),
        "diagnostic_run_uuid": DIAGNOSTIC_RUN_UUID,
        "source_checkpoint_sha256": checkpoint_sha,
        "source_config": {"path": str(source_config), "sha256": sha256_file(source_config)},
        "diagnostic_config": {"path": str(config_path), "sha256": sha256_file(config_path), "path_changes_only": True},
        "copied_files": copied,
        "source_files_unchanged_after_copy": source_unchanged,
        "passed": source_unchanged,
    }
    atomic_write_json(diagnostic_root / "preparation_manifest.json", manifest)
    return manifest


def main() -> None:
    args = _parse_args()
    print(json.dumps(prepare(args.formal_root, args.diagnostic_root), sort_keys=True))


if __name__ == "__main__":
    main()
