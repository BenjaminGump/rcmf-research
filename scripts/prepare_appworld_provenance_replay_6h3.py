from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_provenance_replay_6h3 import (
    QUARANTINED_TASK_ID,
    SNAPSHOT_SEARCH_VERSION,
    build_quarantine_manifest,
    build_quarantine_sentinel,
    classify_provenance_failure,
    redacted_path,
    select_preflight_branch,
    summarize_corpus_identity,
    text_sha256,
    training_contamination_report,
)
from rcmf.training.appworld_semantic_replay_6h2 import (
    canonical_hash,
    identity_hashes,
    parse_full_demo_query,
)
from rcmf.training.datasets import (
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _manifest_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("rows", "query_rows"):
        if isinstance(payload.get(key), list):
            return [dict(row) for row in payload[key]]
    raise ValueError("Manifest has no rows/query_rows")


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _spec_fields(path: Path) -> dict[str, str]:
    payload = _load_json(path)
    supervisor = dict(payload["supervisor"])
    return {
        "instruction": str(payload["instruction"]),
        "first_name": str(supervisor["first_name"]),
        "last_name": str(supervisor["last_name"]),
        "email": str(supervisor["email"]),
        "phone_number": str(supervisor["phone_number"]),
    }


def _field_hashes(fields: Mapping[str, str]) -> dict[str, str]:
    return {key: text_sha256(str(value)) for key, value in sorted(fields.items())}


def _full_query(fields: Mapping[str, str]) -> str:
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {fields['first_name']} {fields['last_name']}. "
        f"My personal email is {fields['email']} and phone number is "
        f"{fields['phone_number']}.\nTask: {fields['instruction']}"
    )


def _line_span(text: str, needle: str) -> dict[str, Any]:
    start = text.find(needle)
    if start < 0:
        return {"present": False, "start_line": None, "end_line": None}
    end = start + len(needle)
    return {
        "present": True,
        "start_line": text.count("\n", 0, start) + 1,
        "end_line": text.count("\n", 0, end) + 1,
    }


def _redacted_line_context(
    text: str, *, start_line: int | None, end_line: int | None, radius: int = 3
) -> list[dict[str, Any]]:
    if start_line is None or end_line is None:
        return []
    lines = text.splitlines()
    first = max(1, int(start_line) - int(radius))
    last = min(len(lines), int(end_line) + int(radius))
    return [
        {
            "line": line_number,
            "line_sha256": text_sha256(lines[line_number - 1]),
            "nonempty": bool(lines[line_number - 1].strip()),
            "inside_query_span": int(start_line) <= line_number <= int(end_line),
        }
        for line_number in range(first, last + 1)
    ]


