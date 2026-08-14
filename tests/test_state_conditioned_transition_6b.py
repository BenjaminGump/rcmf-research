from __future__ import annotations

import json
from pathlib import Path
import time

import pytest
import torch

from rcmf.training.state_conditioned_transition_6b import (
    CELL_A,
    CELL_B,
    CELL_C,
    CELL_D,
    AttemptLedger,
    FactorizedTransitionField,
    FactorizedTransitionProgram,
    UtilityPredictor,
    build_grouped_cv_manifest,
    build_two_axis_rows,
    deterministic_parent_split,
    factorized_field_algebra_validation,
    initialize_or_validate_run_manifest,
    project_program_to_ratio,
    representation_interaction_gate,
)
from scripts.prepare_state_conditioned_transition_6b import _resume_provenance


def _panel() -> list[dict[str, object]]:
    rows = []
    for parent in range(37):
        rows.append(
            {
                "transition_id": f"m-{parent}-t-0",
                "parent_memory_id": f"m-{parent}",
                "parent_task_id": f"task-{parent}",
                "step_index": 1,
                "step_count": 2,
                "apps": [f"app-{parent % 3}"],
                "action_type": "api_read_or_login",
            }
        )
    return rows


def _query_manifest() -> dict[str, object]:
    return {
        "query_rows": [
            {
                "state_example_id": "s-train",
                "example_index": 0,
                "task_id": "query-train",
                "apps": ["app-0"],
                "step_id": 1,
                "split": "train",
            },
            {
                "state_example_id": "s-heldout",
                "example_index": 1,
                "task_id": "query-heldout",
                "apps": ["app-1"],
                "step_id": 2,
                "split": "validation",
            },
        ]
    }


def _teacher_rows(
    panel: list[dict[str, object]], split: dict[str, object]
) -> list[dict[str, object]]:
    rows = []
    for state_id in ("s-train", "s-heldout"):
        for transition in panel:
            transition_id = str(transition["transition_id"])
            parent_id = str(transition["parent_memory_id"])
            utility = 0.2 if int(parent_id.split("-")[-1]) % 2 == 0 else -0.2
            rows.append(
                {
                    "pair_id": f"{state_id}::transition::{transition_id}",
                    "state_example_id": state_id,
                    "transition_id": transition_id,
                    "valid_for_loss": True,
                    "text_utility": utility,
                    "L0": 1.0,
                    "Lj_transition": 1.0 - utility,
                    "state_prompt_tokens": 10,
                    "target_tokens": 2,
                    "transition_step_bucket": "early",
                    "target_sha256": "target",
                    "target_token_sha256": "target-token",
                    "transition_content_sha256": f"content-{transition_id}",
                    "base_prompt_sha256": f"prompt-{state_id}",
                }
            )
    rows.append(
        {
            **rows[0],
            "pair_id": "masked-over-context",
            "valid_for_loss": False,
        }
    )
    return rows


def test_parent_split_and_two_axis_cells_are_exact() -> None:
    panel = _panel()
    split = deterministic_parent_split(panel, seed=18)
    assert split["train_parent_count"] == 29
    assert split["heldout_parent_count"] == 8
    assert not set(split["train_parent_ids"]).intersection(split["heldout_parent_ids"])

    rows = build_two_axis_rows(
        teacher_rows=_teacher_rows(panel, split),
        panel_rows=panel,
        query_manifest=_query_manifest(),
        parent_split=split,
    )
    assert len(rows) == 74
    counts = {cell: sum(row["cell"] == cell for row in rows) for cell in (CELL_A, CELL_B, CELL_C, CELL_D)}
    assert counts == {CELL_A: 29, CELL_B: 29, CELL_C: 8, CELL_D: 8}
    assert all(row["valid_for_loss"] and not row["truncated"] for row in rows)


def test_grouped_cv_holds_out_both_axes() -> None:
    rows = []
    for task in range(10):
        for parent in range(15):
            rows.append(
                {
                    "pair_id": f"t{task}-p{parent}",
                    "state_task_id": f"task-{task}",
                    "transition_parent_id": f"parent-{parent}",
                }
            )
    manifest = build_grouped_cv_manifest(rows, folds=5, seed=19)
    row_by_id = {row["pair_id"]: row for row in rows}
    for fold in manifest["folds"]:
        train = [row_by_id[value] for value in fold["train_pair_ids"]]
        validation = [row_by_id[value] for value in fold["validation_pair_ids"]]
        assert {row["state_task_id"] for row in train}.isdisjoint(
            {row["state_task_id"] for row in validation}
        )
        assert {row["transition_parent_id"] for row in train}.isdisjoint(
            {row["transition_parent_id"] for row in validation}
        )


def test_utility_predictor_shapes_and_signed_interaction() -> None:
    state = torch.randn(7, 12)
    transition = torch.randn(7, 12)
    for kind in UtilityPredictor.KINDS:
        model = UtilityPredictor(
            kind, state_dim=12, transition_dim=12, hidden_dim=16, interaction_dim=8
        )
        assert model(state, transition).shape == (7,)
    bilinear = UtilityPredictor(
        "signed_bilinear", state_dim=12, transition_dim=12, hidden_dim=16, interaction_dim=8
    )
    correct = bilinear(state, transition)
    shuffled = bilinear(state.flip(0), transition)
    assert not torch.allclose(correct, shuffled)


