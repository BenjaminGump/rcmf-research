from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_appworld_testnormal_final_13a import (
    CONDITIONS,
    PAIRED_COMPARISONS,
    build_condition_manifest,
    condition_parts,
    ordered_sha256,
    paired_bootstrap,
    validate_field_payload,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    RCMFFieldRecord,
    ReversibleRCMFField,
)
from scripts.benchmark_rcmf_appworld_testnormal_efficiency_13a import (
    compilation_phase,
    greedy_profile_once,
    timed_cuda,
    timing_summary,
)
from scripts.export_rcmf_appworld_testnormal_audit_13a import (
    CLAIMS_BOUNDARY,
    latex_values,
)
from scripts.export_rcmf_appworld_testnormal_stop_audit_13a import (
    set_repr_order_only,
)
from scripts.run_rcmf_appworld_testnormal_final_13a import FinalTestRuntime, smoke
from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task


CONFIG = Path(
    "configs/benchmark/stage_c_rcmf_appworld_testnormal_final_13a.yaml"
)


def test_locked_five_condition_contract_and_primary_role() -> None:
    config = load_config(CONFIG).raw["stage_c_13a"]
    assert list(CONDITIONS) == [
        "B0",
        "BEST-C",
        "BEST-S",
        "FULL1D-C",
        "FULL1D-S",
    ]
    assert config["test_normal"]["expected_task_count"] == 168
    assert config["test_normal"]["expected_condition_count"] == 840
    assert config["packages"]["BEST"]["scientific_role"] == (
        "preregistered_primary_method"
    )
    assert config["packages"]["FULL1D"]["scientific_role"] == (
        "frozen_secondary_ablation"
    )
    assert len(PAIRED_COMPARISONS) == 5


def test_all_frozen_hashes_are_exact_and_full1d_epoch_two_is_forbidden() -> None:
    config = load_config(CONFIG).raw["stage_c_13a"]
    assert config["packages"]["BEST"]["selector_ensemble_sha256"] == (
        "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"
    )
    assert len(config["packages"]["BEST"]["selector_ensemble_sha256"]) == 64
    assert config["packages"]["BEST"]["writer_reader_checkpoint_sha256"] == (
        "d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1"
    )
    assert config["packages"]["BEST"]["deployment_field_sha256"] == (
        "5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e"
    )
    assert config["packages"]["FULL1D"]["selector_ensemble_sha256"] == (
        "c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300"
    )
    assert config["packages"]["FULL1D"]["writer_reader_checkpoint_sha256"] == (
        "357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764"
    )
    assert config["packages"]["FULL1D"]["forbidden_epoch_02_sha256"] == (
        "d8c82a331e66cb8d7a4fe9504dc34dd75cb3534c518e63dc3e08f1fc3a3fa4f1"
    )
    assert config["packages"]["FULL1D"]["deployment_field_sha256"] == (
        "f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169"
    )


def test_official_ordered_manifest_hash_is_locked() -> None:
    config = load_config(CONFIG).raw["stage_c_13a"]
    assert config["test_normal"]["ordered_task_ids_sha256"] == (
        "990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83"
    )
    assert ordered_sha256(["a", "b"]) == ordered_sha256(["a", "b"])
    assert ordered_sha256(["a", "b"]) != ordered_sha256(["b", "a"])


def test_condition_manifest_has_exactly_840_unique_rows() -> None:
    tasks = [f"task-{index:03d}" for index in range(168)]
    manifest = build_condition_manifest(
        run_uuid="run", task_ids=tasks, package_manifest_sha256="package"
    )
    assert manifest["logical_condition_count"] == 840
    assert len(manifest["rows"]) == 840
    assert len(
        {(row["task_id"], row["condition"]) for row in manifest["rows"]}
    ) == 840
    assert all(not row["runtime_memory_retrieval"] for row in manifest["rows"])
    assert all(
        not row["runtime_per_memory_scoring"] for row in manifest["rows"]
    )
    assert all(
        not row["student_prompt_contains_raw_memory"] for row in manifest["rows"]
    )


def test_condition_parts_keep_bare_outside_memory_packages() -> None:
    assert condition_parts("B0") == (None, "zero")
    assert condition_parts("BEST-C") == ("BEST", "correct")
    assert condition_parts("BEST-S") == ("BEST", "key_payload_shuffle")
    assert condition_parts("FULL1D-C") == ("FULL1D", "correct")
    with pytest.raises(ValueError):
        condition_parts("BEST-X")


