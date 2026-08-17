from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_legacy_replay_6h1 import (
    build_replay_contract,
    build_sentinel_manifest,
    canonical_hash,
    directory_manifest,
    sha256_file,
)
from rcmf.training.datasets import load_decision_examples, load_memory_records
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    log_path: Path,
    timeout: int = 7200,
) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [str(value) for value in command],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=dict(env),
        timeout=timeout,
        check=False,
    )
    atomic_write_text(log_path, completed.stdout)
    result = {
        "command": [str(value) for value in command],
        "exit_code": completed.returncode,
        "elapsed_seconds": time.perf_counter() - started,
        "log_path": str(log_path),
        "log_sha256": sha256_file(log_path),
    }
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}); see {log_path}: {command}")
    return result


def _download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != expected_sha256:
            raise ValueError(f"Existing wheel hash differs: {destination}")
        return
    temporary = destination.with_suffix(destination.suffix + f".partial.{os.getpid()}")
    with urlopen(url, timeout=120) as source, temporary.open("wb") as target:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            target.write(block)
        target.flush()
        os.fsync(target.fileno())
    actual = sha256_file(temporary)
    if actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Downloaded AppWorld wheel hash differs: {actual}")
    os.replace(temporary, destination)


def _example_map(examples: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for index, example in enumerate(examples):
        identity = state_example_id(index, example)
        if identity in output:
            raise ValueError(f"Duplicate decision state: {identity}")
        output[identity] = example
    return output


def _record_map(records: Sequence[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for record in records:
        task_id = str(record.task_id)
        if task_id in output:
            raise ValueError(f"Duplicate source trajectory task: {task_id}")
        output[task_id] = record
    return output


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _build_contracts(
    *,
    queries: Sequence[Mapping[str, Any]],
    examples: Sequence[Any],
    records: Sequence[Any],
    old_rows: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    artifact_dir: Path,
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    by_state = _example_map(examples)
    by_task = _record_map(records)
    old_by_state = {str(row["state_example_id"]): row for row in old_rows}
    query_ids = [str(row["state_example_id"]) for row in queries]
    if len(set(query_ids)) != len(query_ids) or set(query_ids) != set(old_by_state):
        raise ValueError("Immutable query and EXP-024A replay state identities differ")
    contracts_dir = artifact_dir / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    replay = settings["replay"]
    legacy = settings["legacy"]
    for query in sorted(queries, key=lambda row: str(row["state_example_id"])):
        state_id = str(query["state_example_id"])
        task_id = str(query["task_id"])
        experiment_name = f"exp024r_{settings['run_uuid']}_{_safe_name(state_id)}_fresh"
        contract = build_replay_contract(
            query=query,
            example=by_state[state_id],
            record=by_task[task_id],
            legacy_python=Path(legacy["executable"]),
            appworld_root=Path(legacy["root"]),
            experiment_name=experiment_name,
            random_seed=int(replay["random_seed"]),
            max_interactions=int(replay["max_interactions"]),
            max_api_calls_per_interaction=int(replay["max_api_calls_per_interaction"]),
            source_hashes={
                **source_hashes,
                "old_replay_row": canonical_hash(old_by_state[state_id]),
            },
        )
        path = contracts_dir / f"{_safe_name(state_id)}.json"
        if path.exists() and _load_json(path) != contract:
            raise ValueError(f"Immutable replay contract changed: {path}")
        atomic_write_json(path, contract)
        rows.append(
            {
                "state_example_id": state_id,
                "task_id": task_id,
                "step_id": int(query["step_id"]),
                "history_step_count": int(contract["history_step_count"]),
                "contract_path": str(path),
                "contract_sha256": canonical_hash(contract),
            }
        )
    manifest = {
        "format": "appworld_legacy_replay_contract_manifest_6h1_v1",
        "state_count": len(rows),
        "task_count": len({row["task_id"] for row in rows}),
        "prior_observation_count": sum(row["history_step_count"] for row in rows),
        "rows": rows,
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    return manifest


def _freeze_wheels(wheel_dir: Path) -> dict[str, Any]:
    wheels = sorted(path for path in wheel_dir.rglob("*") if path.is_file())
    rows = [
        {"filename": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in wheels
    ]
    return {
        "file_count": len(rows),
        "total_bytes": sum(row["size"] for row in rows),
        "files": rows,
        "manifest_sha256": canonical_hash(rows),
    }


def _wheel_metadata(wheel: Path, provenance_dir: Path) -> dict[str, Any]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError(f"Expected one wheel METADATA entry, found {metadata_names}")
        metadata_text = archive.read(metadata_names[0]).decode("utf-8")
        lock_candidates = [
            name
            for name in archive.namelist()
            if any(token in Path(name).name.lower() for token in ("lock", "requirements"))
        ]
    metadata_path = provenance_dir / "appworld-0.1.0-wheel-METADATA.txt"
    atomic_write_text(metadata_path, metadata_text)
    requires_dist = [
        line.partition(":")[2].strip()
        for line in metadata_text.splitlines()
        if line.startswith("Requires-Dist:")
    ]
    requires_python = next(
        (
            line.partition(":")[2].strip()
            for line in metadata_text.splitlines()
            if line.startswith("Requires-Python:")
        ),
        None,
    )
    return {
        "metadata_path": str(metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "requires_python": requires_python,
        "requires_dist": requires_dist,
        "release_lock_candidates": lock_candidates,
        "release_lock_available": bool(lock_candidates),
    }


def _select_compatible_runtime(
    requested: Mapping[str, Any], wheel: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    effective = dict(requested)
    with zipfile.ZipFile(wheel) as archive:
        environment_source = archive.read("appworld/environment.py").decode("utf-8")
    imports_typing_self = bool(
        re.search(r"from\s+typing\s+import[^\n]*\bSelf\b", environment_source)
    )
    requested_version = tuple(int(value) for value in str(requested["python_version"]).split("."))
    changed = imports_typing_self and requested_version < (3, 11)
    if changed:
        effective.update(
            {
                "seed_python": "/usr/bin/python3.11",
                "python_version": "3.11",
                "venv": str(requested["venv"]) + "-py311",
                "executable": str(requested["venv"]) + "-py311/bin/python",
                "appworld_cli": str(requested["venv"]) + "-py311/bin/appworld",
            }
        )
    return effective, {
        "requested_python_version": str(requested["python_version"]),
        "effective_python_version": str(effective["python_version"]),
        "wheel_imports_typing_self": imports_typing_self,
        "runtime_changed": changed,
        "reason": (
            "official_0_1_0_source_imports_typing_Self_which_requires_python_3_11"
            if changed
            else "requested_runtime_is_source_compatible"
        ),
        "scientific_parameter_changed": False,
    }


def _verification_passed(text: str, exit_code: int, *, kind: str) -> bool:
    lowered = text.lower()
    fatal_markers = (
        "traceback (most recent call last)",
        "some tests failed",
        "verification failed",
    )
    if exit_code != 0 or any(marker in lowered for marker in fatal_markers):
        return False
    if kind == "tests":
        return "all tests passed." in lowered
    if kind == "tasks":
        match = re.search(r"passed\s+(\d+)\s*/\s*(\d+)\s+tasks", lowered)
        return bool(
            match
            and int(match.group(2)) > 0
            and int(match.group(1)) == int(match.group(2))
            and "failed task_ids" not in lowered
        )
    raise ValueError(f"Unknown AppWorld verification kind: {kind}")


def _source_versions(records_path: Path, task_ids: set[str]) -> dict[str, Any]:
    rows = {
        str(row["task_id"]): row
        for row in read_jsonl(records_path)
        if str(row["task_id"]) in task_ids
    }
    if set(rows) != task_ids:
        raise ValueError(f"Missing source trajectories: {sorted(task_ids - set(rows))}")
    by_task = {}
    for task_id, row in sorted(rows.items()):
        source_path = Path(str(row["metadata"]["source_path"]))
        task_root = source_path.parents[1]
        markers = {
            "code": task_root / "version" / "code.txt",
            "data": task_root / "version" / "data.txt",
            "evaluation": task_root / "evaluation" / "version.txt",
        }
        by_task[task_id] = {
            name: path.read_text(encoding="utf-8").strip() for name, path in markers.items()
        }
    return {
        "by_task": by_task,
        "versions": {
            name: sorted({row[name] for row in by_task.values()})
            for name in ("code", "data", "evaluation")
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_legacy_replay_6h1.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r")
    parser.add_argument("--parent-attempt-id")
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h1"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    source = Path(settings["source_data"])
    parent = Path(settings["parent_exp024a"])
    exp022 = Path(settings["exp022_artifact"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
        "queries": exp022 / "one_step_query_manifest.json",
        "old_replay_summary": parent / "replay" / "replay_summary.json",
        "old_final_summary": parent / "final_exp024a_summary.json",
        "old_run_manifest": parent / "run_manifest.json",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    old_state_paths = sorted((parent / "replay" / "states").glob("*.json"))
    expected = settings["expected"]
    if len(old_state_paths) != int(expected["state_count"]):
        raise ValueError("EXP-024A immutable replay state count changed")
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    data_hashes["old_replay_state_set"] = canonical_hash(
        [{"name": path.name, "sha256": sha256_file(path)} for path in old_state_paths]
    )
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "isolated_appworld_0_1_0_environment",
            "immutable_45_state_ground_truth_replay_only",
            "no_qwen_forward_or_generation",
            "no_memory_condition_execution",
        ],
    )

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="legacy_environment_reconstruction",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        queries = list(_load_json(paths["queries"])["rows"])
        old_rows = [_load_json(path) for path in old_state_paths]
        old_summary = _load_json(paths["old_replay_summary"])
        checks = {
            "states": len(queries) == int(expected["state_count"]),
            "tasks": len({str(row["task_id"]) for row in queries}) == int(expected["task_count"]),
            "prior_observations": sum(int(row["step_id"]) - 1 for row in queries)
            == int(expected["prior_observation_count"]),
            "conditions": _load_json(paths["old_final_summary"])["planned_condition_count"]
            == int(expected["condition_count"]),
            "old_replay_pass": old_summary["passed_state_count"]
            == int(expected["old_replay_pass_count"]),
        }
        if not all(checks.values()):
            raise ValueError(f"EXP-024A immutable contract failed: {checks}")

        sentinel = build_sentinel_manifest(old_rows)
        atomic_write_json(args.artifact_dir / "sentinel_manifest.json", sentinel)

        requested_legacy = settings["legacy"]
        legacy = dict(requested_legacy)
        base = Path(legacy["base"])
        root = Path(legacy["root"])
        cache = Path(legacy["cache"])
        wheel_dir = Path(legacy["wheels"])
        locks = Path(legacy["locks"])
        provenance_dir = Path(legacy["provenance"])
        for path in (base, root, cache, wheel_dir, locks, provenance_dir):
            path.mkdir(parents=True, exist_ok=True)
        wheel = wheel_dir / str(legacy["wheel_filename"])
        _download_verified(str(legacy["wheel_url"]), wheel, str(legacy["wheel_sha256"]))
        wheel_metadata = _wheel_metadata(wheel, provenance_dir)
        attempt.progress(latest_validated_checkpoint=str(wheel))

        legacy, runtime_compatibility = _select_compatible_runtime(requested_legacy, wheel)
        effective_settings = dict(settings)
        effective_settings["legacy"] = legacy
        examples = load_decision_examples(paths["decision_examples"])
        records = load_memory_records(paths["memory_records"])
        version_suffix = str(legacy["python_version"]).replace(".", "")
        contract_namespace = (
            f"contracts_py{version_suffix}"
            if runtime_compatibility["runtime_changed"]
            else "contracts"
        )
        contracts = _build_contracts(
            queries=queries,
            examples=examples,
            records=records,
            old_rows=old_rows,
            settings=effective_settings,
            artifact_dir=args.artifact_dir / contract_namespace,
            source_hashes=data_hashes,
        )
        contract_manifest_path = args.artifact_dir / (
            f"replay_contract_manifest_py{version_suffix}.json"
            if runtime_compatibility["runtime_changed"]
            else "replay_contract_manifest.json"
        )
        atomic_write_json(contract_manifest_path, contracts)
        attempt.progress(latest_validated_checkpoint=str(contract_manifest_path))

        seed_python = Path(legacy["seed_python"])
        venv = Path(legacy["venv"])
        legacy_python = Path(legacy["executable"])
        logs = args.artifact_dir / "environment" / "logs" / _safe_name(args.attempt_id)
        logs.mkdir(parents=True, exist_ok=True)
        base_env = dict(os.environ)
        base_env.update(
            {
                "APPWORLD_ROOT": str(root),
                "APPWORLD_CACHE": str(cache),
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "",
                "PYTHONUNBUFFERED": "1",
            }
        )
        command_results = []
        if not legacy_python.exists():
            venv.parent.mkdir(parents=True, exist_ok=True)
            command_results.append(
                _run(
                    [str(seed_python), "-m", "venv", str(venv)],
                    env=base_env,
                    log_path=logs / "create_venv.log",
                )
            )
        resolved = wheel_dir / "resolved"
        resolved.mkdir(parents=True, exist_ok=True)
        command_results.append(
            _run(
                [
                    str(legacy_python),
                    "-m",
                    "pip",
                    "download",
                    "--dest",
                    str(resolved),
                    str(wheel),
                ],
                env=base_env,
                log_path=logs / "pip_download.log",
            )
        )
        command_results.append(
            _run(
                [
                    str(legacy_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--find-links",
                    str(resolved),
                    str(wheel),
                ],
                env=base_env,
                log_path=logs / "pip_install.log",
            )
        )
        appworld_cli = Path(legacy["appworld_cli"])
        command_results.append(
            _run(
                [str(appworld_cli), "install"],
                env=base_env,
                log_path=logs / "appworld_install.log",
            )
        )
        if not (root / "data").is_dir():
            try:
                command_results.append(
                    _run(
                        [str(appworld_cli), "download", "data", "--root", str(root)],
                        env=base_env,
                        log_path=logs / "appworld_download_data.log",
                    )
                )
            except Exception as error:
                atomic_write_json(
                    args.artifact_dir / "environment_failure.json",
                    {
                        "decision_branch": "appworld_010_data_bundle_unavailable",
                        "error": str(error),
                        "data_bundle_url": legacy["data_bundle_url"],
                    },
                )
                raise

        probe_path = args.artifact_dir / "environment" / "legacy_probe.json"
        command_results.append(
            _run(
                [
                    str(legacy_python),
                    "scripts/appworld_legacy_probe_6h1.py",
                    "--output",
                    str(probe_path),
                    "--expected-python",
                    str(legacy_python),
                    "--expected-root",
                    str(root),
                ],
                env=base_env,
                log_path=logs / "legacy_probe.log",
            )
        )
        probe = _load_json(probe_path)
        if probe["appworld_version"] != "0.1.0" or probe["db_version"] != "0.1.0":
            raise ValueError(f"Legacy code/data version mismatch: {probe}")

        verify_results = {}
        for kind in ("tests", "tasks"):
            log_path = logs / f"appworld_verify_{kind}.log"
            result = _run(
                [str(appworld_cli), "verify", kind, "--root", str(root)],
                env=base_env,
                log_path=log_path,
            )
            text = log_path.read_text(encoding="utf-8", errors="replace")
            result["verified_pass"] = _verification_passed(
                text, int(result["exit_code"]), kind=kind
            )
            if not result["verified_pass"]:
                raise RuntimeError(f"Official AppWorld verification failed: {kind}")
            verify_results[kind] = result

        freeze_log = locks / "pip-freeze.txt"
        freeze_result = _run(
            [str(legacy_python), "-m", "pip", "freeze", "--all"],
            env=base_env,
            log_path=freeze_log,
        )
        wheel_manifest = _freeze_wheels(wheel_dir)
        atomic_write_json(provenance_dir / "wheel_manifest.json", wheel_manifest)
        root_manifest = directory_manifest(root)
        atomic_write_json(provenance_dir / "appworld_root_manifest.json", root_manifest)
        task_ids = {str(row["task_id"]) for row in queries}
        source_versions = _source_versions(paths["memory_records"], task_ids)
        expected_version = "0.1.0"
        if any(values != [expected_version] for values in source_versions["versions"].values()):
            raise ValueError(f"Immutable source version markers changed: {source_versions}")

        task_locations = {}
        task_manifests = {}
        for task_id in sorted(task_ids):
            matches = sorted(
                path for path in root.rglob(task_id) if path.is_dir() and path.name == task_id
            )
            if not matches:
                raise FileNotFoundError(f"Legacy data lacks required task: {task_id}")
            task_locations[task_id] = [str(path) for path in matches]
            task_manifests[task_id] = [directory_manifest(path) for path in matches]
        reconstruction = "\n".join(
            [
                "# AppWorld 0.1.0 Replay Capsule Reconstruction",
                "",
                f"1. Create Python {legacy['python_version']} venv at `{venv}`.",
                f"2. Verify `{wheel}` has SHA256 `{legacy['wheel_sha256']}`.",
                f"3. Install with `pip --no-index --find-links {resolved}`.",
                f"4. Set `APPWORLD_ROOT={root}` and `APPWORLD_CACHE={cache}`.",
                f"5. Run `{appworld_cli} install`.",
                f"6. Run `{appworld_cli} download data --root {root}`.",
                f"7. Run official `verify tests` and `verify tasks` against `{root}`.",
                "",
                "The current RCMF environment is never modified or imported by the legacy bridge.",
            ]
        )
        atomic_write_text(
            args.artifact_dir / "environment_reconstruction_instructions.md",
            reconstruction + "\n",
        )
        provenance = {
            "format": "appworld_legacy_environment_provenance_6h1_v1",
            "run_uuid": settings["run_uuid"],
            "source_commit": args.lambda_head,
            "legacy_python": str(legacy_python),
            "seed_python": str(seed_python),
            "legacy_pip": str(venv / "bin" / "pip"),
            "legacy_cli": str(appworld_cli),
            "legacy_root": str(root),
            "legacy_cache": str(cache),
            "requested_legacy_runtime": dict(requested_legacy),
            "runtime_compatibility": runtime_compatibility,
            "wheel": {
                "path": str(wheel),
                "sha256": sha256_file(wheel),
                "required_sha256": legacy["wheel_sha256"],
            },
            "wheel_metadata": wheel_metadata,
            "probe": probe,
            "source_versions": source_versions,
            "task_locations": task_locations,
            "task_manifests": task_manifests,
            "wheel_manifest": wheel_manifest,
            "pip_freeze": {
                "path": str(freeze_log),
                "sha256": sha256_file(freeze_log),
            },
            "root_manifest": {
                "path": str(provenance_dir / "appworld_root_manifest.json"),
                "file_count": root_manifest["file_count"],
                "total_bytes": root_manifest["total_bytes"],
                "manifest_sha256": root_manifest["manifest_sha256"],
            },
            "official_verification": verify_results,
            "commands": command_results + [freeze_result],
            "contract_manifest_sha256": contracts["manifest_sha256"],
            "active_contract_manifest": str(contract_manifest_path),
            "sentinel_manifest_sha256": sentinel["manifest_sha256"],
            "qwen_import_count": 0,
            "qwen_forward_count": 0,
        }
        atomic_write_json(args.artifact_dir / "environment_provenance.json", provenance)
        attempt.progress(latest_validated_checkpoint="environment_provenance.json")
        print(json.dumps(provenance, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
