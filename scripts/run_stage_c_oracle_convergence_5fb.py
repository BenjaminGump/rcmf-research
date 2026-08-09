from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.factory import build_backend
from rcmf.training.addressing_4b import mean_std
from rcmf.training.datasets import load_decision_examples
from rcmf.training.oracle_capacity_5e import validate_target_token_utility_identity
from rcmf.training.oracle_convergence_5fa import (
    OBJECTIVES_5FA,
    IndependentPairTensorTable,
    apply_independent_optimizer_step,
    load_training_checkpoint,
    save_training_checkpoint,
    update_count_summary,
    utility_capacity_gate,
)
from rcmf.training.oracle_convergence_5fb import (
    CHECKPOINT_INTERVAL,
    EXPECTED_SOURCE_CHECKPOINT_SHA256,
    EXPECTED_SOURCE_DELTA_SHA256,
    EXPECTED_STAGE5FA_SOURCE_COMMIT,
    HARD_CAP_UPDATES,
    MINIMUM_TERMINAL_UPDATES,
    ORACLE_EXTENSION_VERSION,
    SOURCE_UPDATES,
    add_selection_category_metrics,
    delta_tensor_summary,
    eligible_plateau,
    extension_checkpoint_schedule,
    final_control_bootstrap,
    metric_reproduction_report,
    numerical_instability_report,
    tensor_state_sha256,
    terminal_decision,
    validate_source_checkpoint_payload,
)
from rcmf.training.pair_grounding_5d import PAIR_RESPONSE_CACHE_VERSION
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.run_raw_text_teacher_pilot import _context_limit_for_backend
from scripts.run_stage_c_oracle_capacity_5e import _collate, _forward_direct_delta
from scripts.run_stage_c_oracle_convergence_5fa import (
    _annotate_evaluation_updates,
    _boundary_fraction,
    _evaluate_direct_tensor,
    _evaluate_underoptimized_stage5e,
    _movement_summary,
    _precompute_direct_base_norms,
    _student_prompt_contract,
    _training_loss,
)
from scripts.run_stage_c_pair_grounding_5d import _build_tokenized_pair_rows

REPRODUCTION_PATHS = (
    ("u_text_vs_u_student_spearman",),
    ("u_text_vs_u_student_pearson",),
    ("positive_negative_sign_agreement",),
    ("sequence_utility_huber", "mean"),
    ("sequence_utility_mae", "mean"),
    ("sequence_utility_mse", "mean"),
    ("target_token_delta_correlation_global",),
    ("target_token_delta_huber", "mean"),
    ("sparse_teacher_kl", "mean"),
    ("target_nll", "mean"),
    ("delta_ratio", "mean"),
    ("delta_ratio", "max"),
)


def utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if not rows:
        raise ValueError(f"No rows found at {path}")
    return rows


def _select_by_pair_ids(
    rows: Sequence[dict[str, Any]], pair_ids: Sequence[str]
) -> list[dict[str, Any]]:
    by_id = {str(row["pair_id"]): row for row in rows}
    missing = [str(pair_id) for pair_id in pair_ids if str(pair_id) not in by_id]
    if missing:
        raise ValueError(f"Missing preserved pair IDs: {missing[:20]}")
    selected = [by_id[str(pair_id)] for pair_id in pair_ids]
    if [str(row["pair_id"]) for row in selected] != [str(value) for value in pair_ids]:
        raise AssertionError("pair order changed during reconstruction")
    return selected


def _portable_evaluation(evaluation: dict[str, Any], rows_path: Path) -> dict[str, Any]:
    write_jsonl(rows_path, evaluation["rows"])
    return {
        "summary": evaluation["summary"],
        "selected_token_report": evaluation.get("selected_token_report"),
        "rows_path": str(rows_path),
    }


def _enrich_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    evaluation["summary"] = add_selection_category_metrics(
        evaluation["summary"], evaluation["rows"]
    )
    return evaluation


def _interval_summary(reports: Sequence[dict[str, float]]) -> dict[str, Any]:
    fields = (
        "objective",
        "gradient_norm",
        "sequence_utility_huber",
        "target_delta_huber",
        "sparse_teacher_kl",
    )
    result = {}
    for field in fields:
        values = [float(report[field]) for report in reports]
        result[field] = {**mean_std(values), "min": min(values), "max": max(values)}
    return result


def _model_identity(backend: Any, pair_cache_summary: Mapping[str, Any]) -> dict[str, Any]:
    runtime_model_name = str(
        getattr(backend, "model_name", getattr(backend.model.config, "name_or_path", ""))
    )
    runtime_config_commit = getattr(backend.model.config, "_commit_hash", None)
    tokenizer_name = str(getattr(backend.tokenizer, "name_or_path", ""))
    chat_template = getattr(backend.tokenizer, "chat_template", None) or ""
    expected_model_name = pair_cache_summary.get("model_name")
    expected_commit = pair_cache_summary.get("model_config_commit_hash")
    errors = []
    if expected_model_name and runtime_model_name != str(expected_model_name):
        errors.append(
            f"runtime model {runtime_model_name!r} != cache model {expected_model_name!r}"
        )
    if expected_commit and runtime_config_commit != expected_commit:
        errors.append(
            f"runtime model commit {runtime_config_commit!r} != cache commit {expected_commit!r}"
        )
    return {
        "passed": not errors,
        "errors": errors,
        "runtime_model_name": runtime_model_name,
        "runtime_model_config_commit_hash": runtime_config_commit,
        "runtime_tokenizer_name_or_path": tokenizer_name,
        "runtime_chat_template_sha256": hashlib.sha256(chat_template.encode("utf-8")).hexdigest(),
        "cache_model_name": expected_model_name,
        "cache_model_config_commit_hash": expected_commit,
        "cache_checkpoint_identity": pair_cache_summary.get("checkpoint_identity"),
    }


