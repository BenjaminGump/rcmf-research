from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any

import _bootstrap  # noqa: F401
import numpy as np
import torch
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.appworld_structured_rescue_7hr import MemoryUseGate
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.ungated_structured_e2e_7h2 import (
    GLOBAL_SEED,
    classify_distribution_shift,
    domain_classifier_audit,
    feature_distribution_report,
    freeze_fresh_test_manifest,
    freeze_transition_shuffle,
    summarize_vector,
)
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_appworld_structured_gated_first37_7hr import StructuredRuntime


REPORT_FORMAT = "ungated_structured_gate_distribution_audit_7h2_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_ungated_structured_e2e_7h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028b_prepare")
    return parser.parse_args()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_live_rows(parent: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    root = parent / "gated_compiled_first37/task_results"
    paths = sorted(root.glob("*.json"))
    if len(paths) != 37:
        raise ValueError(f"Expected 37 frozen first37 task rows, found {len(paths)}")
    for path in paths:
        task = _json(path)
        for step in task["steps"]:
            gate = step["gate"]
            base = {
                "task_id": str(task["task_id"]),
                "step_id": int(step["step_id"]),
                "source_path": str(path),
                "source_sha256": sha256_file(path),
            }
            if gate.get("feature_values") is None:
                missing.append(
                    {
                        **base,
                        "gate_status": str(gate.get("gate_status")),
                        "missing_reason": "frozen_exp028a_live_feature_row_unavailable",
                    }
                )
                continue
            rows.append(
                {
                    **base,
                    "feature_values": list(map(float, gate["feature_values"])),
                    "recorded_probabilities": {
                        str(key): float(value)
                        for key, value in gate["probabilities"].items()
                    },
                }
            )
    if len(rows) + len(missing) != 873:
        raise ValueError("Frozen live-turn accounting differs from EXP-028A")
    return rows, missing


def _model_outputs(
    model: MemoryUseGate,
    values: Sequence[Sequence[float]],
    mean: torch.Tensor,
    std: torch.Tensor,
    temperature: float,
) -> tuple[np.ndarray, np.ndarray]:
    tensor = torch.tensor(values, dtype=torch.float32)
    with torch.no_grad():
        logits = model((tensor - mean) / std)
        probabilities = F.softmax(logits / temperature, dim=-1)
    return logits.numpy(), probabilities.numpy()


def _output_summary(
    *,
    logits: np.ndarray,
    probabilities: np.ndarray,
    labels: Sequence[str],
    thresholds: Sequence[float],
    maximum_harmful: float,
) -> dict[str, Any]:
    positions = {str(value): index for index, value in enumerate(labels)}
    positive = probabilities[:, positions["POSITIVE"]]
    harmful = probabilities[:, positions["HARMFUL"]]
    return {
        "row_count": int(len(probabilities)),
        "positive_probability": summarize_vector(positive),
        "harmful_probability": summarize_vector(harmful),
        "logits": {
            label: summarize_vector(logits[:, index])
            for index, label in enumerate(labels)
        },
        "activation_counts": {
            f"{float(threshold):.2f}": int(
                np.sum((positive >= float(threshold)) & (harmful <= maximum_harmful))
            )
            for threshold in thresholds
        },
    }


def _rank_buckets(values: Sequence[float], count: int = 4) -> list[int]:
    ordered = sorted(range(len(values)), key=lambda index: (float(values[index]), index))
    output = [0] * len(values)
    for rank, index in enumerate(ordered):
        output[index] = min(count - 1, rank * count // len(values))
    return output


def _group_summaries(
    *,
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    logits: np.ndarray,
    probabilities: np.ndarray,
    labels: Sequence[str],
    thresholds: Sequence[float],
    maximum_harmful: float,
    early_max: int,
    middle_max: int,
) -> dict[str, Any]:
    index = {str(value): position for position, value in enumerate(names)}
    selector_scores = [row["feature_values"][index["selector.top1_score"]] for row in rows]
    prompt_lengths = [row["feature_values"][index["state.prompt_token_fraction"]] for row in rows]
    selector_bucket = _rank_buckets(selector_scores)
    prompt_bucket = _rank_buckets(prompt_lengths)
    selectors: dict[str, list[int]] = {
        "all": list(range(len(rows))),
        "first_turn": [position for position, row in enumerate(rows) if int(row["step_id"]) == 1],
        "early": [position for position, row in enumerate(rows) if int(row["step_id"]) <= early_max],
        "middle": [position for position, row in enumerate(rows) if early_max < int(row["step_id"]) <= middle_max],
        "late": [position for position, row in enumerate(rows) if int(row["step_id"]) > middle_max],
    }
    for bucket in range(4):
        selectors[f"selector_score_q{bucket + 1}"] = [
            position for position, value in enumerate(selector_bucket) if value == bucket
        ]
        selectors[f"prompt_length_q{bucket + 1}"] = [
            position for position, value in enumerate(prompt_bucket) if value == bucket
        ]
    return {
        key: _output_summary(
            logits=logits[positions],
            probabilities=probabilities[positions],
            labels=labels,
            thresholds=thresholds,
            maximum_harmful=maximum_harmful,
        )
        for key, positions in selectors.items()
        if positions
    }


def _gate_sensitivity(
    *,
    model: MemoryUseGate,
    validation_values: np.ndarray,
    live_values: np.ndarray,
    mean: torch.Tensor,
    std: torch.Tensor,
    names: Sequence[str],
    positive_index: int,
) -> list[dict[str, Any]]:
    validation_mean = validation_values.mean(axis=0)
    live_mean = live_values.mean(axis=0)
    midpoint = torch.tensor(
        [(validation_mean + live_mean) / 2.0], dtype=torch.float32, requires_grad=True
    )
    positive_logit = model((midpoint - mean) / std)[0, positive_index]
    positive_logit.backward()
    gradient = midpoint.grad[0].detach().numpy()
    rows = [
        {
            "feature": str(name),
            "raw_mean_shift": float(live_mean[index] - validation_mean[index]),
            "positive_logit_gradient": float(gradient[index]),
            "first_order_logit_shift": float(
                gradient[index] * (live_mean[index] - validation_mean[index])
            ),
        }
        for index, name in enumerate(names)
    ]
    return sorted(
        rows,
        key=lambda row: (-abs(float(row["first_order_logit_shift"])), row["feature"]),
    )[:20]


def _fresh_manifest(settings: Mapping[str, Any], parent: Path) -> dict[str, Any]:
    all_ids = [
        line.strip()
        for line in Path(str(settings["test_normal_manifest"])).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    exposed: dict[str, list[str]] = {task_id: [] for task_id in all_ids}
    full_bare = Path(str(settings["historical_full_bare_results"]))
    for task_id in all_ids:
        path = full_bare / f"{task_id}.json"
        if path.exists():
            exposed[task_id].append(
                f"historical_full_bare_baseline:{path}:{sha256_file(path)}"
            )
    for phase in (
        "gated_raw_first37/task_results",
        "gated_compiled_first37/task_results",
    ):
        for path in sorted((parent / phase).glob("*.json")):
            task_id = path.stem
            if task_id in exposed:
                exposed[task_id].append(f"exp028a_{phase}:{path}:{sha256_file(path)}")
    manifest = freeze_fresh_test_manifest(
        all_task_ids=all_ids,
        exposed={key: value for key, value in exposed.items() if value},
    )
    manifest.update(
        {
            "historical_full_bare_directory": str(full_bare),
            "historical_full_bare_task_count": sum(
                (full_bare / f"{task_id}.json").exists() for task_id in all_ids
            ),
            "exposure_definition": (
                "Any prior per-task test_normal evaluation is exposed; the historical "
                "full bare run covers the complete official 0.1.0 test_normal pool."
            ),
            "fresh_tasks_executed": False,
        }
    )
    manifest["exposed_task_manifest_sha256"] = _sha256_json(
        manifest["exposure_sources"]
    )
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return manifest


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7h2"]
    parent_settings = cfg.raw["stage_c_7hr"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028B requires global seed 25101")
    parent = Path(str(settings["parent_exp028a"]))
    gate_path = parent / "gate/memory_use_gate.pt"
    compiler_path = Path(
        _json(parent / "structured_compiler/checkpoint_selection.json")["selected"]["checkpoint"]
    )
    if sha256_file(gate_path) != str(settings["expected_gate_sha256"]):
        raise ValueError("Frozen EXP-028A gate hash differs")
    if sha256_file(compiler_path) != str(settings["expected_compiler_sha256"]):
        raise ValueError("Frozen EXP-028A compiler hash differs")
    gate_payload = torch.load(gate_path, map_location="cpu", weights_only=False)
    names = list(map(str, gate_payload["feature_names"]))
    schema_path = parent / "preflight/structured_feature_schema.json"
    schema = _json(schema_path)
    integrity = {
        "feature_order_match": names == list(map(str, schema["names"])),
        "feature_names_match": len(names) == len(set(names)),
        "feature_schema_hash_match": str(gate_payload["feature_schema_sha256"])
        == sha256_file(schema_path),
        "temperature_match": float(gate_payload["temperature"])
        == float(settings["gate_temperature"]),
        "threshold_match": float(gate_payload["activation_threshold"])
        == float(settings["gate_threshold"]),
        "harmful_threshold_match": float(gate_payload["maximum_harmful_probability"])
        == float(settings["gate_maximum_harmful_probability"]),
    }
    if not all(integrity.values()):
        raise RuntimeError(f"gate_inference_implementation_mismatch:{integrity}")
    model = MemoryUseGate(len(names), int(parent_settings["gate"]["hidden_dim"]))
    model.load_state_dict(gate_payload["model_state_dict"])
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    paired_path = parent / "paired_causal/paired_outcomes.json"
    paired = _json(paired_path)
    train_rows = [row for row in paired["rows"] if row["model_split"] == "model_train"]
    validation_rows = [
        row for row in paired["rows"] if row["model_split"] == "heldout_train_validation"
    ]
    live_rows, missing_live = _load_live_rows(parent)
    train_values = [row["feature_values"] for row in train_rows]
    validation_values = [row["feature_values"] for row in validation_rows]
    live_values = [row["feature_values"] for row in live_rows]
    mean = gate_payload["standardizer_mean"].to(torch.float32)
    std = gate_payload["standardizer_std"].to(torch.float32)
    validation_logits, validation_probability = _model_outputs(
        model, validation_values, mean, std, float(gate_payload["temperature"])
    )
    live_logits, live_probability = _model_outputs(
        model, live_values, mean, std, float(gate_payload["temperature"])
    )
    labels = list(map(str, gate_payload["labels"]))
    positions = {label: index for index, label in enumerate(labels)}
    maximum_probability_error = 0.0
    for index, row in enumerate(live_rows):
        for label, position in positions.items():
            maximum_probability_error = max(
                maximum_probability_error,
                abs(
                    float(live_probability[index, position])
                    - float(row["recorded_probabilities"][label])
                ),
            )
    integrity["recorded_live_probability_reproduction"] = maximum_probability_error <= 1.0e-5
    if not integrity["recorded_live_probability_reproduction"]:
        raise RuntimeError(
            "gate_inference_implementation_mismatch:recorded_probability_reproduction"
        )
    feature_report = feature_distribution_report(
        names=names,
        train_values=train_values,
        validation_values=validation_values,
        live_values=live_values,
        standardizer_mean=mean.tolist(),
        standardizer_std=std.tolist(),
    )
    domain = domain_classifier_audit(validation_values, live_values)
    diagnosis = classify_distribution_shift(
        domain_auc=float(domain["heldout_auc"]), feature_rows=feature_report["rows"]
    )
    thresholds = list(map(float, settings["diagnostic_thresholds"]))
    heldout_output = _output_summary(
        logits=validation_logits,
        probabilities=validation_probability,
        labels=labels,
        thresholds=thresholds,
        maximum_harmful=float(gate_payload["maximum_harmful_probability"]),
    )
    live_groups = _group_summaries(
        rows=live_rows,
        names=names,
        logits=live_logits,
        probabilities=live_probability,
        labels=labels,
        thresholds=thresholds,
        maximum_harmful=float(gate_payload["maximum_harmful_probability"]),
        early_max=int(settings["live_turn_buckets"]["early_max_step"]),
        middle_max=int(settings["live_turn_buckets"]["middle_max_step"]),
    )
    sensitivity = _gate_sensitivity(
        model=model,
        validation_values=np.asarray(validation_values),
        live_values=np.asarray(live_values),
        mean=mean,
        std=std,
        names=names,
        positive_index=positions["POSITIVE"],
    )
    unk = [
        row
        for row in feature_report["rows"]
        if "[UNK]" in str(row["feature"])
    ]
    shuffle = freeze_transition_shuffle(
        list(parent_settings["expected_selector_sha256"] for _ in [])
        or list(_json(parent / "preflight/initial_panel.json").get("unused", [])),
        {},
    ) if False else None
    runtime = {
        "format": "ungated_structured_first37_runtime_preflight_7h2_v1",
        "u1_projected_h100_hours": float(settings["runtime"]["u1_expected_hours"]),
        "u2_projected_h100_hours": float(settings["runtime"]["u2_expected_hours"]),
        "expected_total_h100_hours": float(settings["runtime"]["u1_expected_hours"])
        + float(settings["runtime"]["u2_expected_hours"]),
        "conservative_total_h100_hours": float(settings["runtime"]["u1_conservative_hours"])
        + float(settings["runtime"]["u2_conservative_hours"]),
        "review_threshold_h100_hours": float(settings["runtime"]["review_threshold_h100_hours"]),
        "expected_artifact_gib": float(settings["runtime"]["expected_artifact_gib"]),
    }
    runtime["automatic_launch_allowed"] = (
        runtime["expected_total_h100_hours"] <= runtime["review_threshold_h100_hours"]
    )
    if not runtime["automatic_launch_allowed"]:
        raise RuntimeError("EXP-028B projected H100 work exceeds review threshold")
    source_hash = hashlib.sha256(
        inspect.getsource(StructuredRuntime.decide).encode("utf-8")
    ).hexdigest()
    report = {
        "format": REPORT_FORMAT,
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": GLOBAL_SEED,
        "parent_run_uuid": str(parent_settings["run_uuid"]),
        "feature_order_count": len(names),
        "integrity": integrity,
        "maximum_recorded_probability_error": maximum_probability_error,
        "inference_source_sha256": source_hash,
        "gate_checkpoint_sha256": sha256_file(gate_path),
        "compiler_checkpoint_sha256": sha256_file(compiler_path),
        "feature_schema_sha256": sha256_file(schema_path),
        "paired_outcomes_sha256": sha256_file(paired_path),
        "heldout_train": heldout_output,
        "first37_live": live_groups,
        "first37_total_turn_count": len(live_rows) + len(missing_live),
        "first37_feature_row_count": len(live_rows),
        "first37_missing_feature_row_count": len(missing_live),
        "first37_missing_feature_rows": missing_live,
        "feature_distributions": feature_report,
        "domain_classifier": domain,
        "gate_positive_logit_sensitivity": sensitivity,
        "categorical_unk_shift": unk,
        "diagnosis": diagnosis,
        "first37_outcomes_used": False,
        "implementation_mismatch": False,
    }
    fresh = _fresh_manifest(settings, parent)
    root = args.artifact_dir
    hashes = {
        "parent_gate": sha256_file(gate_path),
        "parent_compiler": sha256_file(compiler_path),
        "parent_features": sha256_file(paired_path),
        "config": sha256_file(args.config),
    }
    with AttemptLedger(
        root,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="gate_distribution_audit_and_preflight",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=hashes["config"],
        data_manifest_hashes=hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint="exp028a_immutable_inputs_validated",
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        atomic_write_json(root / "gate_distribution/gate_distribution_audit.json", report)
        atomic_write_json(root / "preflight/runtime_preflight.json", runtime)
        atomic_write_json(root / "preflight/transition_shuffle_policy.json", {
            "format": "ungated_live_transition_shuffle_policy_7h2_v1",
            "global_seed": GLOBAL_SEED,
            "selection_uses_outcomes": False,
            "different_transition_required": True,
            "different_signature_class_where_possible": True,
            "mapping_frozen_before_generation": True,
        })
        atomic_write_json(root / "fresh_test/fresh_test37_post_exp028b.json", fresh)
        atomic_write_text(
            root / "gate_distribution/report.md",
            "\n".join(
                [
                    "# EXP-028B gate distribution audit",
                    "",
                    f"- heldout-train rows: `{heldout_output['row_count']}`",
                    f"- first37 live feature rows: `{len(live_rows)}/873`",
                    f"- frozen inference maximum probability error: `{maximum_probability_error:.8g}`",
                    f"- maximum live P(POSITIVE): `{live_groups['all']['positive_probability']['max']:.6f}`",
                    f"- live activations at 0.60: `{live_groups['all']['activation_counts']['0.60']}`",
                    f"- domain-classifier AUC: `{domain['heldout_auc']:.6f}`",
                    f"- diagnosis: `{diagnosis['classification']}`",
                    "- no first37 outcome was used",
                    "",
                ]
            ),
        )
        attempt.progress(
            status="gate_distribution_audit_complete",
            completed_live_rows=len(live_rows),
            total_live_turns=len(live_rows) + len(missing_live),
            latest_validated_checkpoint=str(root / "gate_distribution/gate_distribution_audit.json"),
        )
    print(json.dumps({"report": report, "runtime": runtime, "fresh": fresh}, sort_keys=True))


if __name__ == "__main__":
    main()
