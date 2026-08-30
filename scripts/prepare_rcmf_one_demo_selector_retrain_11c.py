"""Prepare and train the locked EXP-034B one-demo selector reconstruction."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.prompt import (
    FULL_DEMO_FIRST_ONLY_PROFILE,
    appworld_renderer_metadata,
    full_demo_sections,
    get_system_prompt,
)
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    POOLING_RULES,
    STATE_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    query_state_text_and_char_spans,
    readout_payload_hash,
    tokenize_and_validate_char_spans,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from rcmf.training.rcmf_joint_full_bank_9a import (
    FrozenSelectorDecomposition,
    tensor_sha256,
)
from rcmf.training.signature_balanced_field_7c import (
    calibrated_ensemble,
    deterministic_seed,
    evaluate_score_matrix,
    score_field_selector,
    state_class_balanced_weights,
    train_field_selector,
    validate_class_balance,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger, utc_now
from rcmf.training.transition_memory_6a import example_task_id, state_example_id
from rcmf.utils.serialization import (
    atomic_write_json,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.run_signature_balanced_field_7c import (
    _class_balanced_calibration_values,
    _class_prior_matrix,
    _selector,
    _space_labels,
    _split_permutation,
    _subset_representations,
)


GLOBAL_SEED = 25101
RUN_UUID = "rcmf_one_demo_selector_retrain_11c_20260830_001"
SELECTOR_CACHE_FORMAT = "one_demo_full_selector_state_multiview_11c_v1"
DOWNSTREAM_CACHE_FORMAT = "one_demo_downstream_state_multiview_11c_v1"


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
            "configs/benchmark/stage_c_rcmf_one_demo_selector_retrain_11c.yaml"
        ),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=(
            "dependency",
            "selector-state-cache",
            "selector-recipe",
            "selector-train",
            "selector-diagnostics",
            "selection-compare",
        ),
        required=True,
    )
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp034b_prepare")
    return parser.parse_args()


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row["attempt_id"])
        for row in read_jsonl(path)
        if row.get("attempt_id")
    }


def _paths(cfg: Any, artifact_dir: Path) -> dict[str, Path]:
    settings = cfg.raw["stage_c_11c"]
    old_c = Path(str(settings["parent_exp025c"]))
    old_a = Path(str(settings["parent_exp034a"]))
    s9a = cfg.raw["stage_c_9a"]
    parent_b = Path(str(s9a["parent_exp025b"]))
    corpus = Path(str(s9a["reconciled_corpus_dir"]))
    selector_cache_root = artifact_dir / "representation_cache/multiview"
    return {
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signatures": parent_b
        / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "parent_split": parent_b
        / "clean_procedural_audit/clean_parent_split_manifest.json",
        "historical_labels": old_c / "clean_full_procedural_labels.jsonl",
        "historical_candidates": old_c / "candidate_space_manifest.json",
        "historical_state_cache": old_c
        / "representation_cache/multiview/state_multiview.pt",
        "historical_transition_cache": old_c
        / "representation_cache/multiview/transition_multiview.pt",
        "historical_cache_summary": old_c
        / "representation_cache/multiview/clean_multiview_cache_summary.json",
        "historical_selector_summary": old_c / "selector/selector_summary.json",
        "historical_cv": old_c / "selector/a_only_cv/a_only_cv_report.json",
        "historical_ensemble": old_c / "selector/ensemble_scores.pt",
        "historical_selector_root": old_c / "selector",
        "exp034a_state_cache": old_a / "prompt_dependent/state_multiview.pt",
        "exp034a_state_summary": old_a
        / "prompt_dependent/one_demo_state_cache_summary.json",
        "exp034a_fixed_states": old_a / "prompt_dependent/fixed_state_manifest.json",
        "exp034a_selections": old_a / "preflight/frozen_train_selections.jsonl",
        "dependency": artifact_dir / "dependency_manifest.json",
        "selector_state_manifest": artifact_dir / "selector_state_manifest.json",
        "selector_state_root": artifact_dir / "selector_state_rows",
        "selector_state_cache": selector_cache_root / "state_multiview.pt",
        "transition_cache": selector_cache_root / "transition_multiview.pt",
        "cache_summary": selector_cache_root / "clean_multiview_cache_summary.json",
        "downstream_state_cache": artifact_dir / "prompt_dependent/state_multiview.pt",
        "recipe": artifact_dir / "selector/locked_recipe.json",
        "selector_root": artifact_dir / "selector",
        "selector_training": artifact_dir / "selector/selector_training.json",
        "selector_ensemble": artifact_dir / "selector/ensemble_scores.pt",
        "selector_diagnostics": artifact_dir / "selector/selector_diagnostics.json",
        "selector_factors": artifact_dir / "selector/selector_factors.pt",
        "selector_factor_summary": artifact_dir
        / "selector/selector_factor_summary.json",
        "new_selections": artifact_dir / "preflight/frozen_train_selections.jsonl",
        "selection_manifest": artifact_dir / "preflight/selection_manifest.json",
        "selection_compare": artifact_dir / "preflight/selection_comparison.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"EXP-034B input missing: {missing}")


def _prompt_identity(settings: Mapping[str, Any]) -> dict[str, Any]:
    metadata = appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)
    expected = settings["expected"]
    retained_demo_sha256 = sha256_text(
        str(
            full_demo_sections(get_system_prompt("full_demo"))[
                "demo_1_with_instruction_prefix"
            ]
        )
    )
    return {
        "prompt_profile": str(metadata["prompt_profile"]),
        "retained_demo_sha256": retained_demo_sha256,
        "initial_messages_sha256": str(metadata["initial_messages_sha256"]),
        "initial_message_count": int(metadata["initial_message_count"]),
        "passed": (
            str(metadata["prompt_profile"]) == FULL_DEMO_FIRST_ONLY_PROFILE
            and retained_demo_sha256
            == str(expected["retained_demo_sha256"])
            and str(metadata["initial_messages_sha256"])
            == str(expected["initial_prompt_asset_sha256"])
        ),
    }


def _member_seeds(member_count: int) -> list[int]:
    return [
        deterministic_seed(GLOBAL_SEED, "selector-member", index)
        for index in range(int(member_count))
    ]


def _candidate(settings: Mapping[str, Any]) -> dict[str, Any]:
    selector = settings["selector"]
    return {
        "name": str(selector["candidate_name"]),
        "learning_rate": float(selector["learning_rate"]),
        "epochs": int(selector["epochs"]),
        "temperature": float(selector["temperature"]),
        "listwise_weight": float(selector["listwise_weight"]),
        "pairwise_weight": float(selector["pairwise_weight"]),
        "hard_negative_weight": float(selector["hard_negative_weight"]),
        "exact_api_weight": float(selector["exact_api_weight"]),
        "stage_weight": float(selector["stage_weight"]),
    }


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    if sha256_file(source) != sha256_file(temporary):
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Copied artifact hash differs: {source}")
    os.replace(temporary, destination)


def _dependency(
    settings: Mapping[str, Any], paths: Mapping[str, Path]
) -> dict[str, Any]:
    names = (
        "decisions",
        "transitions",
        "signatures",
        "parent_split",
        "historical_labels",
        "historical_candidates",
        "historical_state_cache",
        "historical_transition_cache",
        "historical_cache_summary",
        "historical_selector_summary",
        "historical_cv",
        "historical_ensemble",
        "exp034a_state_cache",
        "exp034a_state_summary",
        "exp034a_fixed_states",
        "exp034a_selections",
    )
    _require(paths, names)
    expected = settings["expected"]
    historical_state = torch.load(
        paths["historical_state_cache"], map_location="cpu", weights_only=False
    )
    historical_transition = torch.load(
        paths["historical_transition_cache"], map_location="cpu", weights_only=False
    )
    exp034a_state = torch.load(
        paths["exp034a_state_cache"], map_location="cpu", weights_only=False
    )
    ensemble_hash = sha256_file(paths["historical_ensemble"])
    labels = _rows(paths["historical_labels"])
    label_keys = sorted({key for row in labels for key in row})
    forbidden_label_keys = [
        key
        for key in label_keys
        if any(
            token in key.lower()
            for token in ("selector_score", "hidden_state", "representation")
        )
    ]
    ordered_ids = [str(value) for value in historical_state["ordered_ids"]]
    split_counts = Counter(str(row["split"]) for row in historical_state["rows"])
    overlap = set(ordered_ids) & {
        str(value) for value in exp034a_state["ordered_ids"]
    }
    checks = {
        "prompt_identity": bool(_prompt_identity(settings)["passed"]),
        "historical_selector_identity": ensemble_hash
        == str(expected["historical_selector_sha256"]),
        "selector_state_count": len(ordered_ids)
        == int(expected["selector_state_count"]),
        "selector_train_count": split_counts["train"]
        == int(expected["selector_train_state_count"]),
        "selector_validation_count": split_counts["validation"]
        == int(expected["selector_validation_state_count"]),
        "transition_count": len(historical_transition["ordered_ids"])
        == int(expected["transition_count"]),
        "exp034a_overlap_count": len(overlap)
        == int(expected["downstream_train_state_count"])
        + int(expected["downstream_heldout_state_count"]),
        "prompt_independent_labels": not forbidden_label_keys,
        "transition_renderer_prompt_independent": all(
            token not in Path(
                "rcmf/training/multiview_representations_6c.py"
            ).read_text(encoding="utf-8").split(
                "def transition_text_and_char_spans", 1
            )[1].split("def ", 1)[0]
            for token in (
                "full_demo",
                "full_demo_first_only",
                "AGENT_SYSTEM_PROMPT_TEMPLATE_AW",
            )
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"EXP-034B dependency identity failed: {checks}")
    payload = {
        "format": "rcmf_one_demo_selector_dependency_11c_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "only_method_change": (
            "fresh selector parameters from one-demo state representations"
        ),
        "historical_selector_sha256": ensemble_hash,
        "historical_selector_used_for_initialization": False,
        "historical_state_universe": {
            "count": len(ordered_ids),
            "split_counts": dict(sorted(split_counts.items())),
            "ordered_state_ids_sha256": canonical_sha256(ordered_ids),
        },
        "exp034a_one_demo_overlap_count": len(overlap),
        "expected_new_state_count": len(ordered_ids) - len(overlap),
        "transition_cache": {
            "path": str(paths["historical_transition_cache"]),
            "sha256": sha256_file(paths["historical_transition_cache"]),
            "count": len(historical_transition["ordered_ids"]),
            "prompt_independent": True,
        },
        "selector_supervision": {
            "path": str(paths["historical_labels"]),
            "sha256": sha256_file(paths["historical_labels"]),
            "row_count": len(labels),
            "keys": label_keys,
            "forbidden_keys": forbidden_label_keys,
            "prompt_independent": True,
        },
        "prompt_identity": _prompt_identity(settings),
        "source_hashes": {name: sha256_file(paths[name]) for name in names},
        "checks": checks,
        "dev_used": False,
        "test_normal_used": False,
        "first37_used": False,
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["dependency"], payload)
    return payload


def _recipe(settings: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    _require(paths, ("dependency", "historical_selector_summary", "historical_cv", "historical_ensemble"))
    selector_summary = _json(paths["historical_selector_summary"])
    cv = _json(paths["historical_cv"])
    locked = _candidate(settings)
    historical = dict(cv["selected_candidate"])
    if locked != historical:
        raise RuntimeError(
            f"Locked selector recipe differs from deployed winner: {locked} != {historical}"
        )
    ensemble = torch.load(
        paths["historical_ensemble"], map_location="cpu", weights_only=False
    )
    if len(ensemble["seed_checkpoints"]) != int(settings["selector"]["member_count"]):
        raise ValueError("Historical deployed ensemble member count differs")
    payload = {
        "format": "locked_one_demo_selector_recipe_11c_v1",
        "locked_before_new_training": True,
        "historical_candidate_identity": str(locked["name"]),
        "candidate": locked,
        "architecture": {
            key: int(settings["selector"][key])
            for key in (
                "state_views",
                "transition_views",
                "input_dim",
                "projection_dim",
                "interaction_rank",
                "batch_states",
                "maximum_pair_samples_per_state",
                "maximum_hard_samples_per_state",
            )
        },
        "weight_decay": float(settings["selector"]["weight_decay"]),
        "checkpoint_interval_epochs": int(
            settings["selector"]["checkpoint_interval_epochs"]
        ),
        "member_count": int(settings["selector"]["member_count"]),
        "member_seeds": _member_seeds(settings["selector"]["member_count"]),
        "member_seed_rule": "deterministic_seed(25101, selector-member, member_index)",
        "historical_member_seeds_used": False,
        "historical_weights_used": False,
        "historical_ensemble_sha256": sha256_file(paths["historical_ensemble"]),
        "historical_cv_selection_not_repeated": True,
        "new_candidate_comparison_performed": False,
        "selector_summary_selected_candidate": selector_summary["selected_candidate"],
    }
    payload["recipe_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["recipe"], payload)
    return payload


def _selector_state_cache(
    cfg: Any,
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    _require(
        paths,
        (
            "dependency",
            "decisions",
            "historical_state_cache",
            "historical_transition_cache",
            "exp034a_state_cache",
        ),
    )
    dependency = _json(paths["dependency"])
    historical = torch.load(
        paths["historical_state_cache"], map_location="cpu", weights_only=False
    )
    one_demo = torch.load(
        paths["exp034a_state_cache"], map_location="cpu", weights_only=False
    )
    for name, payload in (("historical", historical), ("one_demo", one_demo)):
        for layer, tensor in payload["representations"].items():
            actual = tensor_state_sha256({"representations": tensor})
            if actual != str(payload["tensor_sha256"][layer]):
                raise ValueError(f"{name} state-cache tensor hash differs: {layer}")
    examples = load_decision_examples(paths["decisions"])
    by_id = {
        state_example_id(index, example): (index, example)
        for index, example in enumerate(examples)
    }
    ordered_ids = [str(value) for value in historical["ordered_ids"]]
    if set(ordered_ids) != set(by_id):
        raise ValueError("Historical selector cache is not the complete clean universe")
    if len(ordered_ids) != int(settings["expected"]["selector_state_count"]):
        raise ValueError("Historical selector state count differs")
    historical_rows = {
        str(row["state_example_id"]): dict(row) for row in historical["rows"]
    }
    old_positions = {
        str(value): index for index, value in enumerate(one_demo["ordered_ids"])
    }
    old_rows = {
        str(row["state_example_id"]): dict(row) for row in one_demo["rows"]
    }
    missing_ids = [value for value in ordered_ids if value not in old_positions]
    expected_missing = int(settings["expected"]["selector_state_count"]) - (
        int(settings["expected"]["downstream_train_state_count"])
        + int(settings["expected"]["downstream_heldout_state_count"])
    )
    if len(missing_ids) != expected_missing:
        raise ValueError(
            f"One-demo state reuse accounting differs: {len(missing_ids)} "
            f"!= {expected_missing}"
        )

    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("State-cache Qwen is not frozen")
    renderer = str(
        appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)["renderer_version"]
    )
    lineage = str(settings["expected"]["structural_lineage_sha256"])
    model_name = str(backend.model_name)
    paths["selector_state_root"].mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    reused = computed = resumed = 0
    rows: list[dict[str, Any]] = []
    matrices: dict[str, list[torch.Tensor]] = {
        layer: [] for layer in LAYER_CANDIDATES
    }

    for ordinal, state_id in enumerate(ordered_ids, start=1):
        global_index, example = by_id[state_id]
        rendered, char_spans, metadata = query_state_text_and_char_spans(
            backend.tokenizer, example, FULL_DEMO_FIRST_ONLY_PROFILE
        )
        input_ids, attention_mask, spans = tokenize_and_validate_char_spans(
            backend.tokenizer, rendered, char_spans
        )
        identity = {
            "state_example_id": state_id,
            "prompt_sha256": sha256_text(rendered),
            "renderer_version": renderer,
            "model_name": model_name,
            "corpus_lineage_sha256": lineage,
        }
        if state_id in old_positions:
            source_row = old_rows[state_id]
            for key, value in identity.items():
                if str(source_row.get(key)) != str(value):
                    raise ValueError(
                        f"EXP-034A reusable state identity differs: {state_id} {key}"
                    )
            if any(
                bool(source_row.get(key))
                for key in (
                    "target_action_accessed",
                    "future_observation_accessed",
                    "truncated",
                    "dev_task",
                )
            ):
                raise ValueError(
                    f"EXP-034A reusable state is scientifically invalid: {state_id}"
                )
            for layer in LAYER_CANDIDATES:
                matrices[layer].append(
                    one_demo["representations"][layer][old_positions[state_id]]
                    .to(torch.float32)
                    .clone()
                )
            provenance = "reused_exp034a_one_demo_exact"
            reused += 1
        else:
            row_path = (
                paths["selector_state_root"] / f"{sha256_text(state_id)}.pt"
            )
            if row_path.exists():
                payload = torch.load(
                    row_path, map_location="cpu", weights_only=False
                )
                if any(
                    str(payload.get(key)) != str(value)
                    for key, value in identity.items()
                ):
                    raise ValueError(
                        f"Resumed selector-state identity differs: {state_id}"
                    )
                if (
                    readout_payload_hash(payload["readouts"])
                    != payload["readout_sha256"]
                ):
                    raise ValueError(
                        f"Resumed selector-state tensor differs: {state_id}"
                    )
                provenance = "resumed_one_demo_selector_row"
                resumed += 1
            else:
                readouts = frozen_qwen_span_readouts(
                    model=backend.model,
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    span_rows=spans,
                    device=backend.device,
                )
                payload = {
                    "format": "one_demo_selector_state_row_11c_v1",
                    **identity,
                    "readouts": readouts,
                    "readout_sha256": readout_payload_hash(readouts),
                    "span_rows": spans,
                    "source_metadata": metadata,
                    "token_count": int(input_ids.shape[1]),
                    "target_action_accessed": False,
                    "future_observation_accessed": False,
                    "truncated": False,
                    "dev_task": False,
                    "created_at_utc": utc_now(),
                }
                atomic_torch_save(payload, row_path)
                provenance = "computed_one_demo_selector_row"
                computed += 1
            for layer in LAYER_CANDIDATES:
                matrices[layer].append(
                    flatten_multiview_readouts(
                        [payload], layer=layer, view_names=STATE_VIEW_NAMES
                    )[0].to(torch.float32)
                )

        rows.append(
            {
                "format": "one_demo_selector_state_metadata_11c_v1",
                **identity,
                "example_index": global_index,
                "task_id": example_task_id(example),
                "split": str(historical_rows[state_id]["split"]),
                "step_id": int(example.step_id),
                "token_count": int(input_ids.shape[1]),
                "provenance": provenance,
                "target_action_accessed": False,
                "future_observation_accessed": False,
                "truncated": False,
                "dev_task": False,
            }
        )
        attempt.progress(
            status="one_demo_selector_state_cache",
            completed=ordinal,
            total=len(ordered_ids),
            reused=reused,
            computed=computed,
            resumed=resumed,
        )
        if ordinal % 10 == 0 or ordinal == len(ordered_ids):
            print(
                f"one-demo selector states {ordinal}/{len(ordered_ids)} "
                f"reused={reused} computed={computed} resumed={resumed}",
                flush=True,
            )

    stacked = {layer: torch.stack(values) for layer, values in matrices.items()}
    aggregate = {
        "format": SELECTOR_CACHE_FORMAT,
        "ordered_ids": ordered_ids,
        "representations": stacked,
        "rows": rows,
        "model_name": model_name,
        "renderer_version": renderer,
        "prompt_profile": FULL_DEMO_FIRST_ONLY_PROFILE,
        "initial_prompt_asset_sha256": str(
            settings["expected"]["initial_prompt_asset_sha256"]
        ),
        "view_names": list(STATE_VIEW_NAMES),
        "pooling_rules": list(POOLING_RULES),
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": value})
            for layer, value in stacked.items()
        },
        "corpus_lineage_sha256": lineage,
        "target_action_accessed": False,
        "future_observation_accessed": False,
        "dev_accessed": False,
        "three_demo_state_representation_count": 0,
        "truncated": False,
        "created_at_utc": utc_now(),
    }
    atomic_torch_save(aggregate, paths["selector_state_cache"])
    _atomic_copy(paths["historical_transition_cache"], paths["transition_cache"])

    downstream_ids = [str(value) for value in one_demo["ordered_ids"]]
    positions = {value: index for index, value in enumerate(ordered_ids)}
    downstream_rows = []
    for source_row in one_demo["rows"]:
        state_id = str(source_row["state_example_id"])
        selector_row = dict(rows[positions[state_id]])
        selector_row["format"] = "one_demo_downstream_state_metadata_11c_v1"
        selector_row["model_split"] = str(source_row["model_split"])
        downstream_rows.append(selector_row)
    downstream_representations = {
        layer: torch.stack(
            [stacked[layer][positions[value]] for value in downstream_ids]
        )
        for layer in LAYER_CANDIDATES
    }
    downstream = {
        **{
            key: value
            for key, value in aggregate.items()
            if key
            not in (
                "ordered_ids",
                "representations",
                "rows",
                "tensor_sha256",
                "format",
            )
        },
        "format": DOWNSTREAM_CACHE_FORMAT,
        "ordered_ids": downstream_ids,
        "representations": downstream_representations,
        "rows": downstream_rows,
        "tensor_sha256": {
            layer: tensor_state_sha256({"representations": value})
            for layer, value in downstream_representations.items()
        },
    }
    atomic_torch_save(downstream, paths["downstream_state_cache"])
    elapsed = time.perf_counter() - started
    summary = {
        "format": "one_demo_selector_state_cache_summary_11c_v1",
        "run_uuid": RUN_UUID,
        "selector_state_count": len(rows),
        "selector_split_counts": dict(
            sorted(Counter(row["split"] for row in rows).items())
        ),
        "reused_exp034a_count": reused,
        "computed_new_count": computed,
        "resumed_new_count": resumed,
        "expected_new_count": expected_missing,
        "downstream_state_count": len(downstream_rows),
        "downstream_model_split_counts": dict(
            sorted(
                Counter(row["model_split"] for row in downstream_rows).items()
            )
        ),
        "selector_state_cache": str(paths["selector_state_cache"]),
        "selector_state_cache_sha256": sha256_file(paths["selector_state_cache"]),
        "downstream_state_cache": str(paths["downstream_state_cache"]),
        "downstream_state_cache_sha256": sha256_file(
            paths["downstream_state_cache"]
        ),
        "transition_cache": str(paths["transition_cache"]),
        "transition_cache_sha256": sha256_file(paths["transition_cache"]),
        "transition_cache_exact_copy": sha256_file(paths["transition_cache"])
        == dependency["transition_cache"]["sha256"],
        "prompt_identity": _prompt_identity(settings),
        "no_target_action": all(not row["target_action_accessed"] for row in rows),
        "no_future_observation": all(
            not row["future_observation_accessed"] for row in rows
        ),
        "no_dev": all(not row["dev_task"] for row in rows),
        "no_three_demo_state_representations": True,
        "no_truncation": all(not row["truncated"] for row in rows),
        "elapsed_seconds": elapsed,
        "h100_hours": elapsed / 3600.0,
    }
    summary["passed"] = (
        len(rows) == int(settings["expected"]["selector_state_count"])
        and reused + computed + resumed == len(rows)
        and reused
        == int(settings["expected"]["downstream_train_state_count"])
        + int(settings["expected"]["downstream_heldout_state_count"])
        and computed + resumed == expected_missing
        and summary["transition_cache_exact_copy"]
        and summary["no_target_action"]
        and summary["no_future_observation"]
        and summary["no_dev"]
        and summary["no_truncation"]
    )
    if not summary["passed"]:
        raise RuntimeError(f"One-demo selector state-cache gate failed: {summary}")
    summary["summary_sha256"] = canonical_sha256(summary)
    atomic_write_json(paths["cache_summary"], summary)
    atomic_write_json(paths["selector_state_manifest"], summary)
    return summary


def _calibrated_scores(
    *,
    models: Sequence[torch.nn.Module],
    state_values: torch.Tensor,
    transition_values: torch.Tensor,
    calibration: Sequence[Mapping[str, float]],
    batch_states: int,
    device: torch.device,
) -> torch.Tensor:
    values = []
    for model, row in zip(models, calibration, strict=True):
        score = score_field_selector(
            model=model,
            state_representations=state_values,
            transition_representations=transition_values,
            batch_states=batch_states,
            device=device,
        )
        values.append(
            (score - float(row["train_mean"])) / float(row["train_std"])
        )
    return torch.stack(values, dim=0).mean(dim=0)


def _selector_train(
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    _require(
        paths,
        (
            "dependency",
            "recipe",
            "selector_state_cache",
            "transition_cache",
            "historical_labels",
        ),
    )
    recipe = _json(paths["recipe"])
    state_cache = torch.load(
        paths["selector_state_cache"], map_location="cpu", weights_only=False
    )
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    labels = _rows(paths["historical_labels"])
    labels_a = [row for row in labels if str(row["cell"]) == "A"]
    weights = state_class_balanced_weights(labels_a)
    balance = validate_class_balance(labels_a, weights)
    if not balance["passed"]:
        raise RuntimeError(f"Selector class balance failed: {balance}")
    layer = str(settings["selector"]["layer"])
    ordered_state_ids = [str(value) for value in state_cache["ordered_ids"]]
    ordered_transition_ids = [
        str(value) for value in transition_cache["ordered_ids"]
    ]
    train_state_ids = sorted(
        {str(row["state_example_id"]) for row in labels_a}
    )
    train_transition_ids = sorted(
        {str(row["transition_id"]) for row in labels_a}
    )
    state_values = state_cache["representations"][layer].to(torch.float32)
    transition_values = transition_cache["representations"][layer].to(
        torch.float32
    )
    train_state_values = _subset_representations(
        state_cache, train_state_ids, layer
    )
    train_transition_values = _subset_representations(
        transition_cache, train_transition_ids, layer
    )
    source_hashes = {
        "dependency": sha256_file(paths["dependency"]),
        "recipe": sha256_file(paths["recipe"]),
        "state_cache": sha256_file(paths["selector_state_cache"]),
        "transition_cache": sha256_file(paths["transition_cache"]),
        "supervision": sha256_file(paths["historical_labels"]),
    }
    state_positions = {
        value: index for index, value in enumerate(ordered_state_ids)
    }
    transition_positions = {
        value: index for index, value in enumerate(ordered_transition_ids)
    }
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    candidate = dict(recipe["candidate"])
    member_seeds = [int(value) for value in recipe["member_seeds"]]
    models = []
    score_matrices = []
    train_calibration_values = []
    member_reports = []
    started = time.perf_counter()
    for member_index, member_seed in enumerate(member_seeds):
        model = _selector(settings["selector"], member_seed)
        fresh_initial_sha = tensor_state_sha256(
            {key: value.detach().cpu() for key, value in model.state_dict().items()}
        )
        checkpoint = (
            paths["selector_root"]
            / f"seed_{member_index}"
            / "field_selector.pt"
        )
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        resume = None
        if checkpoint.exists():
            candidate_resume = torch.load(
                checkpoint, map_location="cpu", weights_only=False
            )
            if dict(candidate_resume.get("source_hashes", {})) != source_hashes:
                raise ValueError(
                    f"Fresh-selector resume source hashes differ: {checkpoint}"
                )
            if int(candidate_resume.get("member_seed", -1)) != member_seed:
                raise ValueError(
                    f"Fresh-selector resume member seed differs: {checkpoint}"
                )
            if (
                str(candidate_resume.get("fresh_initial_parameter_sha256"))
                != fresh_initial_sha
            ):
                raise ValueError(
                    f"Fresh-selector initialization identity differs: {checkpoint}"
                )
            resume = candidate_resume

        def save(
            payload: Mapping[str, Any],
            path: Path = checkpoint,
            index: int = member_index,
            seed: int = member_seed,
            initial_sha: str = fresh_initial_sha,
        ) -> None:
            atomic_torch_save(
                {
                    **dict(payload),
                    "source_hashes": source_hashes,
                    "global_seed": GLOBAL_SEED,
                    "member_index": index,
                    "member_seed": seed,
                    "fresh_initial_parameter_sha256": initial_sha,
                    "historical_weights_loaded": False,
                    "dev_used": False,
                },
                path,
            )

        training = train_field_selector(
            model=model,
            rows=labels_a,
            state_representations=train_state_values,
            transition_representations=train_transition_values,
            ordered_state_ids=train_state_ids,
            ordered_transition_ids=train_transition_ids,
            candidate=candidate,
            batch_states=int(settings["selector"]["batch_states"]),
            maximum_pair_samples_per_state=int(
                settings["selector"]["maximum_pair_samples_per_state"]
            ),
            maximum_hard_samples_per_state=int(
                settings["selector"]["maximum_hard_samples_per_state"]
            ),
            weight_decay=float(settings["selector"]["weight_decay"]),
            seed=member_seed,
            device=device,
            resume=resume,
            checkpoint_callback=save,
            checkpoint_interval_epochs=int(
                settings["selector"]["checkpoint_interval_epochs"]
            ),
        )
        all_scores = score_field_selector(
            model=model,
            state_representations=state_values,
            transition_representations=transition_values,
            batch_states=int(settings["selector"]["batch_states"]),
            device=device,
        )
        if not bool(torch.isfinite(all_scores).all()):
            raise RuntimeError(f"Non-finite selector scores: member {member_index}")
        score_path = (
            paths["selector_root"]
            / f"seed_{member_index}"
            / "all_transition_scores.pt"
        )
        atomic_torch_save(
            {
                "format": "one_demo_selector_member_scores_11c_v1",
                "ordered_state_ids": ordered_state_ids,
                "ordered_transition_ids": ordered_transition_ids,
                "scores": all_scores,
                "scores_sha256": tensor_state_sha256({"scores": all_scores}),
                "checkpoint_sha256": sha256_file(checkpoint),
                "source_hashes": source_hashes,
            },
            score_path,
        )
        legal_train_values = _class_balanced_calibration_values(
            rows=labels_a,
            scores=all_scores,
            state_positions=state_positions,
            transition_positions=transition_positions,
        )
        models.append(model.cpu())
        score_matrices.append(all_scores)
        train_calibration_values.append(legal_train_values)
        member_reports.append(
            {
                "member_index": member_index,
                "member_seed": member_seed,
                "fresh_initial_parameter_sha256": fresh_initial_sha,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint),
                "score_path": str(score_path),
                "score_sha256": sha256_file(score_path),
                "parameter_sha256": tensor_state_sha256(
                    {
                        key: value.detach().cpu()
                        for key, value in model.state_dict().items()
                    }
                ),
                "training": {
                    key: value
                    for key, value in training.items()
                    if not key.endswith("state_dict")
                },
                "historical_weights_loaded": False,
            }
        )
        attempt.progress(
            status="fresh_one_demo_selector_training",
            completed_member_count=len(member_reports),
            total_member_count=len(member_seeds),
            latest_validated_checkpoint=str(checkpoint),
        )

    ensemble_scores, calibration = calibrated_ensemble(
        train_calibration_values, score_matrices
    )
    if not bool(torch.isfinite(ensemble_scores).all()):
        raise RuntimeError("Fresh one-demo selector ensemble scores are non-finite")
    ensemble_payload = {
        "format": "calibrated_three_member_one_demo_field_ensemble_11c_v1",
        "ordered_state_ids": ordered_state_ids,
        "ordered_transition_ids": ordered_transition_ids,
        "scores": ensemble_scores,
        "scores_sha256": tensor_state_sha256({"scores": ensemble_scores}),
        "train_calibration": calibration,
        "seed_checkpoints": member_reports,
        "source_hashes": source_hashes,
        "global_seed": GLOBAL_SEED,
        "member_seed_rule": recipe["member_seed_rule"],
        "historical_weights_loaded": False,
        "dev_used": False,
    }
    atomic_torch_save(ensemble_payload, paths["selector_ensemble"])
    elapsed = time.perf_counter() - started
    parameter_count = sum(
        parameter.numel() for parameter in models[0].parameters()
    )
    summary = {
        "format": "fresh_one_demo_selector_training_11c_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "device": str(device),
        "candidate": candidate,
        "architecture": recipe["architecture"],
        "member_count": len(member_reports),
        "member_seeds": member_seeds,
        "member_reports": member_reports,
        "parameter_count_per_member": parameter_count,
        "parameter_count_total": parameter_count * len(member_reports),
        "class_balance": balance,
        "training_pair_count": len(labels_a),
        "training_state_count": len(train_state_ids),
        "training_transition_count": len(train_transition_ids),
        "full_score_shape": list(ensemble_scores.shape),
        "ensemble": {
            "path": str(paths["selector_ensemble"]),
            "sha256": sha256_file(paths["selector_ensemble"]),
            "scores_sha256": ensemble_payload["scores_sha256"],
            "train_calibration": calibration,
        },
        "source_hashes": source_hashes,
        "historical_weights_loaded": False,
        "old_selector_score_loaded": False,
        "old_selector_calibration_loaded": False,
        "dev_used": False,
        "elapsed_seconds": elapsed,
        "h100_hours": elapsed / 3600.0,
        "completed_at_utc": utc_now(),
    }
    summary["summary_sha256"] = canonical_sha256(summary)
    atomic_write_json(paths["selector_training"], summary)
    return summary


def _selector_diagnostics(
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    _require(
        paths,
        (
            "selector_training",
            "selector_ensemble",
            "selector_state_cache",
            "transition_cache",
            "historical_labels",
            "parent_split",
        ),
    )
    training = _json(paths["selector_training"])
    state_cache = torch.load(
        paths["selector_state_cache"], map_location="cpu", weights_only=False
    )
    transition_cache = torch.load(
        paths["transition_cache"], map_location="cpu", weights_only=False
    )
    ensemble = torch.load(
        paths["selector_ensemble"], map_location="cpu", weights_only=False
    )
    labels = _rows(paths["historical_labels"])
    checkpoints = []
    models = []
    for report in training["member_reports"]:
        checkpoint = torch.load(
            Path(str(report["checkpoint"])),
            map_location="cpu",
            weights_only=False,
        )
        model = _selector(settings["selector"], int(report["member_seed"]))
        model.load_state_dict(checkpoint["model_state_dict"])
        model.eval()
        checkpoints.append(checkpoint)
        models.append(model)
    layer = str(settings["selector"]["layer"])
    state_values = state_cache["representations"][layer].to(torch.float32)
    transition_values = transition_cache["representations"][layer].to(
        torch.float32
    )
    ordered_state_ids = [str(value) for value in state_cache["ordered_ids"]]
    ordered_transition_ids = [
        str(value) for value in transition_cache["ordered_ids"]
    ]
    correct = ensemble["scores"].to(torch.float32)
    if not bool(torch.isfinite(correct).all()):
        raise RuntimeError("Fresh selector ensemble scores are non-finite")
    state_split = {
        str(row["state_example_id"]): str(row["split"])
        for row in state_cache["rows"]
    }
    parent_split = _json(paths["parent_split"])["split_by_parent"]
    transition_split = {
        str(row["transition_id"]): str(
            parent_split[str(row["parent_memory_id"])]
        )
        for row in transition_cache["rows"]
    }
    state_permutation = _split_permutation(
        ordered_state_ids,
        state_split,
        seed=deterministic_seed(GLOBAL_SEED, "state-control"),
    )
    transition_permutation = _split_permutation(
        ordered_transition_ids,
        transition_split,
        seed=deterministic_seed(GLOBAL_SEED, "transition-control"),
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    calibration = ensemble["train_calibration"]
    controls = {
        "correct": correct,
        "transition_only": _class_prior_matrix(
            labels=labels,
            ordered_state_ids=ordered_state_ids,
            ordered_transition_ids=ordered_transition_ids,
        ),
        "shuffled_state": _calibrated_scores(
            models=models,
            state_values=state_values[state_permutation],
            transition_values=transition_values,
            calibration=calibration,
            batch_states=int(settings["selector"]["batch_states"]),
            device=device,
        ),
        "shuffled_transition": _calibrated_scores(
            models=models,
            state_values=state_values,
            transition_values=transition_values[transition_permutation],
            calibration=calibration,
            batch_states=int(settings["selector"]["batch_states"]),
            device=device,
        ),
        "both_shuffled": _calibrated_scores(
            models=models,
            state_values=state_values[state_permutation],
            transition_values=transition_values[transition_permutation],
            calibration=calibration,
            batch_states=int(settings["selector"]["batch_states"]),
            device=device,
        ),
        "zero": torch.zeros_like(correct),
    }
    metrics: dict[str, Any] = {}
    for space in ("B", "C", "D", "E"):
        rows = _space_labels(labels, space)
        metrics[space] = {}
        for name, scores in controls.items():
            _, value = evaluate_score_matrix(
                rows=rows,
                scores=scores,
                ordered_state_ids=ordered_state_ids,
                ordered_transition_ids=ordered_transition_ids,
            )
            metrics[space][name] = value
    member_metrics = []
    for report in training["member_reports"]:
        member_scores = torch.load(
            Path(str(report["score_path"])),
            map_location="cpu",
            weights_only=False,
        )["scores"]
        member_metrics.append(
            {
                "member_index": int(report["member_index"]),
                "metrics": {
                    space: evaluate_score_matrix(
                        rows=_space_labels(labels, space),
                        scores=member_scores,
                        ordered_state_ids=ordered_state_ids,
                        ordered_transition_ids=ordered_transition_ids,
                    )[1]
                    for space in ("B", "C", "D", "E")
                },
            }
        )

    decomposition = FrozenSelectorDecomposition.from_checkpoints(
        checkpoints, calibration
    )
    query = decomposition.query(state_values)
    key = decomposition.key(transition_values)
    direct = decomposition.direct_scores(state_values, transition_values)
    interaction = query @ key.T
    decomposed = interaction + decomposition.intercept
    errors = {
        "direct_vs_qk_plus_intercept_max_abs": float(
            (direct - decomposed).abs().max()
        ),
        "direct_vs_qk_plus_intercept_mean_abs": float(
            (direct - decomposed).abs().mean()
        ),
        "centered_direct_vs_qk_max_abs": float(
            ((direct - decomposition.intercept) - interaction).abs().max()
        ),
        "centered_direct_vs_qk_mean_abs": float(
            ((direct - decomposition.intercept) - interaction).abs().mean()
        ),
        "stored_vs_direct_max_abs": float((correct - direct).abs().max()),
        "stored_vs_direct_mean_abs": float((correct - direct).abs().mean()),
    }
    tolerance = float(settings["selector"]["equality_atol"])
    if max(errors.values()) > tolerance:
        raise RuntimeError(f"Fresh selector factorization failed: {errors}")
    factor_payload = {
        "format": "one_demo_selector_factors_11c_v1",
        "run_uuid": RUN_UUID,
        "global_seed": GLOBAL_SEED,
        "ordered_state_ids": ordered_state_ids,
        "ordered_transition_ids": ordered_transition_ids,
        "state_queries": query,
        "state_query_sha256": tensor_sha256(query),
        "memory_keys": key,
        "memory_key_sha256": tensor_sha256(key),
        "selector_intercept": float(decomposition.intercept),
        "selector_score_definition": "q(s) @ k_i + global_intercept",
        "interaction_score_definition": "q(s) @ k_i",
        "decomposition_errors": errors,
        "ensemble_sha256": sha256_file(paths["selector_ensemble"]),
        "state_cache_sha256": sha256_file(paths["selector_state_cache"]),
        "transition_cache_sha256": sha256_file(paths["transition_cache"]),
        "old_q_or_k_loaded": False,
        "dev_used": False,
    }
    atomic_torch_save(factor_payload, paths["selector_factors"])
    factor_summary = {
        key_name: value
        for key_name, value in factor_payload.items()
        if key_name not in ("state_queries", "memory_keys")
    }
    factor_summary.update(
        {
            "state_query_shape": list(query.shape),
            "memory_key_shape": list(key.shape),
            "factor_artifact": str(paths["selector_factors"]),
            "factor_artifact_sha256": sha256_file(paths["selector_factors"]),
            "passed": max(errors.values()) <= tolerance,
        }
    )
    factor_summary["summary_sha256"] = canonical_sha256(factor_summary)
    atomic_write_json(paths["selector_factor_summary"], factor_summary)
    summary = {
        "format": "one_demo_selector_diagnostics_11c_v1",
        "run_uuid": RUN_UUID,
        "finite_scores": all(
            bool(torch.isfinite(value).all()) for value in controls.values()
        ),
        "class_balance": training["class_balance"],
        "metrics": metrics,
        "member_metrics": member_metrics,
        "controls": {
            "state_shuffle_seed": deterministic_seed(
                GLOBAL_SEED, "state-control"
            ),
            "transition_shuffle_seed": deterministic_seed(
                GLOBAL_SEED, "transition-control"
            ),
            "split_preserving": True,
        },
        "factor_summary": factor_summary,
        "diagnostics_used_for_tuning": False,
        "new_candidate_started": False,
        "dev_used": False,
        "completed_at_utc": utc_now(),
    }
    summary["passed"] = (
        summary["finite_scores"]
        and bool(summary["class_balance"]["passed"])
        and bool(factor_summary["passed"])
    )
    if not summary["passed"]:
        raise RuntimeError(f"Fresh selector diagnostics failed: {summary}")
    summary["summary_sha256"] = canonical_sha256(summary)
    atomic_write_json(paths["selector_diagnostics"], summary)
    attempt.progress(
        status="fresh_selector_diagnostics_complete",
        latest_validated_checkpoint=str(paths["selector_factors"]),
    )
    return summary


def _selection_compare(
    paths: Mapping[str, Path], attempt: AttemptLedger
) -> dict[str, Any]:
    _require(
        paths,
        (
            "new_selections",
            "selection_manifest",
            "exp034a_selections",
            "selector_ensemble",
            "selector_factor_summary",
        ),
    )
    new_rows = _rows(paths["new_selections"])
    old_rows = _rows(paths["exp034a_selections"])
    new_by_id = {str(row["state_example_id"]): row for row in new_rows}
    old_by_id = {str(row["state_example_id"]): row for row in old_rows}
    if set(new_by_id) != set(old_by_id):
        raise ValueError("EXP-034A and EXP-034B downstream state universes differ")
    changed_transition = []
    changed_class = []
    for state_id in sorted(new_by_id):
        new = new_by_id[state_id]
        old = old_by_id[state_id]
        if str(new["selected_transition_id"]) != str(
            old["selected_transition_id"]
        ):
            changed_transition.append(state_id)
        if str(new["selected_class_id"]) != str(old["selected_class_id"]):
            changed_class.append(state_id)
    payload = {
        "format": "one_demo_selector_selection_comparison_11c_v1",
        "run_uuid": RUN_UUID,
        "state_count": len(new_rows),
        "new_model_train_count": sum(
            str(row["model_split"]) == "model_train" for row in new_rows
        ),
        "new_heldout_count": sum(
            str(row["model_split"]) == "heldout_train_validation"
            for row in new_rows
        ),
        "changed_transition_count_vs_exp034a": len(changed_transition),
        "changed_transition_state_ids": changed_transition,
        "changed_class_count_vs_exp034a": len(changed_class),
        "changed_class_state_ids": changed_class,
        "new_selections_sha256": sha256_file(paths["new_selections"]),
        "exp034a_selections_sha256": sha256_file(paths["exp034a_selections"]),
        "new_selection_manifest_sha256": sha256_file(
            paths["selection_manifest"]
        ),
        "new_selector_ensemble_sha256": sha256_file(
            paths["selector_ensemble"]
        ),
        "historical_results_used_for_configuration": False,
        "behavioral_outcomes_used_for_selection": False,
        "dev_used": False,
        "created_at_utc": utc_now(),
    }
    payload["comparison_sha256"] = canonical_sha256(payload)
    atomic_write_json(paths["selection_compare"], payload)
    attempt.progress(
        status="fresh_selector_selection_comparison_complete",
        latest_validated_checkpoint=str(paths["selection_compare"]),
    )
    return payload


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_11c"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-034B requires the single global seed 25101")
    if str(cfg.benchmark.prompt_profile) != FULL_DEMO_FIRST_ONLY_PROFILE:
        raise ValueError("EXP-034B mixed prompt profiles")
    if os.name != "nt" and not os.path.ismount(
        str(settings["persistent_root"])
    ):
        raise RuntimeError("Persistent filesystem is not mounted")
    attempts_path = args.artifact_dir / "attempts.jsonl"
    if args.attempt_id in _attempt_ids(attempts_path):
        raise ValueError(f"Duplicate attempt ID: {args.attempt_id}")
    paths = _paths(cfg, args.artifact_dir)
    data_hashes = {"config": sha256_file(args.config)}
    for name in ("dependency", "recipe", "selector_state_cache", "transition_cache"):
        if paths[name].exists():
            data_hashes[name] = sha256_file(paths[name])
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=RUN_UUID,
        attempt_id=args.attempt_id,
        phase=f"one_demo_selector_retrain_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=True,
        heartbeat_interval_s=float(
            settings["runtime"]["heartbeat_interval_seconds"]
        ),
    ) as attempt:
        started = time.perf_counter()
        if args.phase == "dependency":
            result = _dependency(settings, paths)
            latest = paths["dependency"]
        elif args.phase == "selector-state-cache":
            result = _selector_state_cache(cfg, settings, paths, attempt)
            latest = paths["selector_state_manifest"]
        elif args.phase == "selector-recipe":
            result = _recipe(settings, paths)
            latest = paths["recipe"]
        elif args.phase == "selector-train":
            result = _selector_train(settings, paths, attempt)
            latest = paths["selector_training"]
        elif args.phase == "selector-diagnostics":
            result = _selector_diagnostics(settings, paths, attempt)
            latest = paths["selector_diagnostics"]
        else:
            result = _selection_compare(paths, attempt)
            latest = paths["selection_compare"]
        result["phase_elapsed_seconds"] = time.perf_counter() - started
        attempt.progress(
            status=f"one_demo_selector_retrain_{args.phase}_complete",
            latest_validated_checkpoint=str(latest),
            result=result,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
