from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from transformers import AutoTokenizer

from rcmf.config import load_config
from rcmf.training.appworld_structured_rescue_7hr import (
    FeatureSchema,
    GLOBAL_SEED,
    build_feature_vector,
    expansion_order,
    leakage_audit,
    quantile_buckets,
    select_diverse_panel,
)
from rcmf.training.datasets import _appworld_messages_from_example, load_decision_examples
from rcmf.training.deep_residual_amortization_7f import aggregate_and_select_class
from rcmf.training.procedural_supervision_6f import _stage_compatibility
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import (
    example_task_id,
    is_legal_transition_pair,
    messages_with_transition_memory,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_jsonl,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [dict(value) for value in read_jsonl(path)]
    if not rows:
        raise ValueError(f"No rows at {path}")
    return rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_structured_rescue_7hr.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", default="none")
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_prepare")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_d = Path(str(settings["parent_exp025d"]))
    parent_g = Path(str(settings["parent_exp027b"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    clean = parent_b / "clean_procedural_audit"
    preflight = artifact_dir / "preflight"
    return {
        "preflight": preflight,
        "decisions": corpus / "decision_examples.jsonl",
        "corpus_summary": corpus / "summary.json",
        "corpus_validation": corpus / "structural_validation.json",
        "replay_lineage": parent_b / "replay_validated_corpus_manifest.json",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "transition_signatures": clean / "clean_transition_signature_manifest.jsonl",
        "signature_classes": clean / "clean_signature_equivalence_manifest.json",
        "query_signatures": parent_c / "clean_query_signature_manifest.jsonl",
        "intent_predictions": parent_c / "clean_intent_probe/calibrated_predictions.jsonl",
        "intent_checkpoint": parent_c / "clean_intent_probe/action_intent_probe.pt",
        "selector": parent_c / "selector/ensemble_scores.pt",
        "task_split": Path(str(settings["panel"]["task_split_manifest"])),
        "parent_training": parent_g / "compiler/pairmlp/training_summary.json",
        "panel": preflight / "initial_panel.json",
        "selections": preflight / "frozen_train_selections.jsonl",
        "features": preflight / "structured_feature_rows.jsonl",
        "feature_schema": preflight / "structured_feature_schema.json",
        "leakage": preflight / "feature_leakage_audit.json",
        "runtime": artifact_dir / "runtime_preflight.json",
        "runtime_report": artifact_dir / "runtime_preflight.md",
        "run_manifest": artifact_dir / "run_manifest.json",
    }


def _require(paths: Mapping[str, Path], names: Sequence[str]) -> None:
    missing = {name: str(paths[name]) for name in names if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing EXP-028A immutable inputs: {missing}")


def _render_and_count(tokenizer: Any, messages: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    rendered = tokenizer.apply_chat_template(
        list(messages), tokenize=False, add_generation_prompt=True
    )
    tokens = tokenizer(rendered, add_special_tokens=False, truncation=False)["input_ids"]
    return rendered, len(tokens)


def _normalize_action_stratum(distributions: Mapping[str, Any]) -> str:
    action = max(distributions["action_type"], key=distributions["action_type"].get)
    api = max(distributions["target_api"], key=distributions["target_api"].get)
    if str(api).endswith(".login"):
        return "authentication"
    return {
        "api_documentation": "api_documentation",
        "api_mutation": "write_mutation",
        "api_read_or_login": "read_query",
        "completion": "completion",
        "python_or_reasoning": "python_reasoning",
    }.get(str(action), "api_other")


def _memory_flags(action: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "authentication": bool(action.get("authentication_login_action")),
        "read": bool(action.get("read_query_action")),
        "write": bool(action.get("write_mutation_action") or action.get("message_send_action")),
        "documentation": bool(action.get("api_documentation_action")),
        "completion": bool(action.get("completion_action")),
    }


def _class_selection(
    *,
    example: Any,
    state_scores: Sequence[float],
    ordered_transition_ids: Sequence[str],
    transition_class_ids: Sequence[str],
    transitions: Mapping[str, Mapping[str, Any]],
    classes: Mapping[str, Mapping[str, Any]],
    tokenizer: Any,
    prompt_profile: str,
    context_limit: int,
) -> dict[str, Any]:
    legal_ids = [
        transition_id
        for transition_id in ordered_transition_ids
        if is_legal_transition_pair(example, transitions[transition_id])
    ]
    selected = aggregate_and_select_class(
        state_scores,
        transition_class_ids,
        legal_transition_ids=legal_ids,
        ordered_transition_ids=ordered_transition_ids,
    )
    class_id = str(selected["selected_class_id"])
    class_row = classes[class_id]
    canonical = str(class_row["canonical_transition_id"])
    all_members = [str(value) for value in class_row["member_transition_ids"]]
    members = [
        str(value)
        for value in all_members
        if str(value) in set(legal_ids)
    ]
    if not members:
        raise ValueError(f"Selected class has no legal members: {class_id}")
    median = statistics.median(
        int(transitions[value]["teacher_section_tokens"]) for value in all_members
    )
    ordered_members = ([canonical] if canonical in members else []) + sorted(
        (value for value in members if value != canonical),
        key=lambda value: (
            abs(int(transitions[value]["teacher_section_tokens"]) - median),
            sha256_text(value),
        ),
    )
    base_messages = _appworld_messages_from_example(example, prompt_profile)
    base_rendered, base_tokens = _render_and_count(tokenizer, base_messages)
    attempts = []
    chosen = None
    raw_rendered = None
    raw_tokens = None
    for transition_id in ordered_members:
        raw_messages = messages_with_transition_memory(
            base_messages, transitions[transition_id], prompt_profile
        )
        rendered, tokens = _render_and_count(tokenizer, raw_messages)
        attempts.append({"transition_id": transition_id, "prompt_tokens": tokens})
        if tokens <= context_limit:
            chosen = transition_id
            raw_rendered = rendered
            raw_tokens = tokens
            break
    class_scores = dict(selected["class_scores"])
    ordered_class_scores = sorted(
        ((str(key), float(value)) for key, value in class_scores.items()),
        key=lambda row: (-row[1], sha256_text(row[0])),
    )
    return {
        "selected_class_id": class_id,
        "selected_transition_id": chosen,
        "canonical_transition_id": canonical,
        "canonical_transition_legal": canonical in members,
        "canonical_illegal_same_class_substitution": (
            chosen is not None and canonical not in members
        ),
        "same_class_substitution": chosen is not None and chosen != canonical,
        "class_score": float(selected["class_score"]),
        "class_margin": ordered_class_scores[0][1] - ordered_class_scores[1][1],
        "legal_transition_count": len(legal_ids),
        "legal_class_count": len(class_scores),
        "class_scores": class_scores,
        "ordered_class_scores": [value for _, value in ordered_class_scores],
        "base_prompt_sha256": sha256_text(base_rendered),
        "base_prompt_tokens": base_tokens,
        "raw_prompt_sha256": sha256_text(raw_rendered) if raw_rendered else None,
        "raw_prompt_tokens": raw_tokens,
        "over_context": chosen is None,
        "scoreable": chosen is not None,
        "attempts": attempts,
    }


def _feature_schema(
    intents: Sequence[Mapping[str, Any]],
    signatures: Sequence[Mapping[str, Any]],
) -> FeatureSchema:
    app_values = {"UNK"}
    api_values = {"UNK"}
    action_values = {"UNK"}
    controls = set()
    for row in intents:
        distributions = row["distributions"]
        app_values.update(str(value) for value in distributions["target_app"])
        api_values.update(str(value) for value in distributions["target_api"])
        action_values.update(str(value) for value in distributions["action_type"])
    for row in signatures:
        action = row["action_signature"]
        app_values.update(str(value) for value in action["all_app_api_pairs"] if "." not in str(value))
        app_values.update(str(value).split(".", 1)[0] for value in action["ordered_api_sequence"])
        api_values.update(str(value) for value in action["ordered_api_sequence"])
        action_values.add(str(action["coarse_action_type"]))
        controls.update(str(value) for value in action["control_flow_constructs"])
    return FeatureSchema(
        app_vocabulary=tuple(sorted(app_values)),
        api_vocabulary=tuple(sorted(api_values)),
        action_vocabulary=tuple(sorted(action_values)),
        control_vocabulary=tuple(sorted(controls)),
    )


def _runtime_projection(
    *,
    settings: Mapping[str, Any],
    initial_scoreable: int,
    maximum_scoreable: int,
    initial_train: int,
    initial_validation: int,
) -> dict[str, Any]:
    runtime = settings["runtime"]
    compiler = settings["compiler"]
    initial_paired = initial_scoreable * 2
    maximum_paired = maximum_scoreable * 2
    assumed_positive_train = max(40, round(initial_train / 3))
    training_units = initial_train + 2 * assumed_positive_train
    backwards = training_units * int(compiler["maximum_updates_per_pair"])
    validation_conditions = initial_validation * 4 * len(compiler["checkpoint_updates"])
    heldout_conditions = 45 * 4
    def scenario(name: str, paired: int) -> dict[str, float]:
        generation = float(runtime[f"one_step_generation_seconds_{name}"])
        forward = float(runtime[f"policy_forward_seconds_{name}"])
        backward = float(runtime[f"backward_seconds_{name}"])
        hours = (
            paired * (generation + forward)
            + backwards * backward
            + validation_conditions * generation
            + heldout_conditions * generation
        ) / 3600.0
        hours += 2 * float(runtime[f"first37_{name}_hours"])
        return {
            "h100_hours": hours,
            "paired_generation_and_policy_hours": paired * (generation + forward) / 3600.0,
            "compiler_training_hours": backwards * backward / 3600.0,
            "validation_one_step_hours": validation_conditions * generation / 3600.0,
            "heldout_one_step_hours": heldout_conditions * generation / 3600.0,
            "two_first37_hours": 2 * float(runtime[f"first37_{name}_hours"]),
        }
    expected = scenario("expected", initial_paired)
    conservative = scenario("conservative", maximum_paired)
    return {
        "initial_paired_condition_count": initial_paired,
        "maximum_paired_condition_count": maximum_paired,
        "gate_cpu_training": True,
        "assumed_positive_training_states": assumed_positive_train,
        "structured_compiler_backward_count": backwards,
        "train_validation_one_step_condition_count": validation_conditions,
        "heldout_one_step_condition_count": heldout_conditions,
        "gated_raw_first37_task_count": 37,
        "conditional_compiled_first37_task_count": 37,
        "expected": expected,
        "conservative": conservative,
        "review_threshold_h100_hours": float(runtime["review_threshold_h100_hours"]),
        "automatic_launch_allowed": expected["h100_hours"]
        <= float(runtime["review_threshold_h100_hours"]),
    }


def main() -> None:
    args = _parse_args()
    config = load_config(args.config)
    settings = config.raw["stage_c_7hr"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "decisions",
        "corpus_summary",
        "corpus_validation",
        "replay_lineage",
        "transitions",
        "transition_signatures",
        "signature_classes",
        "query_signatures",
        "intent_predictions",
        "intent_checkpoint",
        "selector",
        "task_split",
    )
    fresh_pipeline_mode = bool(settings.get("fresh_pipeline_mode", False))
    if not fresh_pipeline_mode:
        required += ("parent_training",)
    _require(paths, required)
    parent_training = None
    if not fresh_pipeline_mode:
        parent_training = _json(paths["parent_training"])
        paths["parent_checkpoint"] = Path(str(parent_training["selected_checkpoint"]))
        _require(paths, ("parent_checkpoint",))
        required = (*required, "parent_checkpoint")
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    expected_selector = str(settings["expected_selector_sha256"])
    if expected_selector == "fresh_stage_output":
        expected_selector = source_hashes["selector"]
    immutable_checks = {
        "selector_sha256": source_hashes["selector"] == expected_selector,
        "replay_lineage": str(_json(paths["replay_lineage"])["lineage_sha256"])
        == str(settings["expected_replay_lineage_sha256"]),
    }
    if parent_training is not None:
        immutable_checks.update(
            {
                "parent_checkpoint_sha256": source_hashes["parent_checkpoint"]
                == str(settings["expected_exp027b_checkpoint_sha256"]),
                "parent_checkpoint_selected": str(
                    parent_training["selected_checkpoint_sha256"]
                )
                == str(settings["expected_exp027b_checkpoint_sha256"]),
            }
        )
    if not all(immutable_checks.values()):
        raise ValueError(f"EXP-028A immutable inputs differ: {immutable_checks}")
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="outcome_blind_panel_feature_runtime_preflight",
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
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        started = time.perf_counter()
        examples = load_decision_examples(paths["decisions"])
        query_signature_rows = _rows(paths["query_signatures"])
        query_signatures = {
            str(row["state_example_id"]): row for row in query_signature_rows
        }
        train_examples = [
            (index, example)
            for index, example in enumerate(examples)
            if str(query_signatures[state_example_id(index, example)]["split"])
            == "train"
        ]
        if len(train_examples) != 499:
            raise ValueError(f"Expected 499 clean train states, found {len(train_examples)}")
        transitions_list = _rows(paths["transitions"])
        if len(transitions_list) != 499:
            raise ValueError("Clean train transition bank is not 499")
        transitions = {str(row["transition_id"]): row for row in transitions_list}
        signatures_list = _rows(paths["transition_signatures"])
        signatures = {str(row["transition_id"]): row for row in signatures_list}
        class_payload = _json(paths["signature_classes"])
        classes = {str(row["signature_class_id"]): row for row in class_payload["classes"]}
        class_by_transition: dict[str, str] = {}
        for class_id, row in classes.items():
            for transition_id in row["member_transition_ids"]:
                class_by_transition[str(transition_id)] = class_id
        intents_list = _rows(paths["intent_predictions"])
        intents = {str(row["state_example_id"]): row for row in intents_list}
        ensemble = torch.load(paths["selector"], map_location="cpu", weights_only=False)
        ordered_state_ids = [str(value) for value in ensemble["ordered_state_ids"]]
        ordered_transition_ids = [str(value) for value in ensemble["ordered_transition_ids"]]
        if set(ordered_transition_ids) != set(transitions):
            raise ValueError("Selector and clean transition ledgers differ")
        transition_class_ids = [class_by_transition[value] for value in ordered_transition_ids]
        state_position = {value: index for index, value in enumerate(ordered_state_ids)}
        tokenizer = AutoTokenizer.from_pretrained(
            str(settings["expected_model_name"]), trust_remote_code=True
        )
        task_max_step = defaultdict(int)
        for _, example in train_examples:
            task_max_step[example_task_id(example)] = max(
                task_max_step[example_task_id(example)], int(example.step_id)
            )
        selections = []
        for ordinal, (example_index, example) in enumerate(train_examples, start=1):
            state_id = state_example_id(example_index, example)
            if state_id not in state_position or state_id not in intents:
                raise ValueError(f"Missing frozen selector/intent row: {state_id}")
            selected = _class_selection(
                example=example,
                state_scores=ensemble["scores"][state_position[state_id]].tolist(),
                ordered_transition_ids=ordered_transition_ids,
                transition_class_ids=transition_class_ids,
                transitions=transitions,
                classes=classes,
                tokenizer=tokenizer,
                prompt_profile=str(settings["appworld"]["prompt_profile"]),
                context_limit=int(settings["appworld"]["context_limit"]),
            )
            task_id = example_task_id(example)
            maximum = task_max_step[task_id]
            fraction = int(example.step_id) / max(1, maximum)
            step_bucket = "early" if fraction <= 1 / 3 else "middle" if fraction <= 2 / 3 else "late"
            selections.append(
                {
                    "format": "frozen_train_state_selection_7hr_v1",
                    "state_example_id": state_id,
                    "state_task_id": task_id,
                    "state_step_id": int(example.step_id),
                    "step_bucket": step_bucket,
                    "predicted_action_stratum": _normalize_action_stratum(
                        intents[state_id]["distributions"]
                    ),
                    **selected,
                    "selection_uses_target_action": False,
                    "selection_uses_behavioral_outcome": False,
                    "selection_uses_first37": False,
                }
            )
            if ordinal % 25 == 0 or ordinal == len(train_examples):
                attempt.progress(
                    status="freezing_train_selector_panel",
                    completed_states=ordinal,
                    total_states=len(train_examples),
                    latest_validated_checkpoint=str(paths["selections"]),
                )
                print(f"frozen selection {ordinal}/{len(train_examples)}", flush=True)
        score_quantiles = quantile_buckets(
            [float(row["class_score"]) for row in selections],
            int(settings["panel"]["quantile_count"]),
        )
        margin_quantiles = quantile_buckets(
            [float(row["class_margin"]) for row in selections],
            int(settings["panel"]["quantile_count"]),
        )
        for row, score_q, margin_q in zip(
            selections, score_quantiles, margin_quantiles, strict=True
        ):
            row["selector_score_quantile"] = score_q
            row["selector_margin_quantile"] = margin_q
        schema = _feature_schema(intents_list, signatures_list)
        query_stages = {
            str(row["state_example_id"]): row["state_stage_signature"]
            for row in query_signature_rows
        }
        feature_rows = []
        for row in selections:
            state_id = str(row["state_example_id"])
            transition_id = row["selected_transition_id"]
            if transition_id is None:
                feature_rows.append(
                    {
                        "format": "appworld_structured_feature_row_7hr_v1",
                        "state_example_id": state_id,
                        "state_task_id": row["state_task_id"],
                        "transition_id": None,
                        "scoreable": False,
                        "missing_reason": "selected_signature_class_has_no_context_feasible_raw_member",
                        "feature_values": None,
                    }
                )
                continue
            transition = transitions[str(transition_id)]
            signature = signatures[str(transition_id)]
            action = signature["action_signature"]
            stage = _stage_compatibility(
                query_stages[state_id], signature["pre_action_stage_signature"]
            )
            source = {
                "state_step_index": int(row["state_step_id"]),
                "history_turn_count": max(0, int(row["state_step_id"]) - 1),
                "prompt_tokens": int(row["base_prompt_tokens"]),
                "context_headroom": int(settings["appworld"]["context_limit"])
                - int(row["base_prompt_tokens"]),
                "context_limit": int(settings["appworld"]["context_limit"]),
                "intent_distributions": intents[state_id]["distributions"],
                "selector_class_scores": row["ordered_class_scores"],
                "memory_apps": [str(value) for value in transition["apps"]],
                "memory_apis": [str(value) for value in transition["api_names"]],
                "memory_action_type": str(action["coarse_action_type"]),
                "memory_control_flow": [str(value) for value in action["control_flow_constructs"]],
                "memory_flags": _memory_flags(action),
                "memory_class_size": int(classes[str(row["selected_class_id"])]["class_size"]),
                "memory_token_length": int(transition["teacher_section_tokens"]),
                "memory_parent_step": int(transition["step_index"]),
                "memory_api_call_count": len(action["ordered_api_sequence"]),
                "projected_prompt_overhead": int(row["raw_prompt_tokens"])
                - int(row["base_prompt_tokens"]),
                "stage_compatibility": stage,
            }
            values, names = build_feature_vector(schema, source)
            feature_rows.append(
                {
                    "format": "appworld_structured_feature_row_7hr_v1",
                    "state_example_id": state_id,
                    "state_task_id": row["state_task_id"],
                    "transition_id": transition_id,
                    "selected_class_id": row["selected_class_id"],
                    "scoreable": True,
                    "feature_values": values,
                    "feature_sha256": canonical_sha256({"names": names, "values": values}),
                    "deployment_available": True,
                    "target_action_used": False,
                    "outcome_used": False,
                }
            )
        names = list(schema.names)
        audit = leakage_audit(names, settings["feature_contract"]["forbidden_fields"])
        if not audit["deployment_available"]:
            raise RuntimeError(f"Structured feature leakage audit failed: {audit}")
        split = _json(paths["task_split"])
        if len(split["train_task_ids"]) != 29 or len(split["validation_task_ids"]) != 8:
            raise ValueError("Exact 29/8 A-task split changed")
        split_by_task = {str(value): "model_train" for value in split["train_task_ids"]}
        split_by_task.update(
            {str(value): "heldout_train_validation" for value in split["validation_task_ids"]}
        )
        for row in selections:
            row["model_split"] = split_by_task[str(row["state_task_id"])]
        panel_ids = select_diverse_panel(
            selections,
            count=int(settings["panel"]["initial_state_count"]),
            seed=GLOBAL_SEED,
        )
        expansion = expansion_order(selections, panel_ids, seed=GLOBAL_SEED)
        selection_by_id = {str(row["state_example_id"]): row for row in selections}
        panel_rows = [selection_by_id[value] for value in panel_ids]
        panel = {
            "format": "train_side_causal_panel_7hr_v1",
            "global_seed": GLOBAL_SEED,
            "selection_frozen_before_outcomes": True,
            "initial_state_count": len(panel_ids),
            "initial_scoreable_state_count": sum(bool(row["scoreable"]) for row in panel_rows),
            "initial_over_context_missing_count": sum(bool(row["over_context"]) for row in panel_rows),
            "train_task_count": len({row["state_task_id"] for row in panel_rows}),
            "model_train_task_count": len(
                {row["state_task_id"] for row in panel_rows if row["model_split"] == "model_train"}
            ),
            "heldout_train_validation_task_count": len(
                {
                    row["state_task_id"]
                    for row in panel_rows
                    if row["model_split"] == "heldout_train_validation"
                }
            ),
            "state_ids": panel_ids,
            "expansion_order": expansion,
            "expansion_rule": (
                "append scoreable remaining clean-train states in frozen SHA256 order until "
                "POSITIVE/HARMFUL/NEUTRAL each reach 40 or all 499 states are exhausted"
            ),
            "minimum_per_label": int(settings["panel"]["minimum_per_label"]),
            "task_split_manifest_sha256": source_hashes["task_split"],
            "action_strata": dict(Counter(row["predicted_action_stratum"] for row in panel_rows)),
            "step_buckets": dict(Counter(row["step_bucket"] for row in panel_rows)),
            "score_quantiles": dict(Counter(row["selector_score_quantile"] for row in panel_rows)),
            "margin_quantiles": dict(Counter(row["selector_margin_quantile"] for row in panel_rows)),
            "first37_outcomes_used": False,
            "behavioral_outcomes_used": False,
        }
        panel["manifest_sha256"] = canonical_sha256(panel)
        paths["preflight"].mkdir(parents=True, exist_ok=True)
        write_jsonl(paths["selections"], selections)
        write_jsonl(paths["features"], feature_rows)
        atomic_write_json(
            paths["feature_schema"],
            {
                "format": "appworld_structured_feature_schema_7hr_v1",
                "version": schema.version,
                "names": names,
                "feature_count": len(names),
                "app_vocabulary": list(schema.app_vocabulary),
                "api_vocabulary": list(schema.api_vocabulary),
                "action_vocabulary": list(schema.action_vocabulary),
                "control_vocabulary": list(schema.control_vocabulary),
                "manifest_sha256": canonical_sha256({"version": schema.version, "names": names}),
            },
        )
        atomic_write_json(paths["leakage"], audit)
        atomic_write_json(paths["panel"], panel)
        scoreable_features = [row for row in feature_rows if row["scoreable"]]
        panel_feature_ids = set(panel_ids)
        initial_features = [
            row for row in scoreable_features if row["state_example_id"] in panel_feature_ids
        ]
        initial_train = sum(
            split_by_task[str(row["state_task_id"])] == "model_train"
            for row in initial_features
        )
        initial_validation = len(initial_features) - initial_train
        runtime = _runtime_projection(
            settings=settings,
            initial_scoreable=len(initial_features),
            maximum_scoreable=len(scoreable_features),
            initial_train=initial_train,
            initial_validation=initial_validation,
        )
        runtime.update(
            {
                "format": "appworld_structured_rescue_runtime_preflight_7hr_v1",
                "run_uuid": str(settings["run_uuid"]),
                "global_seed": GLOBAL_SEED,
                "initial_state_count": len(panel_ids),
                "initial_scoreable_state_count": len(initial_features),
                "initial_model_train_state_count": initial_train,
                "initial_validation_state_count": initial_validation,
                "maximum_scoreable_state_count": len(scoreable_features),
                "projected_artifact_bytes": (
                    int(runtime["maximum_paired_condition_count"])
                    * int(settings["runtime"]["projected_bytes_per_condition"])
                    + 2 * int(settings["runtime"]["projected_bytes_per_checkpoint"])
                ),
            }
        )
        if not runtime["automatic_launch_allowed"]:
            raise RuntimeError("Expected EXP-028A H100 time exceeds the 18-hour review threshold")
        atomic_write_json(paths["runtime"], runtime)
        atomic_write_text(
            paths["runtime_report"],
            "\n".join(
                [
                    "# EXP-028A runtime preflight",
                    "",
                    f"- initial states/scoreable: `{len(panel_ids)}/{len(initial_features)}`",
                    f"- model-train/heldout-train-validation: `{initial_train}/{initial_validation}`",
                    f"- initial/maximum paired T0+T1 conditions: `{runtime['initial_paired_condition_count']}/{runtime['maximum_paired_condition_count']}`",
                    f"- structured compiler backward count: `{runtime['structured_compiler_backward_count']}`",
                    f"- train-validation one-step conditions: `{runtime['train_validation_one_step_condition_count']}`",
                    f"- locked heldout one-step conditions: `{runtime['heldout_one_step_condition_count']}`",
                    f"- expected H100 hours: `{runtime['expected']['h100_hours']:.4f}`",
                    f"- conservative H100 hours: `{runtime['conservative']['h100_hours']:.4f}`",
                    f"- 18-hour automatic launch: `{str(runtime['automatic_launch_allowed']).lower()}`",
                    "- no behavioral or first37 result influenced this preflight.",
                    "",
                ]
            ),
        )
        run_manifest = {
            "format": "appworld_structured_gate_compiler_run_manifest_7hr_v1",
            "run_uuid": str(settings["run_uuid"]),
            "global_seed": GLOBAL_SEED,
            "source_commit": args.lambda_head,
            "config_sha256": sha256_file(args.config),
            "source_hashes": source_hashes,
            "immutable_checks": immutable_checks,
            "panel_manifest_sha256": panel["manifest_sha256"],
            "feature_schema_sha256": sha256_file(paths["feature_schema"]),
            "runtime_preflight_sha256": sha256_file(paths["runtime"]),
            "parent_exp027b_preserved": True,
            "first37_used_for_gate_or_compiler_selection": False,
            "created_elapsed_seconds": time.perf_counter() - started,
        }
        run_manifest["manifest_sha256"] = canonical_sha256(run_manifest)
        atomic_write_json(paths["run_manifest"], run_manifest)
        attempt.progress(
            status="preflight_complete",
            completed_states=len(selections),
            latest_validated_checkpoint=str(paths["run_manifest"]),
        )
        print(json.dumps({"panel": panel, "runtime": runtime}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
