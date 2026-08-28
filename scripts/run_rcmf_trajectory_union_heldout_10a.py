from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    compile_differentiable_field,
    read_compiled_field,
    tensor_sha256,
)
from rcmf.training.rcmf_onpolicy_trajectory_distillation_10a import (
    GLOBAL_SEED,
    candidate_eligibility,
    select_final_candidate,
    strict_no_progress_loops,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, read_jsonl, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    _atomic_torch_save,
    _build_components,
    _load_data,
    _paths as parent_paths,
    _runtime_tensors,
)
from scripts.run_rcmf_joint_full_bank_first37_9a import (
    LiveFieldQueryEncoder,
    _run_task,
)
from scripts.run_rcmf_q90_trajectory_common_9c import load_frozen_backend


RUN_UUID = "rcmf_onpolicy_trajectory_distillation_10a_20260828_001"
RESULT_FORMAT = "rcmf_trajectory_union_heldout_task_10a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/stage_c_rcmf_onpolicy_trajectory_distillation_10a.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "run", "finalize", "select"), required=True)
    parser.add_argument("--stage", choices=("reader", "writer"), required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--condition", choices=("correct", "shuffle"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp032a_heldout")
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in read_jsonl(path)
        if row.get("attempt_id") is not None
    }


def _candidate_id(stage: str, epoch: int) -> str:
    return f"{stage}_epoch_{epoch:02d}"


def _condition_codes(stage: str) -> tuple[str, str]:
    return ("RA", "RS") if stage == "reader" else ("WA", "WS")


def paths(artifact_dir: Path, stage: str, epoch: int) -> dict[str, Path]:
    root = artifact_dir / "heldout" / _candidate_id(stage, epoch)
    return {
        "root": root,
        "manifest": root / "condition_manifest.json",
        "field": root / "candidate_401_field.pt",
        "field_report": root / "candidate_401_field_report.json",
        "static_assets": artifact_dir / "raw_audit/static_prompt_assets.json",
        "deployment": root / "candidate_401_field.pt",
        "instant_add": root / "candidate_401_field_report.json",
        "field_provenance": root / "candidate_401_field_report.json",
        "final": root / "final_summary.json",
        "selection": artifact_dir / "heldout/candidate_selection.json",
        "reader_selection": artifact_dir / "heldout/reader_selection.json",
    }


def _compile_candidate_field(
    *,
    cfg: Any,
    settings: Mapping[str, Any],
    stage: str,
    checkpoint_path: Path,
    task_fields_path: Path,
    output: Path,
) -> dict[str, Any]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if checkpoint.get("format") != "rcmf_onpolicy_trajectory_checkpoint_10a_v1":
        raise ValueError("Candidate checkpoint format differs")
    if str(checkpoint.get("stage")) != stage:
        raise ValueError("Candidate checkpoint stage differs")
    reader_state = checkpoint["reader_state_dict"]
    writer_state = checkpoint["writer_state_dict"]
    started = time.perf_counter()
    if stage == "reader":
        frozen = torch.load(task_fields_path, map_location="cpu", weights_only=False)
        fields = frozen["fields"]
        A = fields["correct"]["A_total"].float()
        B = fields["correct"]["B_total"].float()
        shuffled_A = fields["key_payload_shuffle"]["A_total"].float()
        shuffled_B = fields["key_payload_shuffle"]["B_total"].float()
        immutable = settings["immutable_exp031a"]
        original_correct = torch.load(
            Path(str(immutable["heldout_correct_field"])),
            map_location="cpu",
            weights_only=False,
        )
        original_shuffle = torch.load(
            Path(str(immutable["heldout_shuffle_field"])),
            map_location="cpu",
            weights_only=False,
        )
        immutable_error = max(
            float((A - original_correct["A"].float()).abs().max()),
            float((B - original_correct["B"].float()).abs().max()),
            float((shuffled_A - original_shuffle["A"].float()).abs().max()),
            float((shuffled_B - original_shuffle["B"].float()).abs().max()),
        )
        if immutable_error > 5.0e-5:
            raise RuntimeError(
                f"Reader-only 401 field differs from immutable EXP-031A: {immutable_error}"
            )
        memory_count = int(frozen["train_memory_count"])
        source = "immutable_exp031a_401_field_with_candidate_reader"
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        parent_root = Path(str(settings["immutable_exp031a"]["artifact_root"]))
        data = _load_data(parent_paths(cfg.raw["stage_c_9a"], parent_root))
        tensors = _runtime_tensors(data, device)
        writer, _ = _build_components(device)
        writer.load_state_dict(writer_state)
        writer.eval()
        with torch.no_grad():
            payloads = writer(tensors["memory_views"])
            A, B = compile_differentiable_field(
                keys=tensors["keys"], payloads=payloads, rho=tensors["rho"]
            )
            shuffled_A, shuffled_B = compile_differentiable_field(
                keys=tensors["keys"],
                payloads=payloads[tensors["permutation"]],
                rho=tensors["rho"],
            )
        A, B = A.cpu(), B.cpu()
        shuffled_A, shuffled_B = shuffled_A.cpu(), shuffled_B.cpu()
        memory_count = len(data["train_ids"])
        source = "recompiled_candidate_writer_401_field"
    if memory_count != 401:
        raise ValueError("Candidate heldout field must contain 401 memories")
    payload = {
        "format": "rcmf_trajectory_union_candidate_field_10a_v1",
        "stage": stage,
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "writer_sha256": module_state_sha256_from_state(writer_state),
        "reader_sha256": module_state_sha256_from_state(reader_state),
        "memory_count": memory_count,
        "A": A,
        "B": B,
        "shuffled_A": shuffled_A,
        "shuffled_B": shuffled_B,
        "source": source,
    }
    _atomic_torch_save(payload, output)
    report = {
        "format": "rcmf_trajectory_union_candidate_field_report_10a_v1",
        "stage": stage,
        "memory_count": memory_count,
        "checkpoint_sha256": payload["checkpoint_sha256"],
        "field_sha256": sha256_file(output),
        "A_sha256": tensor_sha256(A),
        "B_sha256": tensor_sha256(B),
        "shuffled_A_sha256": tensor_sha256(shuffled_A),
        "shuffled_B_sha256": tensor_sha256(shuffled_B),
        "field_shapes": {"A": list(A.shape), "B": list(B.shape)},
        "runtime_memory_scan": False,
        "runtime_retrieval": False,
        "runtime_per_memory_scoring": False,
        "immutable_exp031a_max_abs_error": (
            immutable_error if stage == "reader" else None
        ),
        "elapsed_seconds": time.perf_counter() - started,
        "passed": tuple(A.shape) == (960, 8, 256)
        and tuple(B.shape) == (8, 256),
    }
    if not report["passed"]:
        raise RuntimeError("Candidate field shape validation failed")
    return report