def test_field_identity_requires_exact_shapes_and_frozen_checkpoint() -> None:
    generator = torch.Generator().manual_seed(25101)
    payload = {
        "A": torch.randn(960, 8, 256, generator=generator),
        "B": torch.randn(8, 256, generator=generator),
        "shuffled_A": torch.randn(960, 8, 256, generator=generator),
        "shuffled_B": torch.randn(8, 256, generator=generator),
        "memory_count": 499,
        "memory_ids": [f"m-{index}" for index in range(499)],
        "checkpoint_sha256": "checkpoint",
    }
    identity = validate_field_payload(
        payload, expected_checkpoint_sha256="checkpoint"
    )
    assert identity["checks"]["finite"]
    assert identity["active_field_bytes"] == (
        payload["A"].numel() + payload["B"].numel()
    ) * 4
    payload["checkpoint_sha256"] = "wrong"
    with pytest.raises(ValueError):
        validate_field_payload(payload, expected_checkpoint_sha256="checkpoint")


def test_resource_metrics_are_default_off_for_old_runner() -> None:
    parameter = inspect.signature(_run_task).parameters["collect_resource_metrics"]
    assert parameter.default is False
    source = inspect.getsource(_run_task)
    assert "torch.cuda.reset_peak_memory_stats" in source
    assert "if collect_resource_metrics" in source


def test_bare_runtime_has_no_field_read_path() -> None:
    source = inspect.getsource(FinalTestRuntime.read)
    assert "Bare condition has no field read" in source
    assert "runtime_memory_scan" in source
    assert "runtime_per_memory_scoring" in source


def test_timing_helper_does_not_change_cpu_operation_result() -> None:
    value, elapsed = timed_cuda(torch.device("cpu"), lambda: 17)
    assert value == 17
    assert elapsed >= 0.0
    row = timing_summary([1.0, 2.0, 3.0, 4.0])
    assert row["median"] == 2.5
    assert row["p95"] == pytest.approx(3.85)


def test_formal_runtime_gate_respects_formal_before_efficiency_order() -> None:
    config = load_config(CONFIG).raw["stage_c_13a"]
    auxiliary = config["runtime"]["auxiliary_estimate"]
    assert auxiliary["phase_b_runs_after_formal"] is True
    source = inspect.getsource(smoke)
    assert "efficiency_pilot.json" not in source
    assert "efficiency_microbenchmark_estimate" in source


def test_raw_reencoding_keeps_identity_hard_and_numeric_drift_diagnostic() -> None:
    source = inspect.getsource(compilation_phase)
    assert "Raw transition text/token provenance differs" in source
    assert "raw_reencoding_exact_cache_match_at_1e_5" in source
    assert "Raw transition re-encoding differs from frozen cache" not in source


def test_ttft_profiler_initializes_generation_cache_position() -> None:
    source = inspect.getsource(greedy_profile_once)
    initialization = "_get_initial_cache_position"
    update = "_update_model_kwargs_for_generation"
    assert initialization in source
    assert source.index(initialization) < source.index(update)


def test_reversible_field_keeps_fixed_shape_and_round_trips() -> None:
    generator = torch.Generator().manual_seed(25101)
    field = ReversibleRCMFField()
    record = RCMFFieldRecord(
        memory_id="m",
        parent_id="p",
        parent_task_id="t",
        key=torch.randn(960, generator=generator),
        payload=torch.randn(8, 256, generator=generator),
        rho=0.25,
    )
    field.add_memory_fast(record)
    A, B = field.A.clone(), field.B.clone()
    field.remove_memory_fast("m")
    field.add_memory_fast(record)
    assert tuple(field.A.shape) == (960, 8, 256)
    assert tuple(field.B.shape) == (8, 256)
    assert torch.allclose(field.A, A, atol=1.0e-6, rtol=0.0)
    assert torch.equal(field.B, B)


def test_paired_bootstrap_preserves_paired_direction() -> None:
    left = [True, True, False, True, False, True, False, True]
    right = [False, True, False, False, False, True, False, False]
    result = paired_bootstrap(left, right, replicates=1_000)
    assert result["observed"] == pytest.approx(3 / 8)
    assert result["analysis_seed"] == 25101
    assert result["lower_95"] <= result["observed"] <= result["upper_95"]


def test_claims_and_latex_keep_unsupported_work_explicit() -> None:
    assert "Test-Normal was partially exposed" in CLAIMS_BOUNDARY
    assert "raw ledger" in CLAIMS_BOUNDARY
    values = {
        "appworld_test_normal": {
            condition: {"success_count": 1, "task_count": 168}
            for condition in CONDITIONS
        },
        "unsupported": {
            "ALFWorld": "NOT_RUN",
            "behavioral_record_deletion": "NOT_RUN",
        },
    }
    latex = latex_values(values)
    assert "1/168" in latex
    assert latex.count("NOT\\_RUN") == 2


def test_smoke_stop_classifies_only_set_repr_ordering() -> None:
    assert set_repr_order_only("items: {'a', 'b'}", "items: {'b', 'a'}")
    assert not set_repr_order_only("items: {'a'}", "items: {'b'}")
    assert not set_repr_order_only("items: ['a', 'b']", "items: ['b', 'a']")
