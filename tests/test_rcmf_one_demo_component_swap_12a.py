from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    StandardFieldCrossAttentionReader,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    CONDITIONS,
    compile_field_pair,
    condition_order_for_task,
    condition_parts,
    field_rebuild_errors,
    load_writer_reader_package,
    permutation_from_rows,
    remove_restore_error,
    select_leakage_safe_memory_ids,
)
from scripts.analyze_rcmf_one_demo_component_swap_12a import (
    classify,
    expected_app_from_task_message,
    trace_mechanism_counts,
)
from scripts.run_rcmf_one_demo_component_swap_diagnostics_12a import spearman
from scripts.run_rcmf_one_demo_component_swap_12a import parse_args


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_locked_old_selector_sha_is_exact_sha256() -> None:
    config = load_config(
        Path("configs/benchmark/stage_c_rcmf_one_demo_component_swap_12a.yaml")
    )
    actual = config.raw["stage_c_12a"]["selectors"]["old"]["ensemble_sha256"]
    assert actual == "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    assert len(actual) == 64


def test_no_generation_selector_spearman_tracks_rank_order() -> None:
    left = torch.tensor([0.1, 0.4, 0.2, 0.3])
    assert spearman(left, left) == pytest.approx(1.0)
    assert spearman(left, -left) == pytest.approx(-1.0)


def test_runner_exposes_separate_manifest_and_execution_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "runner",
            "--artifact-dir",
            "artifacts",
            "--phase",
            "smoke",
            "--attempt-id",
            "attempt",
            "--source-head",
            "execution",
            "--manifest-source-head",
            "manifest",
        ],
    )
    arguments = parse_args()
    assert arguments.source_head == "execution"
    assert arguments.manifest_source_head == "manifest"


def test_coadaptation_requires_positive_native_specificity() -> None:
    point = {
        "selector_old_WR": 0.375,
        "selector_fresh_WR": -0.125,
        "M_selector": 0.125,
        "WR_old_selector": 0.25,
        "WR_fresh_selector": -0.25,
        "M_WR": 0.0,
        "interaction": 0.5,
        "Delta_OO": 0.0,
        "Delta_OF": -0.25,
        "Delta_FO": -0.375,
        "Delta_FF": -0.125,
    }
    loo = {
        key: {"deleting_one_task_changes_direction": value}
        for key, value in {
            "M_selector": True,
            "M_WR": True,
            "interaction": False,
        }.items()
    }

    result = classify(point, loo)

    assert result["decision"] == "INCONCLUSIVE"
    assert result["interaction_loo_stable"] is True
    assert result["native_oo_specificity_retained"] is False


def test_trace_mechanism_counts_are_grounded_in_executed_steps() -> None:
    row = {
        "steps": [
            {
                "current_task_message": "Play a song on Spotify.",
                "exact_executed_code": "apis.api_docs.show_api_doc(app_name='spotify')",
                "complete_environment_observation": "ok",
            },
            {
                "current_task_message": "Play a song on Spotify.",
                "exact_executed_code": "apis.file_system.show(path='x')",
                "complete_environment_observation": "No API named 'show' found",
            },
            {
                "current_task_message": "Play a song on Spotify.",
                "exact_executed_code": "apis.supervisor.complete_task()",
                "complete_environment_observation": "done",
            },
        ]
    }

    assert expected_app_from_task_message("Use my Spotify playlists") == "spotify"
    assert trace_mechanism_counts(row) == {
        "documentation_call_steps": 1,
        "invalid_api_steps": 1,
        "wrong_app_family_steps": 1,
        "executed_completion_steps": 1,
    }


def test_condition_cells_and_counterbalanced_order() -> None:
    assert len(CONDITIONS) == 8
    orders = [condition_order_for_task(index) for index in range(8)]
    assert all(set(order) == set(CONDITIONS) for order in orders)
    for position in range(8):
        assert {order[position] for order in orders} == set(CONDITIONS)
    assert condition_parts("OF-S") == ("OF", "S", "old", "fresh")
    assert condition_parts("FO-C") == ("FO", "C", "fresh", "old")


