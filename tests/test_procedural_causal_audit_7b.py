from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

from rcmf.training.procedural_causal_audit_7b import (
    LIVE_GENERATION_RESULT_VERSION,
    LIVE_BRIDGE_PROTOCOL_VERSION,
    LiveBridgeClient,
    build_live_appworld_messages,
    compare_condition_manifests,
    condition_checkpoint_name,
    generation_runtime_projection,
    validate_condition_checkpoint,
)
from rcmf.training.procedural_causal_analysis_7b import (
    select_decision,
    validate_formal_rows,
)
from scripts.run_procedural_causal_audit_7b import _state_contract
from scripts.prepare_procedural_causal_audit_7b import (
    _validate_required_condition_coverage,
)


def _example() -> SimpleNamespace:
    return SimpleNamespace(
        state_text=(
            "[QUERY]\nClean task header\n[TRACE SO FAR]\n"
            "Step 1 - Response:\nx = 1\n"
            "Step 1 - Observation:\nhistorical token\n"
            "Step 2 - Response:\nprint(x)\n"
            "Step 2 - Observation:\nhistorical result"
        ),
        target_type="code",
        metadata={"system_prompt": "unused for full-demo"},
    )


def test_live_messages_use_actual_observations_and_preserve_actions() -> None:
    messages = build_live_appworld_messages(
        _example(), ["live token", "live result"], prompt_profile="minimal"
    )
    assert messages[-4:] == [
        {"role": "assistant", "content": "x = 1"},
        {"role": "user", "content": "Output:\n```\nlive token\n```"},
        {"role": "assistant", "content": "print(x)"},
        {"role": "user", "content": "Output:\n```\nlive result\n```"},
    ]
    rendered = "\n".join(row["content"] for row in messages)
    assert "historical token" not in rendered
    assert "historical result" not in rendered
    assert "Clean task header" in rendered


def test_live_messages_reject_observation_count_mismatch() -> None:
    with pytest.raises(ValueError, match="observation count"):
        build_live_appworld_messages(_example(), ["only one"], prompt_profile="minimal")


def test_condition_manifest_comparison_distinguishes_id_only_change() -> None:
    old = {
        "conditions": [
            {
                "condition_key": "old",
                "state_example_id": "s",
                "condition_name": "C1_raw_oracle",
                "transition_id": "old-t",
            }
        ]
    }
    clean = {
        "conditions": [
            {
                "condition_key": "clean",
                "state_example_id": "s",
                "condition_name": "C1_raw_oracle",
                "transition_id": "clean-t",
            }
        ]
    }
    result = compare_condition_manifests(
        old,
        clean,
        old_transition_semantics={"old-t": ("task", 3)},
        clean_transition_semantics={"clean-t": ("task", 3)},
    )
    assert result["classification_counts"] == {"id_only_change": 1}


def test_condition_checkpoint_validation_is_hash_strict() -> None:
    condition = {
        "condition_key": "key",
        "condition_name": "C0_bare",
        "state_example_id": "state",
    }
    row = {
        "format": LIVE_GENERATION_RESULT_VERSION,
        "status": "complete",
        **condition,
        "condition_manifest_sha256": "manifest",
        "config_sha256": "config",
        "corpus_lineage_sha256": "lineage",
        "model_name": "model",
        "live_worker": {"complete": True, "same_world_execution": True},
    }
    validate_condition_checkpoint(
        row,
        condition=condition,
        condition_manifest_sha256="manifest",
        config_sha256="config",
        corpus_lineage_sha256="lineage",
        model_name="model",
    )
    row["live_worker"]["same_world_execution"] = False
    with pytest.raises(ValueError, match="same_world"):
        validate_condition_checkpoint(
            row,
            condition=condition,
            condition_manifest_sha256="manifest",
            config_sha256="config",
            corpus_lineage_sha256="lineage",
            model_name="model",
        )