def module_state_sha256_from_state(state: Mapping[str, Tensor]) -> str:
    digest = __import__("hashlib").sha256()
    for name, value in sorted(state.items()):
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(tensor.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


class CandidateFieldRuntime:
    def __init__(
        self,
        *,
        settings_9a: Mapping[str, Any],
        backend: Any,
        checkpoint_path: Path,
        field_path: Path,
        condition_codes: tuple[str, str],
    ) -> None:
        self.backend = backend
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        payload = torch.load(field_path, map_location="cpu", weights_only=False)
        if str(payload["checkpoint_sha256"]) != sha256_file(checkpoint_path):
            raise ValueError("Candidate field/checkpoint identity differs")
        _, self.reader = _build_components(backend.device)
        self.reader.load_state_dict(checkpoint["reader_state_dict"])
        self.reader.eval()
        for parameter in self.reader.parameters():
            parameter.requires_grad_(False)
        self.query_encoder = LiveFieldQueryEncoder(settings=settings_9a, backend=backend)
        self.memory_count = int(payload["memory_count"])
        self.field_path_value = field_path
        self.codes = condition_codes
        self.fields = {
            condition_codes[0]: (
                payload["A"].to(backend.device, torch.float32),
                payload["B"].to(backend.device, torch.float32),
            ),
            condition_codes[1]: (
                payload["shuffled_A"].to(backend.device, torch.float32),
                payload["shuffled_B"].to(backend.device, torch.float32),
            ),
        }

    def field_path(self, condition: str) -> Path:
        if condition not in self.fields:
            raise ValueError(f"Unknown candidate condition: {condition}")
        return self.field_path_value

    @torch.no_grad()
    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[Tensor, dict[str, Any]]:
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        views, query = self.query_encoder.query(messages)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        query_seconds = time.perf_counter() - started
        A, B = self.fields[condition]
        started = time.perf_counter()
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
        if self.backend.device.type == "cuda":
            torch.cuda.synchronize()
        raw = B + torch.einsum("k,ksp->sp", query.to(A.dtype), A)
        return slots, {
            "state_views": views,
            "query": query,
            "query_seconds": query_seconds,
            "field_read_seconds": time.perf_counter() - started,
            "field_control": "correct" if condition == self.codes[0] else "key_payload_shuffle",
            "raw_field_rms": float(raw.float().square().mean().sqrt().cpu()),
            "runtime_memory_scan": False,
            "runtime_retrieval": False,
            "runtime_per_memory_scoring": False,
        }


def _manifest(
    *,
    task_ids: Sequence[str],
    conditions: tuple[str, str],
    stage: str,
    epoch: int,
    checkpoint_path: Path,
    field_report: Mapping[str, Any],
    config_sha256: str,
) -> dict[str, Any]:
    rows = [
        {
            "task_id": task_id,
            "condition": condition,
            "field_control": "correct" if condition == conditions[0] else "key_payload_shuffle",
            "candidate_stage": stage,
            "candidate_epoch": epoch,
            "memory_count": 401,
            "raw_memory_prompt": False,
            "runtime_retrieval": False,
        }
        for condition in conditions
        for task_id in task_ids
    ]
    payload = {
        "format": "rcmf_trajectory_union_heldout_manifest_10a_v1",
        "global_seed": GLOBAL_SEED,
        "stage": stage,
        "epoch": epoch,
        "task_ids": list(task_ids),
        "conditions": list(conditions),
        "task_condition_count": len(rows),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "field_sha256": field_report["field_sha256"],
        "config_sha256": config_sha256,
        "rows": rows,
        "frozen_before_outcomes": True,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def _result_path(p: Mapping[str, Path], condition: str, task_id: str) -> Path:
    return p["root"] / "conditions" / condition / "task_results" / f"{task_id}.json"


def _summary(rows: Sequence[Mapping[str, Any]], condition: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    loops = {}
    for row in rows:
        counts.update(row["counts"])
        loops[str(row["task_id"])] = strict_no_progress_loops(row)
    success_ids = sorted(str(row["task_id"]) for row in rows if bool(row["success"]))
    return {
        "format": "rcmf_trajectory_union_heldout_condition_summary_10a_v1",
        "condition": condition,
        "task_count": len(rows),
        "success_count": len(success_ids),
        "success_ids": success_ids,
        "total_steps": sum(int(row["step_count"]) for row in rows),
        "total_wall_seconds": sum(float(row["wall_seconds"]) for row in rows),
        "total_prompt_tokens": sum(int(row["usage"].get("prompt_tokens", 0)) for row in rows),
        "total_generated_tokens": sum(int(row["usage"].get("completion_tokens", 0)) for row in rows),
        "counts": dict(counts),
        "strict_no_progress_loop_count": sum(len(value) for value in loops.values()),
        "strict_no_progress_loops": loops,
        "passed_infrastructure": len(rows) == 8
        and all(
            row["status"] == "complete"
            and row["success_source"] == "evaluation.success"
            and row["raw_audit_complete"]
            for row in rows
        ),
    }


def _immutable_references(settings: Mapping[str, Any], task_ids: Sequence[str]) -> dict[str, Any]:
    root = Path(str(settings["immutable_exp031c"]["artifact_root"])) / "heldout"
    parent_manifest = _json(root / "condition_manifest.json")
    if [str(value) for value in parent_manifest["task_ids"]] != list(task_ids):
        raise ValueError("Immutable heldout task order differs")
    immutable = settings["immutable_exp031a"]
    expected_fields = {
        "H1": str(immutable["heldout_correct_field_sha256"]),
        "H2": str(immutable["heldout_shuffle_field_sha256"]),
    }
    if sha256_file(Path(str(immutable["heldout_correct_field"]))) != expected_fields["H1"]:
        raise ValueError("Immutable H1 field differs")
    if sha256_file(Path(str(immutable["heldout_shuffle_field"]))) != expected_fields["H2"]:
        raise ValueError("Immutable H2 field differs")
    summaries = {
        condition: _json(root / "conditions" / condition / "summary.json")
        for condition in ("H0", "H1", "H2")
    }
    task_rows = {}
    task_hashes = {}
    for condition in ("H0", "H1", "H2"):
        task_rows[condition], task_hashes[condition] = {}, {}
        for task_id in task_ids:
            path = root / "conditions" / condition / "task_results" / f"{task_id}.json"
            row = _json(path)
            if (
                str(row["task_id"]) != task_id
                or row["status"] != "complete"
                or row["success_source"] != "evaluation.success"
                or not row["raw_audit_complete"]
            ):
                raise ValueError("Immutable heldout task artifact is invalid")
            task_rows[condition][task_id] = row
            task_hashes[condition][task_id] = sha256_file(path)
    return {
        "root": str(root),
        "manifest_sha256": sha256_file(root / "condition_manifest.json"),
        "summaries": summaries,
        "tasks": task_rows,
        "task_hashes": task_hashes,
    }


def _rank_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -int(row["correct_success_count"]),
        -int(row["correct_minus_shuffle"]),
        -int(row["retained_original_success_count"]),
        int(row["no_progress_loop_count"]),
        int(row["total_steps"]),
        0 if str(row["stage"]) == "reader_only" else 1,
        int(row["epoch"]),
    )


def _finalize(
    *, args: argparse.Namespace, settings: Mapping[str, Any], p: Mapping[str, Path]
) -> dict[str, Any]:
    manifest = _json(p["manifest"])
    task_ids = [str(value) for value in manifest["task_ids"]]
    correct_code, shuffle_code = _condition_codes(args.stage)
    summaries = {
        code: _json(p["root"] / "conditions" / code / "summary.json")
        for code in (correct_code, shuffle_code)
    }
    refs = _immutable_references(settings, task_ids)
    original = refs["summaries"]["H1"]
    eligibility = candidate_eligibility(
        correct_success_ids=summaries[correct_code]["success_ids"],
        shuffle_success_ids=summaries[shuffle_code]["success_ids"],
        original_success_ids=original["success_ids"],
        correct_loop_count=int(summaries[correct_code]["strict_no_progress_loop_count"]),
        original_loop_count=sum(
            len(strict_no_progress_loops(refs["tasks"]["H1"][task_id]))
            for task_id in task_ids
        ),
        infrastructure_valid=bool(summaries[correct_code]["passed_infrastructure"])
        and bool(summaries[shuffle_code]["passed_infrastructure"]),
    )
    candidate = {
        "candidate_id": _candidate_id(args.stage, args.epoch),
        "stage": "reader_only" if args.stage == "reader" else "writer_reader",
        "epoch": args.epoch,
        "checkpoint": str(args.checkpoint),
        "checkpoint_sha256": sha256_file(args.checkpoint),
        "field": str(p["field"]),
        "field_sha256": sha256_file(p["field"]),
        "correct_condition": correct_code,
        "shuffle_condition": shuffle_code,
        "correct_success_count": int(summaries[correct_code]["success_count"]),
        "correct_success_ids": summaries[correct_code]["success_ids"],
        "shuffle_success_count": int(summaries[shuffle_code]["success_count"]),
        "shuffle_success_ids": summaries[shuffle_code]["success_ids"],
        "correct_minus_shuffle": int(summaries[correct_code]["success_count"])
        - int(summaries[shuffle_code]["success_count"]),
        "retained_original_success_count": len(
            set(summaries[correct_code]["success_ids"]) & set(original["success_ids"])
        ),
        "no_progress_loop_count": int(
            summaries[correct_code]["strict_no_progress_loop_count"]
        ),
        "total_steps": int(summaries[correct_code]["total_steps"]),
        "summaries": summaries,
        "immutable_references": {
            "root": refs["root"],
            "manifest_sha256": refs["manifest_sha256"],
            "task_hashes": refs["task_hashes"],
            "H0_success_ids": refs["summaries"]["H0"]["success_ids"],
            "H1_success_ids": original["success_ids"],
            "H2_success_ids": refs["summaries"]["H2"]["success_ids"],
        },
        **eligibility,
    }
    result = {
        "format": "rcmf_trajectory_union_heldout_candidate_10a_v1",
        "candidate": candidate,
        "passed": True,
    }
    atomic_write_json(p["final"], result)
    return result


def _select(args: argparse.Namespace, p: Mapping[str, Path]) -> dict[str, Any]:
    heldout_root = args.artifact_dir / "heldout"
    reader_rows = [
        _json(heldout_root / f"reader_epoch_{epoch:02d}/final_summary.json")["candidate"]
        for epoch in (1, 2)
    ]
    reader_ordered = sorted(reader_rows, key=_rank_key)
    selected_reader = select_final_candidate(reader_rows)
    reader_result = {
        "format": "rcmf_trajectory_union_reader_selection_10a_v1",
        "candidates": reader_rows,
        "selected_eligible_reader": selected_reader,
        "stage_c_initial_checkpoint": str(
            (selected_reader or reader_ordered[0])["checkpoint"]
        ),
        "stage_c_required": selected_reader is None
        or int(selected_reader["correct_success_count"]) < 7,
    }
    atomic_write_json(p["reader_selection"], reader_result)
    if args.stage == "reader":
        return reader_result
    writer_path = heldout_root / "writer_epoch_01/final_summary.json"
    candidates = list(reader_rows)
    if writer_path.exists():
        candidates.append(_json(writer_path)["candidate"])
    selected = select_final_candidate(candidates)
    result = {
        "format": "rcmf_trajectory_union_final_candidate_selection_10a_v1",
        "candidates": candidates,
        "selected_candidate": selected,
        "decision": (
            "trajectory_union_candidate_selected"
            if selected is not None
            else "trajectory_union_distillation_failed_on_heldout"
        ),
        "model_frozen_before_first37": selected is not None,
    }
    atomic_write_json(p["selection"], result)
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_10a"]
    if os.name != "nt" and not os.path.ismount(Path(str(settings["persistent_root"]))):
        raise RuntimeError("Persistent filesystem is not mounted")
    if len({args.local_head, args.github_head, args.lambda_head}) != 1:
        raise ValueError("Local/GitHub/Lambda heads differ")
    if args.attempt_id in _attempt_ids(args.artifact_dir / "attempts.jsonl"):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    p = paths(args.artifact_dir, args.stage, args.epoch)
    if args.phase != "select" and args.checkpoint is None:
        raise ValueError("--checkpoint is required")
    source_hashes = {
        "config": sha256_file(args.config),
        "data_manifest": sha256_file(
            Path(str(settings["immutable_exp031a"]["data_manifest"]))
        ),
    }
    if args.checkpoint is not None:
        source_hashes["checkpoint"] = sha256_file(args.checkpoint)
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"exp032a_heldout_{args.phase}_{args.stage}_{args.epoch}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["runtime"]["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "prepare":
            report = _compile_candidate_field(
                cfg=cfg,
                settings=settings,
                stage=args.stage,
                checkpoint_path=args.checkpoint,
                task_fields_path=args.artifact_dir / "preflight/task_legal_fields.pt",
                output=p["field"],
            )
            atomic_write_json(p["field_report"], report)
            data_manifest = _json(Path(str(settings["immutable_exp031a"]["data_manifest"])))
            task_ids = [str(value) for value in data_manifest["heldout_task_ids"]]
            if len(task_ids) != 8:
                raise ValueError("Immutable heldout task count differs")
            manifest = _manifest(
                task_ids=task_ids,
                conditions=_condition_codes(args.stage),
                stage=args.stage,
                epoch=args.epoch,
                checkpoint_path=args.checkpoint,
                field_report=report,
                config_sha256=sha256_file(args.config),
            )
            atomic_write_json(p["manifest"], manifest)
            result, latest = report, p["manifest"]
        elif args.phase == "run":
            if args.condition is None:
                raise ValueError("--condition is required")
            codes = _condition_codes(args.stage)
            condition = codes[0] if args.condition == "correct" else codes[1]
            if condition == codes[1] and not (
                p["root"] / "conditions" / codes[0] / "summary.json"
            ).exists():
                raise RuntimeError("Correct heldout condition must precede shuffle")
            manifest = _json(p["manifest"])
            backend = load_frozen_backend(cfg)
            runtime = CandidateFieldRuntime(
                settings_9a=cfg.raw["stage_c_9a"],
                backend=backend,
                checkpoint_path=args.checkpoint,
                field_path=p["field"],
                condition_codes=codes,
            )
            rows = []
            for task_id in manifest["task_ids"]:
                row, _ = _run_task(
                    task_id=str(task_id),
                    condition=condition,
                    settings=cfg.raw["stage_c_9a"],
                    backend=backend,
                    runtime=runtime,
                    paths=p,
                    manifest=manifest,
                    config_sha256=sha256_file(args.config),
                    attempt_id=args.attempt_id,
                    smoke=False,
                    result_version=RESULT_FORMAT,
                    extra_result_fields={
                        "exp032a_candidate_stage": args.stage,
                        "exp032a_candidate_epoch": args.epoch,
                    },
                    bare_condition=False,
                    condition_name=(
                        "trajectory_trained_correct_field"
                        if condition == codes[0]
                        else "trajectory_trained_key_payload_shuffle"
                    ),
                    memory_count=401,
                    field_artifact_path=p["field"],
                    field_provenance_path=p["field_report"],
                    experiment_prefix="exp032a_heldout",
                )
                rows.append(row)
                attempt.progress(
                    status=f"heldout_{condition.lower()}",
                    completed_tasks=len(rows),
                    total_tasks=8,
                    latest_validated_checkpoint=str(
                        _result_path(p, condition, str(task_id))
                    ),
                )
            result = _summary(rows, condition)
            latest = p["root"] / "conditions" / condition / "summary.json"
            atomic_write_json(latest, result)
        elif args.phase == "finalize":
            result = _finalize(args=args, settings=settings, p=p)
            latest = p["final"]
        else:
            result = _select(args, p)
            latest = p["reader_selection"] if args.stage == "reader" else p["selection"]
        attempt.progress(status="phase_complete", latest_validated_checkpoint=str(latest))
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