def _source_integrity_manifest(
    *,
    source_checkpoint: Path,
    source_payload_report: Mapping[str, Any],
    pair_ids: Sequence[str],
    pair_cache_sha256: str,
    pair_cache_summary_sha256: str,
    source_summary_sha256: str,
    source_resolved_config_sha256: str,
    runtime_model_identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": "stage_c_direct_delta_source_integrity_5fb_v1",
        "source_checkpoint": str(source_checkpoint),
        "source_checkpoint_file_sha256": sha256_file(source_checkpoint),
        "source_delta_tensor_sha256": source_payload_report["delta_tensor_sha256"],
        "legacy_checkpoint_has_embedded_delta_hash": source_payload_report[
            "legacy_checkpoint_has_embedded_delta_hash"
        ],
        "legacy_hash_compatibility": (
            "The immutable Stage-5F-A checkpoint predates embedded DeltaE/config/model/cache hashes. "
            "EXP-016B records and revalidates its file and normalized tensor hashes in this sidecar; "
            "the source artifact is not rewritten."
        ),
        "ordered_pair_manifest_sha256": _json_sha256(list(pair_ids)),
        "pair_count": len(pair_ids),
        "pair_cache_sha256": pair_cache_sha256,
        "pair_cache_summary_sha256": pair_cache_summary_sha256,
        "source_stage5fa_summary_sha256": source_summary_sha256,
        "source_stage5fa_resolved_config_sha256": source_resolved_config_sha256,
        "runtime_model_identity": dict(runtime_model_identity),
        "source_checkpoint_validation": dict(source_payload_report),
        "created_utc": utc_now(),
    }