def test_runtime_projection_does_not_shrink_condition_count() -> None:
    projection = generation_runtime_projection(
        323,
        45,
        {
            "runtime": {
                "generation_seconds_per_condition": {
                    "best": 20,
                    "expected": 45,
                    "conservative": 90,
                },
                "replay_seconds_per_condition": {
                    "best": 2,
                    "expected": 5,
                    "conservative": 10,
                },
                "validation_seconds_per_state": {
                    "best": 1,
                    "expected": 2,
                    "conservative": 4,
                },
                "artifact_bytes_per_condition": 100,
                "review_threshold_h100_hours": 12,
            }
        },
    )
    assert projection["condition_count"] == 323
    assert projection["qwen_generation_count"] == 323
    assert projection["scenarios"]["expected"]["h100_hours"] == pytest.approx(323 * 45 / 3600)
    assert not projection["requires_explicit_runtime_approval"]
    assert condition_checkpoint_name("key") == condition_checkpoint_name("key")


def test_live_bridge_client_uses_two_stage_protocol(tmp_path: Path) -> None:
    child = tmp_path / "child.py"
    child.write_text(
        """import json, sys
prepare = json.loads(sys.stdin.readline())
print(json.dumps({
    'format': prepare['format'], 'op': 'ready', 'ready': True,
    'ready_nonce': 'nonce', 'condition_key': prepare['condition_key']
}), flush=True)
execute = json.loads(sys.stdin.readline())
print(json.dumps({
    'format': execute['format'], 'op': 'executed', 'complete': True,
    'same_world_execution': True, 'condition_key': execute['condition_key']
}), flush=True)
""",
        encoding="utf-8",
    )
    root = tmp_path / "root"
    root.mkdir()
    with LiveBridgeClient(
        executable=Path(sys.executable),
        bridge_script=child,
        appworld_root=root,
        stderr_path=tmp_path / "child.err",
        timeout_seconds=10,
    ) as client:
        ready = client.prepare(
            {
                "format": LIVE_BRIDGE_PROTOCOL_VERSION,
                "op": "prepare",
                "condition_key": "key",
            }
        )
        executed = client.execute(condition_key="key", ready_nonce=ready["ready_nonce"], code="x=1")
    assert executed["same_world_execution"]


def test_live_state_contract_preserves_recorded_history_and_target() -> None:
    example = SimpleNamespace(
        state_text=(
            "[QUERY]\nNow here is another task in a different environment. "
            "The task is the following:\nMy name is: A B. My personal email is "
            "a@example.com and phone number is 1234567890.\nTask: Test\n"
            "[TRACE SO FAR]\nStep 1 - Response:\n```python\nx = 1\n```\n"
            "Step 1 - Observation:\n1"
        ),
        target_text="```python\nprint(x)\n```",
        step_id=2,
    )
    record = SimpleNamespace(
        raw_trajectory={
            "steps": [
                {
                    "response": "```python\nx = 1\n```",
                    "observation": "1",
                },
                {
                    "response": "```python\nprint(x)\n```",
                    "observation": "1",
                },
            ]
        }
    )
    contract = _state_contract(example, record)
    assert contract["history_steps"][0]["code"] == "x = 1"
    assert contract["history_steps"][0]["expected_observation"] == "1"
    assert contract["target_code"] == "print(x)"


def test_live_worker_source_keeps_world_open_across_prepare_and_execute() -> None:
    source = Path("scripts/appworld_live_one_step_bridge_7b.py").read_text(encoding="utf-8")
    assert "world.__enter__()" in source
    assert "execute = _read_message()" in source
    assert "world.execute(code)" in source
    assert '"same_world_execution": True' in source
    assert "world.__exit__(None, None, None)" in source


def _comparison(exact: float, signature: float, execution: float = 0.0) -> dict:
    def metric(value: float) -> dict:
        return {
            "difference": value,
            "ci95_low": value - 0.01,
            "ci95_high": value + 0.01,
        }

    return {
        "exact_primary_app_api_match": metric(exact),
        "canonical_procedural_signature_match": metric(signature),
        "execution_success": metric(execution),
    }


