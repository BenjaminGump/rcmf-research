from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml

from rcmf.config import load_config
from rcmf.benchmarks.appworld.reproduction_contract_14e import (
    resolved_causal_panel_contract,
    validate_post_d06_expectations_are_not_panel_inputs,
)


def arm_root(run_root: str | Path, arm_id: str) -> Path:
    if arm_id not in {"3d", "1d"}:
        raise ValueError(f"Unknown EXP-037A arm: {arm_id}")
    return Path(run_root) / "arms" / arm_id


def compatibility_parent_b(run_root: str | Path) -> Path:
    return Path(run_root) / "shared" / "compat_exp025b"


def build_arm_runtime_config(
    pipeline: Mapping[str, Any], run_root: str | Path, arm_id: str
) -> dict[str, Any]:
    """Resolve the historical runners onto fresh EXP-037A-owned artifacts."""
    base = copy.deepcopy(
        load_config(
            Path("configs/benchmark/stage_c_rcmf_one_demo_retrain_11b.yaml")
        ).raw
    )
    selector_source = load_config(
        Path("configs/benchmark/stage_c_signature_balanced_field_7c.yaml")
    ).raw
    base["stage_c_7c"] = copy.deepcopy(selector_source["stage_c_7c"])
    root = Path(run_root)
    target = arm_root(root, arm_id)
    shared = root / "shared"
    parent_b = compatibility_parent_b(root)
    prompt_profile = str(
        pipeline["arms"][arm_id]["task_conditioned_prompt_profile"]
    )
    expected = pipeline["pipeline"]["expected"]
    method = pipeline["pipeline"]

    base["experiment"] = {
        "name": f"rcmf_reproducible_pipeline_14b_{arm_id}",
        "version": "rcmf_reproducible_pipeline_arm_14b_v1",
        "seed": int(method["global_seed"]),
    }
    base.setdefault("benchmark", {})["prompt_profile"] = prompt_profile

    selector = copy.deepcopy(base["stage_c_7c"])
    selector.update(
        {
            "run_uuid": f"{method['run_uuid']}-{arm_id}",
            "artifact_dir": str(target),
            "parent_exp025b": str(parent_b),
            "reconciled_corpus_dir": str(method["roots"]["authoritative_corpus"]),
            "expected_structural_lineage_sha256": str(
                method["expected"]["structural_lineage_sha256"]
            ),
            "expected_replay_lineage_sha256": str(
                method["expected"]["replay_lineage_sha256"]
            ),
        }
    )
    selector["multiview_cache"] = {
        **selector["multiview_cache"],
        "output_root": str(target / "representation_cache/multiview"),
        "old_state_cache": str(root / "forbidden_historical_state_cache.pt"),
        "old_transition_cache": str(root / "forbidden_historical_transition_cache.pt"),
        "expected_reused_states": 0,
        "expected_recomputed_states": int(expected["selector_train_states"])
        + int(expected["selector_validation_states"]),
        "expected_reused_transitions": 0,
        "expected_recomputed_transitions": int(expected["train_transitions"]),
        "fresh_rebuild_without_old_cache": True,
    }
    selector["selector"] = {
        **copy.deepcopy(method["selector"]),
        "final_seeds": list(method["final_selector_member_seeds"]),
        "bootstrap_samples": int(
            base["stage_c_7c"]["selector"]["bootstrap_samples"]
        ),
        "bootstrap_seed": int(
            base["stage_c_7c"]["selector"]["bootstrap_seed"]
        ),
        "initialization_root": str(root / "preflight/initialization_snapshots"),
    }
    selector["generation"] = {
        **selector["generation"],
        "prompt_profile": prompt_profile,
    }
    base["stage_c_7c"] = selector

    causal = copy.deepcopy(base["stage_c_7hr"])
    causal.update(
        {
            "run_uuid": f"{method['run_uuid']}-{arm_id}",
            "artifact_dir": str(target),
            "parent_exp025b": str(parent_b),
            "parent_exp025c": str(target),
            "reconciled_corpus_dir": str(method["roots"]["authoritative_corpus"]),
            "expected_replay_lineage_sha256": str(
                method["expected"]["replay_lineage_sha256"]
            ),
            "expected_structural_lineage_sha256": str(
                method["expected"]["structural_lineage_sha256"]
            ),
            "expected_model_name": str(method["roots"]["model_snapshot"]),
            "expected_selector_sha256": "fresh_stage_output",
            "fresh_pipeline_mode": True,
        }
    )
    causal["appworld"] = {
        **causal["appworld"],
        "prompt_profile": prompt_profile,
        **{
            key: method["evaluation"][key]
            for key in (
                "max_context_turns",
                "max_steps",
                "max_new_tokens",
                "context_limit",
                "temperature",
                "top_p",
                "enable_thinking",
                "max_api_calls_per_interaction",
            )
        },
    }
    panel_contract = resolved_causal_panel_contract(method)
    causal["panel"] = {
        **causal["panel"],
        **panel_contract,
        "task_split_manifest": str(method["roots"]["approved_downstream_split"]),
    }
    panel_independence = validate_post_d06_expectations_are_not_panel_inputs(
        method, panel_contract
    )
    if not panel_independence["passed"]:
        raise ValueError(f"Causal-panel independence gate failed: {panel_independence}")
    causal["panel"]["post_d06_reproduction_expectation"] = (
        panel_independence["post_d06_expected_completed"]
    )
    causal["runtime"] = {
        **causal["runtime"],
        "review_threshold_h100_hours": float(method["approved_hard_cap_hours"]),
    }
    base["stage_c_7hr"] = causal

    full_bank = copy.deepcopy(base["stage_c_9a"])
    full_bank.update(
        {
            "run_uuid": f"{method['run_uuid']}-{arm_id}",
            "artifact_dir": str(target),
            "starting_head": "resolved_at_launch",
            "working_branch": str(
                method.get(
                    "working_branch", "research/v6-rcmf-reproducible-3d-gated-pipeline"
                )
            ),
            "global_seed": int(method["global_seed"]),
            "parent_exp025b": str(parent_b),
            "parent_exp025c": str(target),
            "parent_exp028a": str(target),
            "reconciled_corpus_dir": str(method["roots"]["authoritative_corpus"]),
            "task_split_manifest": str(method["roots"]["approved_downstream_split"]),
        }
    )
    full_bank["expected"] = {
        **full_bank["expected"],
        "structural_lineage_sha256": str(
            method["expected"]["structural_lineage_sha256"]
        ),
        "replay_lineage_sha256": str(
            method["expected"]["replay_lineage_sha256"]
        ),
        "selector_ensemble_sha256": "fresh_stage_output",
        "model_name": str(method["roots"]["model_snapshot"]),
    }
    full_bank["selector"] = {
        **full_bank["selector"],
        "require_stored_score_equivalence": True,
    }
    full_bank["prompt_dependent_inputs"] = {
        "state_cache": str(target / "representation_cache/multiview/state_multiview.pt"),
        "outcomes": str(target / "paired_causal/paired_outcomes.json"),
        "teacher_cache": str(target / "structured_compiler/policy_teacher_cache.pt"),
    }
    full_bank["appworld"] = {
        **full_bank["appworld"],
        "prompt_profile": prompt_profile,
        **{
            key: method["evaluation"][key]
            for key in (
                "max_context_turns",
                "max_steps",
                "max_api_calls_per_interaction",
                "max_new_tokens",
                "context_limit",
                "temperature",
                "top_p",
                "do_sample",
                "enable_thinking",
            )
        },
    }
    full_bank["runtime"] = {
        **full_bank["runtime"],
        "review_threshold_h100_hours": float(method["approved_hard_cap_hours"]),
    }
    base["stage_c_9a"] = full_bank

    base["stage_c_11b"] = {
        **base["stage_c_11b"],
        "run_uuid": f"{method['run_uuid']}-{arm_id}",
        "artifact_dir": str(target),
        "prompt_profile": prompt_profile,
        "persistent_root": str(method["required_environment"]["persistent_mount"]),
    }
    return base


def write_resolved_arm_config(
    path: str | Path,
    pipeline: Mapping[str, Any],
    run_root: str | Path,
    arm_id: str,
) -> dict[str, Any]:
    payload = build_arm_runtime_config(pipeline, run_root, arm_id)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    if target.exists() and target.read_text(encoding="utf-8") != rendered:
        raise ValueError(f"Frozen resolved arm config differs: {target}")
    target.write_text(rendered, encoding="utf-8")
    return payload