def _task_snapshot_index(roots: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for root_text in roots:
        root = Path(str(root_text))
        task_root = root / "tasks"
        if not task_root.is_dir():
            rows.append({"root": str(root), "exists": False, "task_count": 0})
            continue
        count = 0
        for path in sorted(task_root.glob("*/specs.json")):
            count += 1
            fields = _spec_fields(path)
            hashes = _field_hashes(fields)
            identity = canonical_hash(hashes)
            entry = {
                "task_id": path.parent.name,
                "root": str(root),
                "spec_path": str(path),
                "spec_sha256": sha256_file(path),
                "field_sha256": hashes,
                "identity_sha256": identity,
                "full_query_sha256": text_sha256(_full_query(fields)),
            }
            by_identity[identity].append(entry)
        rows.append(
            {
                "root": str(root),
                "exists": True,
                "task_count": count,
                "root_path_sha256": text_sha256(str(root)),
            }
        )
    return rows, by_identity


def _candidate_text_files(
    root: Path,
    *,
    suffixes: set[str],
    name_fragments: Sequence[str],
    maximum_bytes: int,
    task_id: str,
) -> list[Path]:
    if not root.exists():
        return []
    output: list[Path] = []
    fragments = tuple(str(value).lower() for value in name_fragments)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        name = path.name.lower()
        if task_id.lower() not in path.as_posix().lower() and not any(value in name for value in fragments):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= int(maximum_bytes):
            output.append(path)
    return output


def _scan_text_files(
    files: Sequence[Path],
    *,
    query: str,
    source_fields: Mapping[str, str],
    category: str,
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        full_match = query in text
        component_matches = sorted(
            key
            for key in ("first_name", "last_name", "email", "phone_number")
            if source_fields[key] and source_fields[key] in text
        )
        if not full_match and not component_matches:
            continue
        hits.append(
            {
                "category": category,
                **redacted_path(str(path)),
                "file_sha256": sha256_file(path),
                "file_bytes": size,
                "full_query_match": full_match,
                "matching_component_names": component_matches,
                "is_task_snapshot": path.name == "specs.json",
            }
        )
    return {
        "category": category,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "hit_count": len(hits),
        "hits": hits,
    }


def _git_history_search(repo: Path, values: Mapping[str, str]) -> dict[str, Any]:
    hits: dict[str, list[str]] = {}
    for key, value in values.items():
        completed = subprocess.run(
            ["git", "log", "--all", f"-S{value}", "--format=%H", "--"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        commits = sorted(set(completed.stdout.splitlines())) if completed.returncode == 0 else []
        hits[key] = commits
    return {
        "repository_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "search_terms": sorted(values),
        "commit_hits": hits,
        "error_free": True,
    }


def _git_lfs_search(
    repo: Path,
    *,
    query: str,
    source_fields: Mapping[str, str],
    maximum_bytes: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "lfs", "ls-files", "--all", "--long"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    common_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True
    ).strip()
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = (repo / common_path).resolve()
    object_root = common_path / "lfs" / "objects"
    files = sorted(path for path in object_root.rglob("*") if path.is_file()) if object_root.exists() else []
    needles = {
        "full_query": query.encode("utf-8"),
        **{
            key: str(source_fields[key]).encode("utf-8")
            for key in ("first_name", "last_name", "email", "phone_number")
        },
    }
    hits = []
    searched = 0
    searched_bytes = 0
    skipped_large = 0
    for path in files:
        size = path.stat().st_size
        if size > int(maximum_bytes):
            skipped_large += 1
            continue
        searched += 1
        searched_bytes += size
        content = path.read_bytes()
        matching = sorted(key for key, value in needles.items() if value and value in content)
        if matching:
            hits.append(
                {
                    "object_sha256": sha256_file(path),
                    "object_bytes": size,
                    "matching_component_names": matching,
                }
            )
    return {
        "git_lfs_available": completed.returncode == 0,
        "git_lfs_indexed_line_count": len(completed.stdout.splitlines()) if completed.returncode == 0 else 0,
        "git_lfs_index_sha256": text_sha256(completed.stdout) if completed.returncode == 0 else None,
        "git_lfs_error_sha256": text_sha256(completed.stderr) if completed.returncode != 0 else None,
        "object_root_exists": object_root.exists(),
        "object_count": len(files),
        "searched_object_count": searched,
        "searched_bytes": searched_bytes,
        "skipped_large_object_count": skipped_large,
        "hit_count": len(hits),
        "hits": hits,
    }


def _archive_member_hits(
    path: Path,
    *,
    query: str,
    source_fields: Mapping[str, str],
    suffixes: set[str],
    maximum_bytes: int,
) -> tuple[list[dict[str, Any]], int, int]:
    needles = {
        "full_query": query.encode("utf-8"),
        **{
            key: str(source_fields[key]).encode("utf-8")
            for key in ("first_name", "last_name", "email", "phone_number")
        },
    }
    members: list[tuple[str, int, bytes]] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > int(maximum_bytes):
                    continue
                if Path(info.filename).suffix.lower() not in suffixes:
                    continue
                members.append((info.filename, info.file_size, archive.read(info)))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for info in archive.getmembers():
                if not info.isfile() or info.size > int(maximum_bytes):
                    continue
                if Path(info.name).suffix.lower() not in suffixes:
                    continue
                extracted = archive.extractfile(info)
                if extracted is not None:
                    members.append((info.name, info.size, extracted.read()))
    hits = []
    for name, size, content in members:
        matching = sorted(key for key, value in needles.items() if value and value in content)
        if matching:
            hits.append(
                {
                    "member_basename": Path(name).name,
                    "member_path_sha256": text_sha256(name),
                    "member_bytes": size,
                    "member_sha256": hashlib.sha256(content).hexdigest(),
                    "matching_component_names": matching,
                }
            )
    return hits, len(members), sum(size for _, size, _ in members)


def _bundle_inventory(
    roots: Sequence[str],
    *,
    repo: Path,
    query: str,
    source_fields: Mapping[str, str],
    suffixes: set[str],
    maximum_bytes: int,
) -> dict[str, Any]:
    seen: set[Path] = set()
    rows: list[dict[str, Any]] = []
    for root_text in roots:
        root = Path(str(root_text))
        if not root.exists():
            continue
        for pattern in ("*.bundle", "*.tar", "*.tar.gz", "*.tgz", "*.zip"):
            for path in sorted(root.glob(pattern)):
                resolved = path.resolve()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                row: dict[str, Any] = {
                    **redacted_path(str(path)),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "kind": "git_bundle" if path.suffix == ".bundle" else "archive",
                }
                if path.suffix == ".bundle":
                    listed = subprocess.run(
                        ["git", "bundle", "list-heads", str(path)],
                        cwd=repo,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    heads = [line.split(maxsplit=1)[0] for line in listed.stdout.splitlines() if line.strip()]
                    present = []
                    for commit in heads:
                        checked = subprocess.run(
                            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                            cwd=repo,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        present.append(checked.returncode == 0)
                    row.update(
                        {
                            "head_count": len(heads),
                            "head_hashes_sha256": canonical_hash(heads),
                            "all_heads_present_in_repository": all(present),
                            "search_coverage": (
                                "covered_by_repository_git_history_search"
                                if all(present)
                                else "incomplete_unreachable_bundle_heads"
                            ),
                        }
                    )
                else:
                    hits, member_count, searched_bytes = _archive_member_hits(
                        path,
                        query=query,
                        source_fields=source_fields,
                        suffixes=suffixes,
                        maximum_bytes=maximum_bytes,
                    )
                    row.update(
                        {
                            "searched_text_member_count": member_count,
                            "searched_text_member_bytes": searched_bytes,
                            "hit_count": len(hits),
                            "hits": hits,
                            "search_coverage": "searched_supported_text_members",
                        }
                    )
                rows.append(row)
    incomplete = [row for row in rows if row["search_coverage"].startswith("incomplete")]
    return {
        "artifact_count": len(rows),
        "artifacts": rows,
        "search_complete": not incomplete,
        "incomplete_artifact_count": len(incomplete),
    }


def _trajectory_identity_evidence(
    record: Any,
    *,
    source_fields: Mapping[str, str],
    official_fields: Mapping[str, str],
) -> dict[str, Any]:
    rows = []
    source_total = 0
    official_total = 0
    mixed_steps = 0
    for step in record.raw_trajectory["steps"]:
        text_by_location = {
            "action": str(step.get("response", "")),
            "observation": str(step.get("observation", "")),
        }
        source_matches = []
        official_matches = []
        for field in ("first_name", "last_name", "email", "phone_number"):
            source_value = str(source_fields[field])
            official_value = str(official_fields[field])
            for location, text in text_by_location.items():
                if source_value and source_value in text:
                    source_matches.append(f"{location}:{field}")
                if official_value and official_value in text:
                    official_matches.append(f"{location}:{field}")
        source_total += len(source_matches)
        official_total += len(official_matches)
        mixed_steps += bool(source_matches and official_matches)
        if source_matches or official_matches:
            rows.append(
                {
                    "step_id": int(step["step_id"]),
                    "source_identity_match_locations": sorted(source_matches),
                    "official_identity_match_locations": sorted(official_matches),
                }
            )
    return {
        "source_identity_evidence_count": source_total,
        "official_identity_evidence_count": official_total,
        "mixed_identity_step_count": mixed_steps,
        "steps_with_identity_evidence": rows,
        "source_identity_behaviorally_referenced": source_total > 0,
        "official_identity_behaviorally_referenced": official_total > 0,
    }


def _probe_official_tasks(
    *,
    settings: Mapping[str, Any],
    task_ids: Sequence[str],
    artifact_dir: Path,
    attempt_id: str,
) -> dict[str, Any]:
    request = {
        "format": "appworld_identity_probe_request_6h3_v1",
        "legacy_python": settings["legacy"]["executable"],
        "appworld_root": settings["legacy"]["appworld_root"],
        "experiment_prefix": f"exp024r3_{_safe_name(attempt_id)}_identity",
        "random_seed": int(settings["replay"]["random_seed"]),
        "max_interactions": int(settings["replay"]["max_interactions"]),
        "max_api_calls_per_interaction": int(settings["replay"]["max_api_calls_per_interaction"]),
        "task_ids": sorted(map(str, task_ids)),
        "jwt_pairs": [],
    }
    private = artifact_dir / "private"
    private.mkdir(parents=True, exist_ok=True)
    request_path = private / f"corpus_identity_probe_{_safe_name(attempt_id)}.json"
    output_path = artifact_dir / "corpus_official_identity_probe.json"
    atomic_write_json(request_path, request)
    if output_path.exists():
        existing = _load_json(output_path)
        if existing.get("task_count") != len(task_ids):
            raise ValueError("Existing corpus identity probe has the wrong task count")
        if {str(row["task_id"]) for row in existing.get("rows", [])} != set(
            map(str, task_ids)
        ):
            raise ValueError("Existing corpus identity probe has different task IDs")
        return existing
    env = dict(os.environ)
    env.update(
        {
            "APPWORLD_ROOT": str(settings["legacy"]["appworld_root"]),
            "APPWORLD_CACHE": str(settings["legacy"]["appworld_cache"]),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
            "PYTHONUNBUFFERED": "1",
        }
    )
    command = [
        str(settings["legacy"]["executable"]),
        str(settings["replay"]["identity_bridge"]),
        "--input",
        str(request_path),
        "--output",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=int(settings["replay"]["subprocess_timeout_seconds"]) * len(task_ids),
        check=False,
    )
    (artifact_dir / "logs").mkdir(parents=True, exist_ok=True)
    atomic_write_text(artifact_dir / "logs" / "corpus_identity_probe.log", completed.stdout)
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError("Corpus-wide AppWorld 0.1.0 identity probe failed")
    return _load_json(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_provenance_replay_6h3.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp024r3")
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_6h3"]
    persistent = Path(settings["persistent_root"])
    if os.name != "nt" and not os.path.ismount(persistent):
        raise RuntimeError(f"Persistent root is not mounted: {persistent}")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Attempt ID already exists: {args.attempt_id}")

    source = Path(settings["source_data"])
    exp017 = Path(settings["exp017_artifact"])
    exp020 = Path(settings["exp020_artifact"])
    exp022 = Path(settings["exp022_artifact"])
    exp024a = Path(settings["parent_exp024a"])
    exp024r = Path(settings["parent_exp024r"])
    exp024r2 = Path(settings["parent_exp024r2"])
    stage_b = Path(settings["stage_b_labels"])
    paths = {
        "decision_examples": source / "decision_examples.jsonl",
        "memory_records": source / "memory_records.jsonl",
        "split_manifest": Path(settings["split_manifest"]),
        "transition_manifest": exp017 / "transition_manifest.jsonl",
        "exp020_queries": exp020 / "expanded_query_manifest.json",
        "exp022_one_step": exp022 / "one_step_query_manifest.json",
        "exp024a_conditions": exp024a / "condition_manifest.json",
        "exp024r_environment": exp024r / "environment_provenance.json",
        "exp024r_sentinel": exp024r / "sentinel_manifest.json",
        "exp024r2_jwt": exp024r2 / "jwt_stable_claim_audit.json",
        "exp024r2_identity": exp024r2 / "identity_provenance_audit.json",
        "exp024r2_decision": exp024r2 / "preflight_decision.json",
        "stage_b_labels": stage_b / "student_labels.jsonl",
        "effective_memory_bank": stage_b / "effective_memory_bank.jsonl",
    }
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Immutable input missing: {name}={path}")
    environment = _load_json(paths["exp024r_environment"])
    contract_manifest_path = Path(str(environment["active_contract_manifest"]))
    paths["exp024r_contract_manifest"] = contract_manifest_path
    data_hashes = {name: sha256_file(path) for name, path in paths.items()}
    config_hash = sha256_file(args.config)
    initialize_or_validate_run_manifest(
        args.artifact_dir / "run_manifest.json",
        run_uuid=str(settings["run_uuid"]),
        config_sha256=config_hash,
        data_manifest_hashes=data_hashes,
        source_commit=args.lambda_head,
        command_scope=[
            "all_46_trajectories_and_638_decisions_identity_audit",
            "bounded_existing_snapshot_search",
            "exact_snapshot_or_whole_task_quarantine",
            "semantic_replay_only_after_provenance_gate",
            "no_qwen_or_memory_conditions_or_training",
        ],
    )

    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="corpus_provenance_and_snapshot_preflight",
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
        expected = settings["expected"]
        jwt = _load_json(paths["exp024r2_jwt"])
        r2_identity = _load_json(paths["exp024r2_identity"])
        r2_decision = _load_json(paths["exp024r2_decision"])
        if not bool(jwt["hard_gate_passed"]) or jwt["non_temporal_mismatch_count"] != 0:
            raise ValueError("Immutable EXP-024R2 JWT semantic gate changed")
        if r2_identity["identity_match_count"] != 40 or r2_identity["identity_mismatch_count"] != 5:
            raise ValueError("Immutable EXP-024R2 identity result changed")
        if r2_decision["decision_branch"] != "source_query_task_identity_snapshot_unresolved":
            raise ValueError("Immutable EXP-024R2 branch changed")

        records = load_memory_records(paths["memory_records"])
        examples = load_decision_examples(paths["decision_examples"])
        if len(records) != int(expected["memory_records"]) or len(examples) != int(expected["decision_examples"]):
            raise ValueError("Source corpus counts changed")
        records_by_task = {str(record.task_id): record for record in records}
        if len(records_by_task) != len(records):
            raise ValueError("Source memory records duplicate task IDs")
        examples_by_task: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        for index, example in enumerate(examples):
            examples_by_task[str(example.metadata["task_id"])].append((index, example))

        probe = _probe_official_tasks(
            settings=settings,
            task_ids=sorted(records_by_task),
            artifact_dir=args.artifact_dir,
            attempt_id=args.attempt_id,
        )
        official_by_task = {str(row["task_id"]): row for row in probe["rows"]}
        if len(official_by_task) != len(records_by_task):
            raise ValueError("Official identity probe did not cover all source tasks")
        attempt.progress(latest_validated_checkpoint=str(args.artifact_dir / "corpus_official_identity_probe.json"))

        backup_root = Path(settings["snapshot_search"]["task_snapshot_roots"][1])
        transition_rows = _load_jsonl(paths["transition_manifest"])
        transition_parent_task_ids = sorted({str(row["parent_task_id"]) for row in transition_rows})
        exp020_rows = _manifest_rows(_load_json(paths["exp020_queries"]))
        exp024a_rows = _manifest_rows(_load_json(paths["exp022_one_step"]))
        if len(set(transition_parent_task_ids)) != int(expected["transition_parents"]):
            raise ValueError("EXP-017 transition-parent count changed")
        if len(exp020_rows) != int(expected["exp020_query_states"]):
            raise ValueError("EXP-020 query-state count changed")
        if len(exp024a_rows) != int(expected["original_audit_states"]):
            raise ValueError("EXP-024A audit-state count changed")
        if len({str(row["task_id"]) for row in exp024a_rows}) != int(
            expected["original_audit_tasks"]
        ):
            raise ValueError("EXP-024A audit-task count changed")
        exp020_by_task = Counter(str(row["task_id"]) for row in exp020_rows)
        exp024a_by_task = Counter(str(row["task_id"]) for row in exp024a_rows)

        corpus_rows = []
        decision_identity_rows: list[dict[str, Any]] = []
        private_source_fields: dict[str, dict[str, str]] = {}
        private_official_fields: dict[str, dict[str, str]] = {}
        for memory_line, record in enumerate(records, start=1):
            task_id = str(record.task_id)
            raw_query = str(record.raw_trajectory["query"])
            source_fields = parse_full_demo_query(raw_query)
            private_source_fields[task_id] = source_fields
            source_full_hash = text_sha256(raw_query)
            decision_entries = examples_by_task[task_id]
            decision_hashes = []
            decision_state_ids = []
            decision_lines = []
            for index, example in decision_entries:
                _, query, _ = _parse_appworld_state_text(str(example.state_text))
                query_hash = text_sha256(query)
                state_id = state_example_id(index, example)
                decision_hashes.append(query_hash)
                decision_state_ids.append(state_id)
                decision_lines.append(index + 1)
                decision_identity_rows.append(
                    {
                        "decision_example_line": index + 1,
                        "state_example_id": state_id,
                        "state_example_id_sha256": text_sha256(state_id),
                        "task_id": task_id,
                        "episode_id_sha256": text_sha256(str(example.episode_id)),
                        "step_id": int(example.step_id),
                        "decision_query_sha256": query_hash,
                        "raw_trajectory_query_sha256": source_full_hash,
                        "decision_matches_raw_trajectory": query_hash == source_full_hash,
                        "metadata_source": str(example.metadata.get("source", "")),
                        "source_path": redacted_path(str(example.metadata.get("source_path", ""))),
                        "parent_memory_id": str(record.memory_id),
                        "parent_memory_id_sha256": text_sha256(str(record.memory_id)),
                    }
                )
            source_path = Path(str(record.metadata["source_path"]))
            source_text = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
            source_span = _line_span(source_text, raw_query)
            official = official_by_task[task_id]
            official_spec = Path(settings["legacy"]["appworld_root"]) / "data" / "tasks" / task_id / "specs.json"
            official_fields = _spec_fields(official_spec)
            private_official_fields[task_id] = official_fields
            official_hashes = _field_hashes(official_fields)
            backup_spec = backup_root / "tasks" / task_id / "specs.json"
            backup_hashes = _field_hashes(_spec_fields(backup_spec))
            source_hashes = _field_hashes(source_fields)
            field_matches = {key: source_hashes[key] == official_hashes[key] for key in source_hashes}
            source_layers_agree = bool(
                set(decision_hashes) == {source_full_hash}
                and source_span["present"]
            )
            task_row = {
                "task_id": task_id,
                "memory_id": str(record.memory_id),
                "memory_record_line": memory_line,
                "decision_example_count": len(decision_entries),
                "decision_example_line_min": min(decision_lines),
                "decision_example_line_max": max(decision_lines),
                "decision_state_ids_sha256": canonical_hash(decision_state_ids),
                "source_path": redacted_path(str(source_path)),
                "source_file_sha256": sha256_file(source_path),
                "source_query_line_span": source_span,
                "source_query_line_context": _redacted_line_context(
                    source_text,
                    start_line=source_span["start_line"],
                    end_line=source_span["end_line"],
                ),
                "source_query_sha256": source_full_hash,
                "source_field_sha256": source_hashes,
                "decision_query_unique_hashes": sorted(set(decision_hashes)),
                "source_layers_agree": source_layers_agree,
                "official_field_sha256": official_hashes,
                "backup_field_sha256": backup_hashes,
                "official_backup_agree": official_hashes == backup_hashes,
                "field_matches": field_matches,
                "mismatched_fields": sorted(key for key, match in field_matches.items() if not match),
                "transition_parent": task_id in set(transition_parent_task_ids),
                "exp020_query_count": exp020_by_task[task_id],
                "exp024a_audit_state_count": exp024a_by_task[task_id],
                "identity_match": bool(source_layers_agree and all(field_matches.values()) and official_hashes == backup_hashes),
            }
            corpus_rows.append(task_row)

        corpus = summarize_corpus_identity(corpus_rows)
        transition_formats = sorted({str(row.get("format", "")) for row in transition_rows})
        exp020_payload = _load_json(paths["exp020_queries"])
        exp024a_payload = _load_json(paths["exp022_one_step"])
        mismatch_rows = [row for row in corpus_rows if not bool(row["identity_match"])]
        corpus.update(
            {
                "memory_record_count": len(records),
                "decision_example_count": len(examples),
                "decision_examples_accounted_for": sum(row["decision_example_count"] for row in corpus_rows),
                "transition_parent_count": len(set(transition_parent_task_ids)),
                "exp020_query_state_count": len(exp020_rows),
                "exp024a_audit_state_count": len(exp024a_rows),
                "official_probe_sha256": sha256_file(args.artifact_dir / "corpus_official_identity_probe.json"),
                "identity_layers": {
                    "memory_record_source": sorted({str(record.metadata.get("source", "")) for record in records}),
                    "decision_example_source": sorted({str(example.metadata.get("source", "")) for example in examples}),
                    "decision_state_id_builder": "rcmf.training.transition_memory_6a.state_example_id",
                    "decision_state_parser": "rcmf.training.datasets._parse_appworld_state_text",
                    "transition_manifest_formats": transition_formats,
                    "exp020_query_manifest_format": str(exp020_payload.get("format", "unknown")),
                    "exp024a_query_manifest_format": str(exp024a_payload.get("format", "unknown")),
                    "source_memory_records_sha256": data_hashes["memory_records"],
                    "source_decision_examples_sha256": data_hashes["decision_examples"],
                    "exp017_transition_manifest_sha256": data_hashes["transition_manifest"],
                    "exp020_query_manifest_sha256": data_hashes["exp020_queries"],
                    "exp024a_query_manifest_sha256": data_hashes["exp022_one_step"],
                },
                "mismatch_grouping": {
                    "task_ids": sorted(str(row["task_id"]) for row in mismatch_rows),
                    "source_path_hashes": sorted({str(row["source_path"]["path_sha256"]) for row in mismatch_rows}),
                    "source_line_spans": [row["source_query_line_span"] for row in mismatch_rows],
                    "dataset_building_stages": [
                        "raw_successful_trajectory",
                        "memory_record_ingestion",
                        "decision_example_construction",
                        "exp017_transition_parent_selection",
                        "exp020_query_manifest",
                        "exp024a_audit_manifest",
                        "official_010_task_metadata",
                        "immutable_010_backup_metadata",
                    ],
                },
            }
        )
        atomic_write_json(args.artifact_dir / "corpus_identity_consistency.json", corpus)
        _atomic_write_jsonl(
            args.artifact_dir / "decision_example_identity_rows.jsonl",
            decision_identity_rows,
        )
        if len(decision_identity_rows) != int(expected["decision_examples"]):
            raise ValueError("Decision identity audit did not account for every example")
        if corpus["identity_mismatch_count"] > 1:
            decision = {
                "format": "appworld_provenance_preflight_decision_6h3_v1",
                "decision_branch": "source_dataset_identity_consistency_failure",
                "snapshot_found": False,
                "quarantine_allowed": False,
                "replay_allowed": False,
                "qwen_import_or_forward_count": 0,
            }
            atomic_write_json(args.artifact_dir / "preflight_decision.json", decision)
            attempt.progress(latest_validated_checkpoint=str(args.artifact_dir / "preflight_decision.json"))
            print(json.dumps(decision, indent=2, sort_keys=True))
            return
        if corpus["identity_mismatch_task_ids"] != [str(expected["quarantined_task_id"])]:
            raise ValueError("Corpus mismatch is not isolated to the preregistered task")

        task_id = str(expected["quarantined_task_id"])
        source_fields = private_source_fields[task_id]
        official_fields = private_official_fields[task_id]
        source_query = str(records_by_task[task_id].raw_trajectory["query"])
        snapshot_roots, identity_index = _task_snapshot_index(settings["snapshot_search"]["task_snapshot_roots"])
        source_identity = canonical_hash(_field_hashes(source_fields))
        source_identity_by_task = {
            candidate_task: canonical_hash(_field_hashes(fields))
            for candidate_task, fields in private_source_fields.items()
        }
        source_identity_matches_other_tasks = sorted(
            candidate_task
            for candidate_task, identity in source_identity_by_task.items()
            if candidate_task != task_id and identity == source_identity
        )
        identity_candidates = identity_index.get(source_identity, [])
        exact_task_snapshots = [row for row in identity_candidates if row["task_id"] == task_id]
        other_task_matches = [row for row in identity_candidates if row["task_id"] != task_id]

        search_settings = settings["snapshot_search"]
        suffixes = set(map(str, search_settings["searchable_suffixes"]))
        maximum_bytes = int(search_settings["maximum_text_file_bytes"])
        search_groups: list[dict[str, Any]] = []
        source_root = Path(str(search_settings["source_experiment_root"]))
        source_files = _candidate_text_files(
            source_root,
            suffixes=suffixes,
            name_fragments=search_settings["searchable_name_fragments"],
            maximum_bytes=maximum_bytes,
            task_id=task_id,
        )
        search_groups.append(_scan_text_files(source_files, query=source_query, source_fields=source_fields, category="source_experiment_outputs"))
        run_root = Path(str(search_settings["project_runs_root"]))
        run_files = _candidate_text_files(
            run_root,
            suffixes=suffixes,
            name_fragments=search_settings["searchable_name_fragments"],
            maximum_bytes=maximum_bytes,
            task_id=task_id,
        )
        run_files = [path for path in run_files if args.artifact_dir.resolve() not in path.resolve().parents]
        search_groups.append(_scan_text_files(run_files, query=source_query, source_fields=source_fields, category="persistent_project_runs"))
        snapshot_files = [Path(row["spec_path"]) for values in identity_index.values() for row in values]
        search_groups.append(_scan_text_files(snapshot_files, query=source_query, source_fields=source_fields, category="task_snapshot_specs"))
        git_search = _git_history_search(
            Path(str(search_settings["git_root"])),
            {"full_query": source_query, **{key: source_fields[key] for key in ("email", "phone_number")}},
        )
        lfs_search = _git_lfs_search(
            Path(str(search_settings["git_root"])),
            query=source_query,
            source_fields=source_fields,
            maximum_bytes=maximum_bytes,
        )
        transfer_search = _bundle_inventory(
            search_settings["transfer_roots"],
            repo=Path(str(search_settings["git_root"])),
            query=source_query,
            source_fields=source_fields,
            suffixes=suffixes,
            maximum_bytes=maximum_bytes,
        )
        if not bool(transfer_search["search_complete"]):
            raise RuntimeError("A transfer bundle contains unreachable heads and was not fully searched")
        snapshot_search = {
            "format": SNAPSHOT_SEARCH_VERSION,
            "task_id": task_id,
            "source_query_sha256": text_sha256(source_query),
            "source_identity_sha256": source_identity,
            "enumerated_sources": [
                "persistent_project_runs",
                "appworld_legacy_roots_and_backups",
                "historical_source_experiment_outputs",
                "task_specs_and_databases_via_task_root_manifests",
                "git_history",
                "git_lfs_objects",
                "recorded_transfer_bundle_inventory",
            ],
            "task_snapshot_roots": snapshot_roots,
            "text_search_groups": search_groups,
            "git_history": git_search,
            "git_lfs": lfs_search,
            "transfer_bundle_inventory": transfer_search,
            "identity_candidate_count": len(identity_candidates),
            "exact_task_snapshot_count": len(exact_task_snapshots),
            "other_task_identity_match_count": len(other_task_matches),
            "source_corpus_other_task_identity_match_count": len(source_identity_matches_other_tasks),
            "source_corpus_other_task_identity_matches": source_identity_matches_other_tasks,
            "identity_candidates": [
                {
                    "task_id": row["task_id"],
                    "root_path_sha256": text_sha256(row["root"]),
                    "spec_sha256": row["spec_sha256"],
                    "same_task_id": row["task_id"] == task_id,
                }
                for row in identity_candidates
            ],
            "exact_historical_snapshot_found": bool(exact_task_snapshots),
            "search_complete": bool(transfer_search["search_complete"]),
            "search_result": "exact_historical_snapshot_found" if exact_task_snapshots else "exact_historical_snapshot_not_found",
        }
        atomic_write_json(args.artifact_dir / "bounded_snapshot_search.json", snapshot_search)

        record = records_by_task[task_id]
        evidence = _trajectory_identity_evidence(
            record,
            source_fields=source_fields,
            official_fields=official_fields,
        )
        b0_row = next(row for row in corpus_rows if row["task_id"] == task_id)
        supervisor_only = set(b0_row["mismatched_fields"]) == {"first_name", "last_name", "email", "phone_number"}
        classification = classify_provenance_failure(
            source_layers_agree=bool(b0_row["source_layers_agree"]),
            supervisor_only_mismatch=supervisor_only,
            exact_identity_matches_other_task=bool(other_task_matches or source_identity_matches_other_tasks),
            source_identity_evidence_count=int(evidence["source_identity_evidence_count"]),
            official_identity_evidence_count=int(evidence["official_identity_evidence_count"]),
            mixed_identity_step_count=int(evidence["mixed_identity_step_count"]),
            exact_snapshot_found=bool(exact_task_snapshots),
        )
        b0_examples = examples_by_task[task_id]
        if len(b0_examples) != int(expected["quarantined_decision_count"]):
            raise ValueError("Quarantined task decision-example count changed")
        adjacent_by_line: dict[int, dict[str, Any]] = {}
        for index, _ in b0_examples:
            for candidate in range(max(0, index - 1), min(len(examples), index + 2)):
                example = examples[candidate]
                adjacent_by_line[candidate + 1] = {
                    "line": candidate + 1,
                    "task_id": str(example.metadata["task_id"]),
                    "state_id_sha256": text_sha256(state_example_id(candidate, example)),
                    "is_b0a8eae_2": str(example.metadata["task_id"]) == task_id,
                }
        forensic = {
            "format": "b0a8eae_2_forensic_provenance_6h3_v1",
            "task_id": task_id,
            "failure_classification": classification,
            "source_layers_agree": b0_row["source_layers_agree"],
            "mismatched_fields": b0_row["mismatched_fields"],
            "official_and_backup_agree": b0_row["official_backup_agree"],
            "source_file": b0_row["source_path"],
            "source_file_sha256": b0_row["source_file_sha256"],
            "source_query_line_span": b0_row["source_query_line_span"],
            "decision_example_lines": [index + 1 for index, _ in b0_examples],
            "adjacent_decision_records": [adjacent_by_line[line] for line in sorted(adjacent_by_line)],
            "trajectory_identity_evidence": evidence,
            "matches_other_official_task": bool(other_task_matches),
            "matching_other_task_ids": sorted({row["task_id"] for row in other_task_matches}),
            "matches_other_source_corpus_task": bool(source_identity_matches_other_tasks),
            "matching_other_source_corpus_task_ids": source_identity_matches_other_tasks,
            "exact_snapshot_found": bool(exact_task_snapshots),
            "snapshot_search_result": snapshot_search["search_result"],
            "raw_identity_values_redacted": True,
        }
        atomic_write_json(args.artifact_dir / "b0a8eae_2_forensic_provenance.json", forensic)

        split = _load_json(paths["split_manifest"])
        train_label_tasks = {
            str(row["task_id"])
            for row in _load_jsonl(paths["stage_b_labels"])
            if str(row["split"]) == "train"
        }
        teacher_source_tasks = {
            str(row["task_id"])
            for row in _load_jsonl(paths["effective_memory_bank"])
            if bool(row.get("eligible_for_stage_b"))
        }
        contamination = training_contamination_report(
            task_id=task_id,
            train_task_ids=split["train_task_ids"],
            transition_parent_task_ids=transition_parent_task_ids,
            train_label_task_ids=sorted(train_label_tasks),
            teacher_source_task_ids=sorted(teacher_source_tasks),
        )
        contamination.update(
            {
                "stage_b_split": "validation" if task_id in set(split["validation_task_ids"]) else "unknown",
                "decision_example_count": len(b0_examples),
                "exp020_query_count": exp020_by_task[task_id],
                "exp024a_audit_state_count": exp024a_by_task[task_id],
            }
        )
        atomic_write_json(args.artifact_dir / "training_contamination_audit.json", contamination)

        branch = select_preflight_branch(
            mismatch_task_count=int(corpus["identity_mismatch_count"]),
            exact_snapshot_found=bool(exact_task_snapshots),
            training_contaminated=bool(contamination["contaminates_training"]),
        )
        quarantine = None
        quarantine_sentinel = None
        if branch == "provenance_valid_task_quarantine_ready":
            quarantine = build_quarantine_manifest(exp024a_rows, quarantined_task_id=task_id)
            if quarantine["retained_state_count"] != int(expected["provenance_valid_states"]) or quarantine["retained_task_count"] != int(expected["provenance_valid_tasks"]):
                raise ValueError("Provenance-valid quarantine manifest count differs")
            atomic_write_json(args.artifact_dir / "provenance_valid_one_step_manifest_v1.json", quarantine)
            parent_sentinel = _load_json(paths["exp024r_sentinel"])
            quarantine_sentinel = build_quarantine_sentinel(
                _manifest_rows(parent_sentinel),
                retained_state_ids=[row["state_example_id"] for row in quarantine["rows"]],
                quarantined_task_id=task_id,
            )
            atomic_write_json(args.artifact_dir / "provenance_valid_sentinel_manifest.json", quarantine_sentinel)
            base_contracts = _load_json(contract_manifest_path)
            retained_ids = {str(row["state_example_id"]) for row in quarantine["rows"]}
            contract_rows = [
                dict(row)
                for row in _manifest_rows(base_contracts)
                if str(row["state_example_id"]) in retained_ids
            ]
            if len(contract_rows) != int(expected["provenance_valid_states"]):
                raise ValueError("Filtered replay contract count differs")
            contract_payload = {
                "format": "provenance_valid_replay_contract_manifest_6h3_v1",
                "source_manifest_sha256": sha256_file(contract_manifest_path),
                "quarantined_task_id": task_id,
                "row_count": len(contract_rows),
                "rows": contract_rows,
            }
            contract_payload["manifest_sha256"] = canonical_hash(contract_payload)
            atomic_write_json(args.artifact_dir / "provenance_valid_replay_contract_manifest.json", contract_payload)

        decision = {
            "format": "appworld_provenance_preflight_decision_6h3_v1",
            "decision_branch": branch,
            "corpus_identity_mismatch_task_count": corpus["identity_mismatch_count"],
            "failure_classification": classification,
            "snapshot_search_result": snapshot_search["search_result"],
            "snapshot_found": bool(exact_task_snapshots),
            "training_contaminated": bool(contamination["contaminates_training"]),
            "quarantine_allowed": branch == "provenance_valid_task_quarantine_ready",
            "replay_mode": (
                "original_45_exact_snapshot"
                if branch == "exact_historical_snapshot_found_pending_replay"
                else "provenance_valid_40_state_quarantine"
                if branch == "provenance_valid_task_quarantine_ready"
                else "blocked"
            ),
            "replay_allowed": branch in {"exact_historical_snapshot_found_pending_replay", "provenance_valid_task_quarantine_ready"},
            "quarantine_manifest_sha256": quarantine.get("manifest_sha256") if quarantine else None,
            "sentinel_manifest_sha256": quarantine_sentinel.get("manifest_sha256") if quarantine_sentinel else None,
            "scientific_parameter_changed": False,
            "qwen_import_or_forward_count": 0,
        }
        atomic_write_json(args.artifact_dir / "preflight_decision.json", decision)
        attempt.progress(latest_validated_checkpoint=str(args.artifact_dir / "preflight_decision.json"))
        print(json.dumps(decision, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