def _gates() -> dict:
    return {
        "oracle_exact_api_gain": 0.10,
        "material_control_gain": 0.05,
        "raw_content_gain_over_signature_card": 0.05,
        "maximum_execution_drop": 0.05,
        "minimum_positive_tasks": 6,
        "same_signature_direction_agreement": 0.70,
        "metadata_gain_retention": 0.90,
    }


def test_decision_requires_content_beyond_metadata() -> None:
    primary = {
        "C1_raw_oracle_minus_C0_bare": _comparison(0.20, 0.15),
        "C1_raw_oracle_minus_C2_signature_only": _comparison(0.02, 0.01),
        "C1_raw_oracle_minus_C3_hard_negative": _comparison(0.10, 0.10),
        "C1_raw_oracle_minus_C5_unrelated": _comparison(0.12, 0.12),
        "C2_signature_only_minus_C0_bare": _comparison(0.19, 0.14),
        "positive_task_count": 8,
    }
    result = select_decision(
        primary=primary,
        documentation={},
        consistency={"same_effect_direction_fraction": 0.9},
        gates=_gates(),
    )
    assert result["procedural_oracle_behavioral_gate"]
    assert not result["content_beyond_metadata_gate"]
    assert result["metadata_sufficient"]
    assert result["decision_branch"] == (
        "procedural_metadata_sufficient_raw_transition_content_not_validated"
    )


def test_decision_clean_raw_content_pass_branch() -> None:
    primary = {
        "C1_raw_oracle_minus_C0_bare": _comparison(0.20, 0.15),
        "C1_raw_oracle_minus_C2_signature_only": _comparison(0.08, 0.07),
        "C1_raw_oracle_minus_C3_hard_negative": _comparison(0.10, 0.10),
        "C1_raw_oracle_minus_C5_unrelated": _comparison(0.12, 0.12),
        "C2_signature_only_minus_C0_bare": _comparison(0.12, 0.08),
        "positive_task_count": 7,
    }
    result = select_decision(
        primary=primary,
        documentation={},
        consistency={"same_effect_direction_fraction": 0.8},
        gates=_gates(),
    )
    assert result["decision_branch"] == (
        "raw_transition_content_behaviorally_validated_on_clean_corpus"
    )
    assert result["raw_transition_content_behaviorally_validated"]
    assert result["field_program_training_remains_blocked"]


def test_formal_validation_requires_same_world_and_all_core_conditions() -> None:
    condition_names = (
        "C0_bare",
        "C1_raw_oracle",
        "C2_signature_only",
        "C3_hard_negative",
        "C4_signature_popularity",
        "C5_unrelated",
    )
    conditions = [
        {
            "condition_key": name,
            "condition_name": name,
            "state_example_id": "state",
        }
        for name in condition_names
    ]
    rows = [
        {
            **condition,
            "state_task_id": "task",
            "live_worker": {
                "complete": True,
                "same_world_execution": True,
                "same_python_namespace": True,
                "history_semantic_v3_match": True,
                "task_identity_checks": {"task_id": True, "query": True},
            },
        }
        for condition in conditions
    ]
    manifest = {"condition_count": len(conditions), "conditions": conditions}
    summary = {"passed": True, "condition_count": len(rows)}
    result = validate_formal_rows(rows, manifest, summary)
    assert result["passed"]
    rows[0]["live_worker"]["same_world_execution"] = False
    with pytest.raises(ValueError, match="clean_corpus_behavioral_audit_infrastructure_invalid"):
        validate_formal_rows(rows, manifest, summary)


def test_clean_preflight_rejects_missing_required_control() -> None:
    _validate_required_condition_coverage(
        {
            "missing_conditions": [
                {"condition_name": "C6_alternate_same_signature", "required": False}
            ]
        }
    )
    with pytest.raises(RuntimeError, match="required C0-C5"):
        _validate_required_condition_coverage(
            {
                "missing_conditions": [
                    {"condition_name": "C3_hard_negative", "required": True}
                ]
            }
        )