def _validate_or_create_source_sidecar(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    immutable_keys = (
        "source_checkpoint",
        "source_checkpoint_file_sha256",
        "source_delta_tensor_sha256",
        "ordered_pair_manifest_sha256",
        "pair_count",
        "pair_cache_sha256",
        "pair_cache_summary_sha256",
        "source_stage5fa_summary_sha256",
        "source_stage5fa_resolved_config_sha256",
        "runtime_model_identity",
    )
    if path.exists():
        previous = _load_json(path)
        differences = {
            key: {"recorded": previous.get(key), "current": manifest.get(key)}
            for key in immutable_keys
            if previous.get(key) != manifest.get(key)
        }
        if differences:
            raise ValueError(f"source integrity sidecar mismatch: {differences}")
        return previous
    atomic_write_json(path, manifest)
    verified = _load_json(path)
    if any(verified.get(key) != manifest.get(key) for key in immutable_keys):
        raise RuntimeError("source integrity sidecar did not round-trip exactly")
    return verified


def _checkpoint_sidecar(path: Path, *, metadata: Mapping[str, Any]) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    table_hash = tensor_state_sha256(payload["table_state_dict"])
    expected_hash = payload.get("metadata", {}).get("delta_tensor_sha256")
    if table_hash != expected_hash:
        raise RuntimeError(f"saved checkpoint DeltaE hash mismatch at {path}")
    report = {
        "format": "stage_c_direct_delta_checkpoint_integrity_5fb_v1",
        "checkpoint": str(path),
        "checkpoint_file_sha256": sha256_file(path),
        "delta_tensor_sha256": table_hash,
        "completed_rounds": int(payload["completed_rounds"]),
        "update_accounting": payload["update_accounting"],
        "metadata": dict(metadata),
        "verified_utc": utc_now(),
    }
    atomic_write_json(path.with_suffix(path.suffix + ".integrity.json"), report)
    return report


def _evaluate_tensor(
    *,
    backend: Any,
    rows: Sequence[dict[str, Any]],
    tensor: torch.Tensor,
    pair_ids: Sequence[str],
    device: torch.device,
    k: int,
    batch_size: int,
    huber_delta: float,
    control: str,
    updates: int,
) -> dict[str, Any]:
    evaluation = _evaluate_direct_tensor(
        backend=backend,
        rows=rows,
        delta_tensor=tensor.to(device),
        pair_ids=pair_ids,
        device=device,
        k=k,
        batch_size=batch_size,
        huber_delta=huber_delta,
        control=control,
    )
    _annotate_evaluation_updates(evaluation, updates)
    return _enrich_evaluation(evaluation)


def _matched_random_tensor(
    tensor: torch.Tensor, *, training_seed: int, ratio_budget: float, k: int
) -> torch.Tensor:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(training_seed + 30000 + int(ratio_budget * 1000) + k)
    random_tensor = torch.randn(tensor.shape, generator=generator, dtype=torch.float32)
    trained_norms = tensor.to(torch.float32).flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_norms = random_tensor.flatten(start_dim=1).norm(dim=1).clamp_min(1.0e-8)
    random_tensor.mul_((trained_norms / random_norms).view(-1, 1, 1))
    return random_tensor


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# EXP-016B Direct-Oracle Convergence Extension",
        "",
        f"Status: `{summary['status']}`",
        f"Source commit: `{summary['source_commit']}`",
        f"Artifact: `{summary['output_dir']}`",
        "",
        "## Resume Integrity",
        "",
        f"- Source checkpoint: `{summary['source_checkpoint']}`",
        f"- Source file SHA256: `{summary['source_integrity']['source_checkpoint_file_sha256']}`",
        f"- Source DeltaE SHA256: `{summary['source_integrity']['source_delta_tensor_sha256']}`",
        (
            f"- Pair count and u64 accounting: `{summary['resume_validation']['pair_count']}` pairs, "
            f"`{summary['resume_validation']['update_accounting']['minimum_updates_per_pair']}` updates each"
        ),
        f"- u64 metric reproduction passed: `{summary['resume_validation']['metric_reproduction']['passed']}`",
        "",
        "## Convergence Curve",
        "",
        "| Updates/pair | Spearman | Sign | Seq Huber | Seq MAE | Seq MSE | Target-delta corr | Sparse KL | Ratio mean/max | Boundary frac | Grad mean/max | Movement mean/max |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["convergence_history"]:
        metrics = item["evaluation_summary"]
        train = item.get("train_interval", {}).get("gradient_norm", {})
        movement = item.get("movement_from_previous_checkpoint", {}).get("l2", {})
        lines.append(
            f"| {item['updates_per_pair']} | {metrics['u_text_vs_u_student_spearman']:.6f} | "
            f"{metrics['positive_negative_sign_agreement']:.6f} | "
            f"{metrics['sequence_utility_huber']['mean']:.6f} | "
            f"{metrics['sequence_utility_mae']['mean']:.6f} | "
            f"{metrics['sequence_utility_mse']['mean']:.6f} | "
            f"{metrics['target_token_delta_correlation_global']:.6f} | "
            f"{metrics['sparse_teacher_kl']['mean']:.6f} | "
            f"{metrics['delta_ratio']['mean']:.6f}/{metrics['delta_ratio']['max']:.6f} | "
            f"{item.get('fraction_at_ratio_boundary', 0.0):.6f} | "
            f"{train.get('mean', 0.0):.6f}/{train.get('max', 0.0):.6f} | "
            f"{movement.get('mean', 0.0):.6f}/{movement.get('max', 0.0):.6f} |"
        )
    final = summary["final_evaluation"]["summary"]
    lines.extend(
        [
            "",
            "## Plateau And Capacity",
            "",
            "```json",
            json.dumps(summary["final_plateau"], indent=2, sort_keys=True),
            "```",
            "",
            "```json",
            json.dumps(summary["utility_capacity_gate"], indent=2, sort_keys=True),
            "```",
            "",
            (
                f"Final Spearman/sign/Huber: `{final['u_text_vs_u_student_spearman']:.6f}` / "
                f"`{final['positive_negative_sign_agreement']:.6f}` / "
                f"`{final['sequence_utility_huber']['mean']:.6f}`."
            ),
            "",
            "## Controls And Bootstrap",
            "",
            "```json",
            json.dumps(summary["control_comparison"], indent=2, sort_keys=True),
            "```",
            "",
            "```json",
            json.dumps(summary["paired_bootstrap"], indent=2, sort_keys=True),
            "```",
            "",
            "## Decision",
            "",
            "```json",
            json.dumps(summary["decision"], indent=2, sort_keys=True),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pair-cache-dir", type=Path, required=True)
    parser.add_argument("--stage5fa-dir", type=Path, required=True)
    parser.add_argument("--stage5e-dir", type=Path, required=True)
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--training-seed", type=int, default=1001)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--direct-lr", type=float, default=0.05)
    parser.add_argument("--minimum-updates", type=int, default=128)
    parser.add_argument("--hard-cap-updates", type=int, default=256)
    parser.add_argument("--reproduction-tolerance", type=float, default=5.0e-5)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--progress-interval-s", type=float, default=300.0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.k != 4:
        raise ValueError("EXP-016B is restricted to K=4")
    if args.batch_size != 1:
        raise ValueError("EXP-016B requires batch_size=1 for exact pair accounting")
    if args.training_seed != 1001:
        raise ValueError("EXP-016B must preserve the Stage-5F-A ratio-1.0 training seed 1001")
    if not math.isclose(args.direct_lr, 0.05, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError("EXP-016B must restore and preserve the Stage-5F-A learning rate 0.05")
    if (
        args.minimum_updates != MINIMUM_TERMINAL_UPDATES
        or args.hard_cap_updates != HARD_CAP_UPDATES
    ):
        raise ValueError("formal EXP-016B requires minimum u128 and hard cap u256")

    started = time.perf_counter()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    save_resolved_config(cfg, args.output_dir / "resolved_config.yaml")
    if args.smoke:
        print(
            json.dumps(
                {"schedule": extension_checkpoint_schedule(), "config": str(args.config)}, indent=2
            )
        )
        return

    source_summary_path = args.stage5fa_dir / "summary.json"
    source_config_path = args.stage5fa_dir / "resolved_config.yaml"
    pair_cache_path = args.pair_cache_dir / "pair_response_cache.jsonl"
    pair_cache_summary_path = args.pair_cache_dir / "pair_response_cache_summary.json"
    required_paths = (
        source_summary_path,
        source_config_path,
        pair_cache_path,
        pair_cache_summary_path,
        args.source_checkpoint,
    )
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"required EXP-016B inputs are missing: {missing}")

    source_summary = _load_json(source_summary_path)
    source_run = source_summary["confirmation_runs"]["ratio_1.0"]
    expected_pair_ids = [str(value) for value in source_run["pair_ids"]]
    if len(expected_pair_ids) != 192 or len(set(expected_pair_ids)) != 192:
        raise ValueError("Stage-5F-A ratio-1.0 manifest is not exactly 192 unique pairs")
    source_payload = torch.load(args.source_checkpoint, map_location="cpu", weights_only=False)
    source_payload_report = validate_source_checkpoint_payload(
        source_payload,
        expected_pair_ids=expected_pair_ids,
        expected_updates=SOURCE_UPDATES,
        expected_lr=args.direct_lr,
        expected_source_commit=EXPECTED_STAGE5FA_SOURCE_COMMIT,
    )
    if not source_payload_report["passed"]:
        raise ValueError(f"source checkpoint integrity failed: {source_payload_report['errors']}")
    source_checkpoint_from_summary = Path(source_run["checkpoint"])
    if source_checkpoint_from_summary.resolve() != args.source_checkpoint.resolve():
        raise ValueError("source checkpoint path differs from the Stage-5F-A recorded checkpoint")
    source_checkpoint_sha = sha256_file(args.source_checkpoint)
    if source_checkpoint_sha != EXPECTED_SOURCE_CHECKPOINT_SHA256:
        raise ValueError(
            f"source checkpoint SHA256 changed: {source_checkpoint_sha} != "
            f"{EXPECTED_SOURCE_CHECKPOINT_SHA256}"
        )
    if source_payload_report["delta_tensor_sha256"] != EXPECTED_SOURCE_DELTA_SHA256:
        raise ValueError(
            f"source DeltaE SHA256 changed: {source_payload_report['delta_tensor_sha256']} != "
            f"{EXPECTED_SOURCE_DELTA_SHA256}"
        )

    pair_cache_summary = _load_json(pair_cache_summary_path)
    if not pair_cache_summary.get("validation", {}).get("passed"):
        raise ValueError("Stage-5D pair cache validation is not passed")
    pair_rows = _load_rows(pair_cache_path)
    if any(row.get("format") != PAIR_RESPONSE_CACHE_VERSION for row in pair_rows):
        raise ValueError("Unexpected pair response-cache format")
    target_identity = validate_target_token_utility_identity(pair_rows)
    if not target_identity["passed"]:
        raise ValueError(
            f"target-token utility identity failed: {target_identity['errors_first_20']}"
        )
    pair_cache_sha = sha256_file(pair_cache_path)
    if pair_cache_sha != source_summary.get("pair_cache_sha256"):
        raise ValueError("pair cache hash differs from Stage-5F-A")

    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    device = backend.device
    model_identity = _model_identity(backend, pair_cache_summary)
    if not model_identity["passed"]:
        raise ValueError(f"model identity mismatch: {model_identity['errors']}")

    context_limit = _context_limit_for_backend(backend)
    selected_raw = _select_by_pair_ids(pair_rows, expected_pair_ids)
    examples = load_decision_examples(args.data / "decision_examples.jsonl")
    tokenized = _build_tokenized_pair_rows(
        backend=backend,
        examples=examples,
        pair_rows=selected_raw,
        prompt_profile=cfg.benchmark.prompt_profile,
        context_limit=context_limit,
    )
    rows = _select_by_pair_ids(tokenized, expected_pair_ids)
    prompt_contract = _student_prompt_contract(rows)
    if not prompt_contract["passed"]:
        raise ValueError(f"student prompt contract failed: {prompt_contract['errors_first_20']}")

    manifest = _source_integrity_manifest(
        source_checkpoint=args.source_checkpoint,
        source_payload_report=source_payload_report,
        pair_ids=expected_pair_ids,
        pair_cache_sha256=pair_cache_sha,
        pair_cache_summary_sha256=sha256_file(pair_cache_summary_path),
        source_summary_sha256=sha256_file(source_summary_path),
        source_resolved_config_sha256=sha256_file(source_config_path),
        runtime_model_identity=model_identity,
    )
    source_integrity = _validate_or_create_source_sidecar(
        args.output_dir / "source_integrity_manifest.json", manifest
    )

    objective = OBJECTIVES_5FA["sequence_utility_plus_sparse_kl"]
    source_weights = source_run["objective_weights"]
    expected_weights = {
        "target_delta_weight": objective.target_delta_weight,
        "sequence_utility_weight": objective.sequence_utility_weight,
        "sparse_teacher_kl_weight": objective.sparse_teacher_kl_weight,
        "huber_delta": objective.huber_delta,
    }
    if source_weights != expected_weights:
        raise ValueError(
            f"objective definition changed since Stage-5F-A: {source_weights} != {expected_weights}"
        )

    model_dim = int(backend.model.config.hidden_size)
    table = IndependentPairTensorTable(expected_pair_ids, (args.k, model_dim), init_std=0.0).to(
        device
    )
    optimizer = torch.optim.AdamW(table.parameters(), lr=args.direct_lr, weight_decay=0.0)
    restored_source = load_training_checkpoint(
        args.source_checkpoint, table=table, optimizer=optimizer
    )
    loaded_source_hash = tensor_state_sha256(table.state_dict())
    if loaded_source_hash != source_integrity["source_delta_tensor_sha256"]:
        raise ValueError("loaded source DeltaE hash differs from the integrity sidecar")
    source_tensor = table.stacked().detach().cpu().clone()
    update_counts = restored_source["update_counts"]
    base_norms = _precompute_direct_base_norms(
        backend=backend, rows=rows, device=device, k=args.k
    ).to(device)

    source_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=source_tensor,
        pair_ids=expected_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
        control="stage5fa_u64_resume_validation",
        updates=SOURCE_UPDATES,
    )
    reproduction = metric_reproduction_report(
        actual=source_evaluation["summary"],
        expected=source_run["final_evaluation"]["summary"],
        paths=REPRODUCTION_PATHS,
        tolerance=args.reproduction_tolerance,
    )
    resume_validation = {
        "passed": bool(
            source_payload_report["passed"] and model_identity["passed"] and reproduction["passed"]
        ),
        "validated_before_any_new_update": True,
        "source_checkpoint": str(args.source_checkpoint),
        "pair_count": len(expected_pair_ids),
        "pair_ids_match_exactly_in_order": True,
        "update_accounting": update_count_summary(expected_pair_ids, update_counts),
        "optimizer_state_present_and_nonempty": source_payload_report["optimizer_state_present"],
        "optimizer_state_count": source_payload_report["optimizer_state_count"],
        "optimizer_learning_rates": source_payload_report["optimizer_learning_rates"],
        "delta_tensor_sha256": loaded_source_hash,
        "source_integrity_sidecar": str(args.output_dir / "source_integrity_manifest.json"),
        "objective_weights": expected_weights,
        "model_identity": model_identity,
        "prompt_contract": prompt_contract,
        "target_token_utility_identity": target_identity,
        "metric_reproduction": reproduction,
        "validated_utc": utc_now(),
    }
    atomic_write_json(args.output_dir / "resume_validation.json", resume_validation)
    write_jsonl(
        args.output_dir / "evaluation_u064_resume_validation.jsonl", source_evaluation["rows"]
    )
    if not resume_validation["passed"]:
        raise ValueError(f"u64 resume validation failed before updates: {resume_validation}")
    print(
        f"resume-integrity passed pairs=192 updates=64 delta_sha={loaded_source_hash} "
        f"metric_max_abs_delta={reproduction['maximum_absolute_delta']:.8g}",
        flush=True,
    )

    source_history_entry = next(
        item for item in source_run["history"] if int(item["updates_per_pair"]) == SOURCE_UPDATES
    )
    source_history_entry = dict(source_history_entry)
    source_history_entry["evaluation_summary"] = source_evaluation["summary"]
    source_history_entry["delta_tensor"] = delta_tensor_summary(source_tensor)
    source_history_entry["source_stage5fa_checkpoint"] = str(args.source_checkpoint)
    history: list[dict[str, Any]] = [source_history_entry]
    completed_rounds = SOURCE_UPDATES
    previous_snapshot = source_tensor.clone()

    latest_pointer = args.output_dir / "latest_checkpoint.json"
    terminal_from_resume = False
    if not args.no_resume and latest_pointer.exists():
        pointer = _load_json(latest_pointer)
        checkpoint_path = Path(pointer["checkpoint"])
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        completed_rounds = int(payload["completed_rounds"])
        continuation_report = validate_source_checkpoint_payload(
            payload,
            expected_pair_ids=expected_pair_ids,
            expected_updates=completed_rounds,
            expected_lr=args.direct_lr,
        )
        if not continuation_report["passed"]:
            raise ValueError(
                f"continuation checkpoint failed validation: {continuation_report['errors']}"
            )
        embedded_hash = payload.get("metadata", {}).get("delta_tensor_sha256")
        if continuation_report["delta_tensor_sha256"] != embedded_hash:
            raise ValueError("continuation checkpoint embedded DeltaE hash mismatch")
        restored = load_training_checkpoint(checkpoint_path, table=table, optimizer=optimizer)
        update_counts = restored["update_counts"]
        history = _load_json(args.output_dir / "history.json")
        previous_snapshot = table.stacked().detach().cpu().clone()
        if not history or int(history[-1]["updates_per_pair"]) != completed_rounds:
            raise ValueError("continuation history does not terminate at latest checkpoint")
        resumed_convergence = history[-1].get("convergence", {})
        resumed_instability = history[-1].get("numerical_instability", {"unstable": False})
        terminal_from_resume = bool(
            resumed_instability.get("unstable")
            or (completed_rounds >= args.minimum_updates and resumed_convergence.get("plateau"))
            or completed_rounds >= args.hard_cap_updates
        )
        print(f"resumed EXP-016B continuation at u{completed_rounds}", flush=True)

    schedule = extension_checkpoint_schedule(hard_cap=args.hard_cap_updates)
    interval_reports: list[dict[str, float]] = []
    checkpoint_path: Path | None = None
    last_progress = time.perf_counter()
    terminal_reason: str | None = None
    final_plateau: dict[str, Any] = {"assessable": False, "plateau": False}
    instability = {"unstable": False}

    loop_maximum = completed_rounds if terminal_from_resume else args.hard_cap_updates
    for update_round in range(completed_rounds + 1, loop_maximum + 1):
        order = list(range(len(rows)))
        random.Random(args.training_seed * 1_000_000 + update_round).shuffle(order)
        for batch_number, index in enumerate(order, start=1):
            batch_rows = [rows[index]]
            batch = _collate(batch_rows, device=device, k=args.k)
            delta_slots = table.forward_indices([index])
            student = _forward_direct_delta(backend=backend, batch=batch, delta_slots=delta_slots)
            loss, terms = _training_loss(
                logits=student["target_logits"], batch=batch, objective=objective
            )
            if not torch.isfinite(loss):
                raise FloatingPointError(
                    f"nonfinite loss at round={update_round} pair={expected_pair_ids[index]}"
                )
            grad_norm = apply_independent_optimizer_step(
                optimizer=optimizer,
                loss=loss,
                table=table,
                selected_indices=[index],
                update_counts=update_counts,
                base_norms=base_norms,
                ratio_budget=1.0,
            )
            if not math.isfinite(grad_norm) or not torch.isfinite(table.rows[index]).all():
                raise FloatingPointError(
                    f"nonfinite gradient/DeltaE at round={update_round} pair={expected_pair_ids[index]}"
                )
            interval_reports.append(
                {
                    "objective": float(loss.detach().cpu()),
                    "gradient_norm": grad_norm,
                    "sequence_utility_huber": float(terms["sequence_utility_huber"].detach().cpu()),
                    "target_delta_huber": float(terms["target_delta_huber"].detach().cpu()),
                    "sparse_teacher_kl": float(terms["sparse_teacher_kl"].detach().cpu()),
                }
            )
            now = time.perf_counter()
            if now - last_progress >= args.progress_interval_s:
                last_progress = now
                accounting = update_count_summary(expected_pair_ids, update_counts)
                print(
                    f"direct5fb round={update_round}/{args.hard_cap_updates} pair={batch_number}/192 "
                    f"updates={accounting['minimum_updates_per_pair']}-{accounting['maximum_updates_per_pair']} "
                    f"elapsed={(now - started) / 3600.0:.2f}h",
                    flush=True,
                )

        accounting = update_count_summary(expected_pair_ids, update_counts)
        if (
            not accounting["all_pairs_equal"]
            or accounting["minimum_updates_per_pair"] != update_round
        ):
            raise RuntimeError(f"unequal updates after round {update_round}: {accounting}")
        if update_round not in schedule:
            continue

        current_snapshot = table.stacked().detach().cpu()
        evaluation = _evaluate_tensor(
            backend=backend,
            rows=rows,
            tensor=current_snapshot,
            pair_ids=expected_pair_ids,
            device=device,
            k=args.k,
            batch_size=args.batch_size,
            huber_delta=objective.huber_delta,
            control=f"direct_delta_u{update_round}",
            updates=update_round,
        )
        checkpoint_entry = {
            "updates_per_pair": update_round,
            "pair_ids": expected_pair_ids,
            "update_accounting": accounting,
            "train_interval": _interval_summary(interval_reports),
            "fraction_at_ratio_boundary": _boundary_fraction(table, base_norms, 1.0),
            "delta_tensor": delta_tensor_summary(current_snapshot),
            "movement_from_previous_checkpoint": _movement_summary(
                current_snapshot, previous_snapshot
            ),
            "evaluation_summary": evaluation["summary"],
            "timestamp_utc": utc_now(),
        }
        history = [item for item in history if int(item["updates_per_pair"]) != update_round]
        history.append(checkpoint_entry)
        history.sort(key=lambda item: int(item["updates_per_pair"]))
        final_plateau = eligible_plateau(history, current_updates=update_round)
        checkpoint_entry["convergence"] = final_plateau
        instability = numerical_instability_report(history)
        checkpoint_entry["numerical_instability"] = instability

        delta_hash = tensor_state_sha256(table.state_dict())
        checkpoint_path = (
            args.output_dir
            / "checkpoints"
            / f"direct_sequence_utility_plus_sparse_kl_ratio1.0_u{update_round:03d}.pt"
        )
        metadata = {
            "run_format": ORACLE_EXTENSION_VERSION,
            "component": "direct_delta",
            "objective": objective.name,
            "objective_weights": expected_weights,
            "ratio_budget": 1.0,
            "k": args.k,
            "position": "last_user_k",
            "injection_site": "input_embedding",
            "pair_ids": expected_pair_ids,
            "ordered_pair_manifest_sha256": source_integrity["ordered_pair_manifest_sha256"],
            "delta_tensor_sha256": delta_hash,
            "source_checkpoint_file_sha256": source_integrity["source_checkpoint_file_sha256"],
            "source_delta_tensor_sha256": source_integrity["source_delta_tensor_sha256"],
            "pair_cache_sha256": pair_cache_sha,
            "model_identity": model_identity,
            "training_seed": args.training_seed,
            "learning_rate": args.direct_lr,
            "source_commit": maybe_git_commit(),
        }
        save_training_checkpoint(
            checkpoint_path,
            table=table,
            optimizer=optimizer,
            update_counts=update_counts,
            completed_rounds=update_round,
            metadata=metadata,
        )
        checkpoint_integrity = _checkpoint_sidecar(checkpoint_path, metadata=metadata)
        checkpoint_entry["checkpoint"] = str(checkpoint_path)
        checkpoint_entry["checkpoint_integrity"] = checkpoint_integrity
        atomic_write_json(
            latest_pointer, {"checkpoint": str(checkpoint_path), "updates_per_pair": update_round}
        )
        atomic_write_json(args.output_dir / "history.json", history)
        write_jsonl(args.output_dir / f"evaluation_u{update_round:03d}.jsonl", evaluation["rows"])
        print(
            f"checkpoint u{update_round} spearman={evaluation['summary']['u_text_vs_u_student_spearman']:.6f} "
            f"huber={evaluation['summary']['sequence_utility_huber']['mean']:.6f} "
            f"plateau={final_plateau.get('plateau')} boundary={checkpoint_entry['fraction_at_ratio_boundary']:.6f}",
            flush=True,
        )
        interval_reports = []
        previous_snapshot = current_snapshot.clone()

        if instability["unstable"]:
            terminal_reason = "numerical_instability"
            break
        if update_round >= args.minimum_updates and final_plateau.get("plateau"):
            terminal_reason = "first_eligible_plateau"
            break
        if update_round >= args.hard_cap_updates:
            terminal_reason = "hard_cap_without_plateau"
            break

    if checkpoint_path is None:
        if terminal_from_resume:
            checkpoint_path = Path(history[-1]["checkpoint"])
            final_plateau = history[-1]["convergence"]
            instability = history[-1].get("numerical_instability", {"unstable": False})
            terminal_reason = "reused_terminal_checkpoint"
        else:
            raise RuntimeError("EXP-016B produced no extension checkpoint")

    final_updates = int(history[-1]["updates_per_pair"])
    final_tensor = table.stacked().detach().cpu().clone()
    if final_updates != min(update_counts) or final_updates != max(update_counts):
        raise RuntimeError("final updates-per-pair accounting is not exact")

    # Final controls are deliberately re-evaluated with identical pair IDs and scoring code.
    zero_tensor = torch.zeros_like(final_tensor)
    random_tensor = _matched_random_tensor(
        final_tensor, training_seed=args.training_seed, ratio_budget=1.0, k=args.k
    )
    zero_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=zero_tensor,
        pair_ids=expected_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
        control="zero_direct_delta_final_rerun",
        updates=0,
    )
    random_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=random_tensor,
        pair_ids=expected_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
        control="matched_norm_random_delta_final_rerun",
        updates=final_updates,
    )
    u64_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=source_tensor,
        pair_ids=expected_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
        control="stage5fa_u64_final_rerun",
        updates=SOURCE_UPDATES,
    )
    final_evaluation = _evaluate_tensor(
        backend=backend,
        rows=rows,
        tensor=final_tensor,
        pair_ids=expected_pair_ids,
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
        control=f"direct_delta_final_u{final_updates}",
        updates=final_updates,
    )
    underoptimized = _evaluate_underoptimized_stage5e(
        backend=backend,
        rows=rows,
        stage5e_dir=args.stage5e_dir,
        output_dir=args.output_dir / "controls",
        device=device,
        k=args.k,
        batch_size=args.batch_size,
        huber_delta=objective.huber_delta,
    )

    evaluations = {
        "zero": _portable_evaluation(
            zero_evaluation, args.output_dir / "controls" / "zero_rows.jsonl"
        ),
        "matched_random": _portable_evaluation(
            random_evaluation, args.output_dir / "controls" / "matched_random_rows.jsonl"
        ),
        "stage5e_two_update": underoptimized["evaluation"],
        "stage5fa_u64": _portable_evaluation(
            u64_evaluation, args.output_dir / "controls" / "stage5fa_u64_rows.jsonl"
        ),
        "final": _portable_evaluation(
            final_evaluation, args.output_dir / "controls" / "final_rows.jsonl"
        ),
    }
    paired_bootstrap = final_control_bootstrap(
        final_rows=final_evaluation["rows"],
        zero_rows=zero_evaluation["rows"],
        random_rows=random_evaluation["rows"],
        u64_rows=u64_evaluation["rows"],
        samples=args.bootstrap_samples,
        seed=20260809,
    )
    gate = utility_capacity_gate(
        summary=final_evaluation["summary"],
        zero_summary=zero_evaluation["summary"],
        plateau=bool(final_plateau.get("plateau")),
    )
    decision = terminal_decision(
        final_updates=final_updates,
        plateau=bool(final_plateau.get("plateau")),
        gate_passed=bool(gate["passed"]),
        hard_cap=args.hard_cap_updates,
        numerical_instability=bool(instability.get("unstable")),
    )
    decision.update(
        {
            "terminal_reason": terminal_reason,
            "stage5e_direct_failure_interpretation_superseded": True,
            "stage5e_sparse_objective_mismatch_remains_valid": True,
            "stage5fa_strong_not_plateaued_evidence_preserved": True,
            "recommended_next_milestone": (
                "properly_optimized_128d_pair_latent_shared_injector_decoder_capacity"
                if decision["branch"] == "input_embedding_channel_capacity_passed_after_convergence"
                else (
                    "later_layer_residual_injection_site_comparison"
                    if decision["branch"] == "converged_input_embedding_channel_insufficient"
                    else "review_complete_curve_before_any_channel_redesign"
                )
            ),
        }
    )
    control_comparison = {
        name: {
            "updates_per_pair": (
                2
                if name == "stage5e_two_update"
                else SOURCE_UPDATES
                if name == "stage5fa_u64"
                else final_updates
                if name in {"matched_random", "final"}
                else 0
            ),
            "u_text_vs_u_student_spearman": value["summary"]["u_text_vs_u_student_spearman"],
            "u_text_vs_u_student_pearson": value["summary"]["u_text_vs_u_student_pearson"],
            "sign_agreement": value["summary"]["positive_negative_sign_agreement"],
            "sequence_utility_huber": value["summary"]["sequence_utility_huber"]["mean"],
            "target_nll": value["summary"]["target_nll"]["mean"],
            "target_token_delta_correlation": value["summary"][
                "target_token_delta_correlation_global"
            ],
            "sparse_teacher_kl": value["summary"]["sparse_teacher_kl"]["mean"],
            "delta_ratio_mean": value["summary"]["delta_ratio"]["mean"],
            "delta_ratio_max": value["summary"]["delta_ratio"]["max"],
        }
        for name, value in evaluations.items()
    }

    summary = {
        "format": ORACLE_EXTENSION_VERSION,
        "status": "completed",
        "timestamp_utc": utc_now(),
        "source_commit": maybe_git_commit(),
        "output_dir": str(args.output_dir),
        "source_checkpoint": str(args.source_checkpoint),
        "source_integrity": source_integrity,
        "resume_validation": resume_validation,
        "objective": objective.name,
        "objective_weights": expected_weights,
        "ratio_budget": 1.0,
        "k": args.k,
        "position": "last_user_k",
        "injection_site": "input_embedding",
        "pair_count": len(expected_pair_ids),
        "pair_ids": expected_pair_ids,
        "training_seed": args.training_seed,
        "learning_rate": args.direct_lr,
        "checkpoint_interval": CHECKPOINT_INTERVAL,
        "minimum_terminal_updates": args.minimum_updates,
        "hard_cap_updates": args.hard_cap_updates,
        "final_updates_per_pair": final_updates,
        "final_update_accounting": update_count_summary(expected_pair_ids, update_counts),
        "final_checkpoint": str(checkpoint_path),
        "convergence_history": history,
        "final_plateau": final_plateau,
        "numerical_instability": instability,
        "evaluations": evaluations,
        "final_evaluation": evaluations["final"],
        "control_comparison": control_comparison,
        "paired_bootstrap": paired_bootstrap,
        "utility_capacity_gate": gate,
        "decision": decision,
        "runtime_s": time.perf_counter() - started,
        "hard_scope": {
            "qwen_frozen": True,
            "teacher_forced_target_scoring_only": True,
            "resumed_without_reinitializing_delta_or_optimizer": True,
            "objective_changed": False,
            "injection_position_changed": False,
            "k_changed": False,
            "pair_z_or_injector_decoder_training": False,
            "memory_compiler_training": False,
            "signed_selector_used": False,
            "full_bank_model_training": False,
            "appworld_generation_evaluation": False,
            "stage_c2_started": False,
            "end_to_end_rcmf_training": False,
        },
    }
    atomic_write_json(args.output_dir / "summary.json", summary)
    atomic_write_text(args.output_dir / "report.md", _report(summary))
    print(
        json.dumps(
            {
                "summary": str(args.output_dir / "summary.json"),
                "final_updates_per_pair": final_updates,
                "plateau": final_plateau,
                "gate": gate,
                "decision": decision,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