def _metric(spearman: float, sign: float, huber: float) -> dict[str, float]:
    return {
        "u_text_vs_prediction_spearman": spearman,
        "positive_negative_sign_agreement": sign,
        "huber": huber,
    }


def test_representation_gate_requires_bilinear_and_both_shuffles() -> None:
    results = {
        "state_only": {"correct": _metric(0.10, 0.55, 0.10)},
        "transition_only": {"correct": _metric(0.08, 0.55, 0.11)},
        "concat_mlp": {
            "correct": _metric(0.30, 0.70, 0.06),
            "shuffled_state": _metric(0.10, 0.55, 0.10),
            "shuffled_transition": _metric(0.12, 0.56, 0.10),
        },
        "signed_bilinear": {
            "correct": _metric(0.27, 0.68, 0.07),
            "shuffled_state": _metric(0.10, 0.55, 0.10),
            "shuffled_transition": _metric(0.11, 0.56, 0.10),
        },
    }
    gate = representation_interaction_gate(model_results=results)
    assert gate["proceed_to_behavioral_training"]
    results["signed_bilinear"]["shuffled_transition"] = _metric(0.25, 0.65, 0.08)
    gate = representation_interaction_gate(model_results=results)
    assert gate["branch"] == "field_compatible_interaction_factorization_insufficient"


def test_factorized_program_is_zero_initialized_and_ratio_projected() -> None:
    model = FactorizedTransitionProgram(
        state_dim=16, transition_dim=16, controller_rank=4, program_dim=8, hidden_dim=12
    )
    state = torch.randn(5, 16)
    transition = torch.randn(5, 16)
    components = model.components(state, transition)
    assert components["basis"].shape == (5, 4, 8)
    assert torch.equal(components["z"], torch.zeros_like(components["z"]))

    z = torch.tensor([[3.0, 4.0], [0.3, 0.4]])
    projected, ratios = project_program_to_ratio(
        z, maximum_delta_norm=torch.tensor([2.5, 1.0])
    )
    assert torch.allclose(projected.norm(dim=-1), torch.tensor([2.5, 0.5]))
    assert float(ratios.max()) <= 1.0


def test_factorized_field_explicit_and_reversible() -> None:
    report = factorized_field_algebra_validation(seed=22)
    assert report["passed"]
    assert all(report["checks"].values())

    field = FactorizedTransitionField(3, 2, 4)
    field.add("t", "p", torch.ones(3), torch.ones(4), torch.ones(2, 4))
    assert field.runtime_shapes == {"V0": [3, 4], "T": [3, 2, 4]}
    assert torch.allclose(
        field.read(torch.ones(3), torch.ones(2)),
        field.explicit_read(torch.ones(3), torch.ones(2)),
    )


def test_run_manifest_is_immutable_and_attempt_ledger_is_append_only(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run_manifest.json"
    manifest = initialize_or_validate_run_manifest(
        manifest_path,
        run_uuid="run-1",
        config_sha256="config",
        data_manifest_hashes={"data": "hash"},
        source_commit="commit",
        command_scope=["prepare", "cheap_gate"],
    )
    assert initialize_or_validate_run_manifest(
        manifest_path,
        run_uuid="run-1",
        config_sha256="config",
        data_manifest_hashes={"data": "hash"},
        source_commit="commit",
        command_scope=["prepare", "cheap_gate"],
    ) == manifest
    assert initialize_or_validate_run_manifest(
        manifest_path,
        run_uuid="run-1",
        config_sha256="config",
        data_manifest_hashes={"data": "hash"},
        source_commit="compatible-fix-commit",
        command_scope=["prepare", "cheap_gate"],
    ) == manifest
    with pytest.raises(ValueError, match="config_sha256"):
        initialize_or_validate_run_manifest(
            manifest_path,
            run_uuid="run-1",
            config_sha256="changed",
            data_manifest_hashes={"data": "hash"},
            source_commit="commit",
            command_scope=["prepare", "cheap_gate"],
        )

    with AttemptLedger(
        tmp_path,
        run_uuid="run-1",
        attempt_id="attempt-001",
        phase="unit-test",
        command=["python", "test.py"],
        local_head="commit",
        github_head="commit",
        lambda_head="commit",
        tmux_session="test",
        config_sha256="config",
        data_manifest_hashes={"data": "hash"},
        heartbeat_interval_s=0.01,
    ) as attempt:
        attempt.progress(progress=1, latest_validated_checkpoint="checkpoint.pt")
        time.sleep(0.02)
    ledger = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert [row["event"] for row in ledger] == ["start", "end"]
    assert ledger[-1]["exit_code"] == 0
    assert ledger[-1]["latest_validated_checkpoint"] == "checkpoint.pt"
    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text())
    assert heartbeat["status"] == "completed"
    parent_attempt, checkpoint = _resume_provenance(tmp_path, "attempt-002")
    assert parent_attempt == "attempt-001"
    assert checkpoint == "checkpoint.pt"
    with pytest.raises(ValueError, match="already exists"):
        _resume_provenance(tmp_path, "attempt-001")