def test_common_shuffle_is_bijection_and_preserves_payload_multiset() -> None:
    ids = ["a", "b", "c", "d"]
    rows = [
        {"key_transition_id": "a", "payload_transition_id": "b"},
        {"key_transition_id": "b", "payload_transition_id": "c"},
        {"key_transition_id": "c", "payload_transition_id": "d"},
        {"key_transition_id": "d", "payload_transition_id": "a"},
    ]
    permutation = permutation_from_rows(ids, rows)
    payloads = torch.arange(4 * 8 * 256, dtype=torch.float32).reshape(4, 8, 256)
    assert sorted(permutation.tolist()) == list(range(4))
    assert torch.equal(
        payloads.flatten(start_dim=1).sort(dim=0).values,
        payloads[permutation].flatten(start_dim=1).sort(dim=0).values,
    )


def test_shuffle_with_fixed_point_is_rejected() -> None:
    rows = [
        {"key_transition_id": "a", "payload_transition_id": "a"},
        {"key_transition_id": "b", "payload_transition_id": "b"},
    ]
    with pytest.raises(ValueError, match="fixed point"):
        permutation_from_rows(["a", "b"], rows)


def test_cross_field_assembly_rebuild_and_remove_restore() -> None:
    generator = torch.Generator().manual_seed(25101)
    keys = torch.randn(4, 960, generator=generator)
    old_payloads = torch.randn(4, 8, 256, generator=generator)
    fresh_payloads = torch.randn(4, 8, 256, generator=generator)
    rho = torch.tensor([0.25, 0.25, 0.5, 1.0])
    permutation = torch.tensor([1, 2, 3, 0])
    oo = compile_field_pair(
        keys=keys, payloads=old_payloads, rho=rho, permutation=permutation
    )
    of = compile_field_pair(
        keys=keys, payloads=fresh_payloads, rho=rho, permutation=permutation
    )
    assert tuple(oo["A"].shape) == (960, 8, 256)
    assert tuple(oo["B"].shape) == (8, 256)
    assert not torch.equal(oo["A"], of["A"])
    assert max(field_rebuild_errors(fields=oo, historical=oo).values()) == 0.0
    errors = remove_restore_error(
        fields=oo,
        key=keys[0],
        payload=old_payloads[0],
        shuffled_payload=old_payloads[permutation[0]],
        rho=float(rho[0]),
    )
    assert max(errors.values()) <= 1.0e-5


def test_401_memory_exclusion_uses_parent_tasks() -> None:
    ids = ["m1", "m2", "m3"]
    parents = {"m1": "train-a", "m2": "heldout-a", "m3": "train-b"}
    selected = select_leakage_safe_memory_ids(
        ordered_transition_ids=ids,
        parent_task_by_transition=parents,
        train_task_ids=["train-a", "train-b"],
        heldout_task_ids=["heldout-a"],
    )
    assert selected == ["m1", "m3"]


def test_writer_reader_package_strict_sha_and_strict_load(tmp_path: Path) -> None:
    writer = AlignedTransitionWriter()
    reader = StandardFieldCrossAttentionReader()
    checkpoint = {
        "writer_state_dict": writer.state_dict(),
        "reader_state_dict": reader.state_dict(),
        "writer_sha256": "writer-test",
        "reader_sha256": "reader-test",
    }
    path = tmp_path / "checkpoint.pt"
    torch.save(checkpoint, path)
    expected = file_sha256(path)
    loaded_writer, loaded_reader, identity = load_writer_reader_package(
        name="test",
        checkpoint_path=path,
        expected_checkpoint_sha256=expected,
    )
    assert identity.checkpoint_sha256 == expected
    assert not any(parameter.requires_grad for parameter in loaded_writer.parameters())
    assert not any(parameter.requires_grad for parameter in loaded_reader.parameters())
    with pytest.raises(ValueError, match="SHA differs"):
        load_writer_reader_package(
            name="test", checkpoint_path=path, expected_checkpoint_sha256="0" * 64
        )
