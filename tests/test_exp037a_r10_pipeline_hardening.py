from __future__ import annotations

import copy
from pathlib import Path

import pytest
import torch

from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _selected_401_field,
    _strict_prior_stage_validation,
    _validate_deployment_field,
    formal_stage_output_paths,
    write_stage_manifest,
)
from rcmf.pipeline.manifests import stage_identity_payload
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.pipeline.validators import validate_stage_completion
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    _checkpoint_payload,
    _restore_checkpoint,
    _validated_checkpoint_pointer,
)
from scripts.audit_exp037a_pipeline_r10 import build_stage_map, device_load_audit


SOURCE = "a" * 40
RUN_UUID = "r10-test-run"
CONFIG_SHA = "b" * 64
CONTRACT_SHA = "c" * 64


def _components() -> tuple[torch.nn.Module, torch.nn.Module, torch.optim.Optimizer]:
    torch.manual_seed(25101)
    writer = torch.nn.Linear(4, 4)
    reader = torch.nn.Linear(4, 2)
    optimizer = torch.optim.AdamW(
        list(writer.parameters()) + list(reader.parameters()), lr=1.0e-3
    )
    return writer, reader, optimizer


def test_checkpoint_pointer_hash_is_verified_before_deserialization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkpoints"
    root.mkdir()
    checkpoint = root / "progress.pt"
    torch.save({"value": 1}, checkpoint)
    pointer = {
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha256_file(checkpoint),
        "completed_units": 1,
        "epoch": 1,
    }
    assert _validated_checkpoint_pointer(
        pointer, checkpoint_root=root, epoch_boundaries=(2, 4)
    ) == checkpoint.resolve()
    pointer["checkpoint_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="pointer SHA256 differs"):
        _validated_checkpoint_pointer(
            pointer, checkpoint_root=root, epoch_boundaries=(2, 4)
        )


def test_checkpoint_pointer_enforces_root_and_boundary_kind(tmp_path: Path) -> None:
    root = tmp_path / "checkpoints"
    root.mkdir()
    progress = root / "progress.pt"
    torch.save({"value": 1}, progress)
    pointer = {
        "checkpoint": str(progress),
        "checkpoint_sha256": sha256_file(progress),
        "completed_units": 2,
        "epoch": 1,
    }
    with pytest.raises(ValueError, match="kind differs"):
        _validated_checkpoint_pointer(
            pointer, checkpoint_root=root, epoch_boundaries=(2, 4)
        )
    outside = tmp_path / "epoch_01.pt"
    torch.save({"value": 1}, outside)
    pointer.update(
        checkpoint=str(outside), checkpoint_sha256=sha256_file(outside)
    )
    with pytest.raises(ValueError, match="escapes"):
        _validated_checkpoint_pointer(
            pointer, checkpoint_root=root, epoch_boundaries=(2, 4)
        )


def test_checkpoint_restore_rejects_module_state_hash_mismatch() -> None:
    writer, reader, optimizer = _components()
    payload = _checkpoint_payload(
        writer=writer,
        reader=reader,
        optimizer=optimizer,
        completed_units=1,
        unit_ids=["u0"],
        history=[],
        shuffle_nll={},
        source_hashes={"source": "d" * 64},
    )
    payload = copy.deepcopy(payload)
    key = next(iter(payload["writer_state_dict"]))
    payload["writer_state_dict"][key] = payload["writer_state_dict"][key] + 1
    restored_writer, restored_reader, restored_optimizer = _components()
    with pytest.raises(ValueError, match="module hashes differ"):
        _restore_checkpoint(
            payload=payload,
            writer=restored_writer,
            reader=restored_reader,
            optimizer=restored_optimizer,
            unit_ids=["u0"],
            source_hashes={"source": "d" * 64},
        )
    assert module_state_sha256(restored_writer) != payload["writer_sha256"]


def test_real_manifest_writer_seals_declared_scientific_artifacts(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    stage_dir = run_root / "stages/S00_environment_manifest"
    stage_dir.mkdir(parents=True)
    artifact = run_root / "preflight/environment_manifest.json"
    atomic_write_json(artifact, {"environment": "sealed"})
    identity = stage_identity_payload(
        source_commit=SOURCE,
        run_uuid=RUN_UUID,
        run_root=run_root,
        pipeline_config_sha256=CONFIG_SHA,
        contract_sha256=CONTRACT_SHA,
        stage_id=stage_dir.name,
        attempt_id="attempt-1",
        require_complete=True,
    )
    write_stage_manifest(
        stage_id=stage_dir.name,
        stage_dir=stage_dir,
        stage_identity=identity,
        arm="shared",
        prompt_profile=None,
        result={"passed": True},
        command=["test"],
        started_utc="2026-09-04T00:00:00+00:00",
        elapsed_seconds=0.0,
        run_root=run_root,
        output_artifacts=[artifact],
    )
    assert validate_stage_completion(
        stage_dir,
        SOURCE,
        expected_run_uuid=RUN_UUID,
        expected_pipeline_config_sha256=CONFIG_SHA,
        expected_contract_sha256=CONTRACT_SHA,
        expected_run_root=run_root,
    )["passed"]
    atomic_write_json(artifact, {"environment": "mutated"})
    assert not validate_stage_completion(
        stage_dir,
        SOURCE,
        expected_run_uuid=RUN_UUID,
        expected_pipeline_config_sha256=CONFIG_SHA,
        expected_contract_sha256=CONTRACT_SHA,
        expected_run_root=run_root,
    )["passed"]


def test_every_formal_stage_has_a_declared_output_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(
        "rcmf.benchmarks.appworld.reproducible_stages_14b._tree_files",
        lambda root: [root / "sealed-output"],
    )
    for stage in build_exp037a_stage_graph():
        outputs = formal_stage_output_paths(stage.stage_id, tmp_path)
        assert outputs, stage.stage_id


def test_strict_prior_stage_validation_rejects_foreign_run_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "run"
    stage_dir = run_root / "stages/S00_environment_manifest"
    stage_dir.mkdir(parents=True)
    artifact = run_root / "preflight/environment_manifest.json"
    atomic_write_json(artifact, {"environment": "sealed"})
    identity = stage_identity_payload(
        source_commit=SOURCE,
        run_uuid=RUN_UUID,
        run_root=run_root,
        pipeline_config_sha256=CONFIG_SHA,
        contract_sha256=CONTRACT_SHA,
        stage_id=stage_dir.name,
        attempt_id="attempt-1",
        require_complete=True,
    )
    write_stage_manifest(
        stage_id=stage_dir.name,
        stage_dir=stage_dir,
        stage_identity=identity,
        arm="shared",
        prompt_profile=None,
        result={"passed": True},
        command=["test"],
        started_utc="2026-09-04T00:00:00+00:00",
        elapsed_seconds=0.0,
        run_root=run_root,
        output_artifacts=[artifact],
    )
    monkeypatch.setenv("RCMF_PIPELINE_RUN_UUID", RUN_UUID)
    monkeypatch.setenv("RCMF_PIPELINE_RUN_ROOT", str(run_root))
    monkeypatch.setenv("RCMF_PIPELINE_CONFIG_SHA256", CONFIG_SHA)
    monkeypatch.setenv("RCMF_PIPELINE_CONTRACT_SHA256", CONTRACT_SHA)
    assert _strict_prior_stage_validation(
        stage_dir.name, run_root, SOURCE
    )["passed"]
    monkeypatch.setenv("RCMF_PIPELINE_RUN_UUID", "foreign-run")
    assert not _strict_prior_stage_validation(
        stage_dir.name, run_root, SOURCE
    )["passed"]


def _field_tensor(memory_count: int, checkpoint_sha: str) -> dict[str, object]:
    return {
        "memory_count": memory_count,
        "checkpoint_sha256": checkpoint_sha,
        "A": torch.zeros(960, 8, 256),
        "B": torch.zeros(8, 256),
    }


def test_selected_401_field_rejects_checkpoint_identity_mismatch(
    tmp_path: Path,
) -> None:
    target = tmp_path / "arms/3d"
    live = target / "heldout_validation/live_full_field"
    live.mkdir(parents=True)
    atomic_write_json(
        live / "checkpoint_selection.json",
        {"selected": {"epoch": 1, "checkpoint": "unused", "checkpoint_sha256": "a" * 64}},
    )
    field_root = live / "field_artifacts"
    field_root.mkdir()
    torch.save(_field_tensor(401, "b" * 64), field_root / "epoch_01_correct.pt")
    torch.save(
        _field_tensor(401, "a" * 64),
        field_root / "epoch_01_key_payload_shuffle.pt",
    )
    with pytest.raises(RuntimeError, match="401-memory field identity failed"):
        _selected_401_field(tmp_path, "3d")


def test_deployment_field_validation_checks_counts_ids_and_checkpoint(
    tmp_path: Path,
) -> None:
    target = tmp_path / "arms/3d"
    live = target / "heldout_validation/live_full_field"
    live.mkdir(parents=True)
    checkpoint = target / "joint_training/checkpoints/epoch_01.pt"
    checkpoint.parent.mkdir(parents=True)
    torch.save({"checkpoint": "sealed"}, checkpoint)
    checkpoint_sha = sha256_file(checkpoint)
    atomic_write_json(
        live / "checkpoint_selection.json",
        {
            "selected": {
                "epoch": 1,
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": checkpoint_sha,
            }
        },
    )
    source = target / "data/rcmf_source_cache.pt"
    source.parent.mkdir(parents=True)
    memory_ids = [f"m-{index:03d}" for index in range(499)]
    torch.save({"ordered_transition_ids": memory_ids}, source)
    deployment = target / "deployment_field/complete_37_task_field.pt"
    deployment.parent.mkdir(parents=True)
    payload = {
        **_field_tensor(499, checkpoint_sha),
        "memory_ids": memory_ids,
        "shuffled_A": torch.zeros(960, 8, 256),
        "shuffled_B": torch.zeros(8, 256),
    }
    torch.save(payload, deployment)
    report = target / "deployment_field/instant_add_report.json"
    atomic_write_json(
        report,
        {
            "deployment_field_sha256": sha256_file(deployment),
            "selected_checkpoint_sha256": checkpoint_sha,
            "field_memory_count_before": 401,
            "new_memory_count": 98,
            "field_memory_count_after": 499,
            "no_retraining_or_optimizer_step": True,
        },
    )
    assert _validate_deployment_field(tmp_path, "3d")["passed"]
    payload["shuffled_B"] = torch.zeros(7, 256)
    torch.save(payload, deployment)
    atomic_write_json(
        report,
        {
            "deployment_field_sha256": sha256_file(deployment),
            "selected_checkpoint_sha256": checkpoint_sha,
            "field_memory_count_before": 401,
            "new_memory_count": 98,
            "field_memory_count_after": 499,
            "no_retraining_or_optimizer_step": True,
        },
    )
    with pytest.raises(RuntimeError, match="Deployment field validation failed"):
        _validate_deployment_field(tmp_path, "3d")


def test_whole_pipeline_audit_covers_every_exact_stage_and_device_load() -> None:
    stages = build_exp037a_stage_graph()
    rows = build_stage_map(None)
    assert len(rows) == len(stages) == 60
    assert [row["stage_id"] for row in rows] == [stage.stage_id for stage in stages]
    valid_stage_ids = {stage.stage_id for stage in stages}
    for row in rows:
        for source in row["logical_inputs"]:
            producer = source["producer"]
            if producer in {"preflight", "launcher", "all executed stages", "D22 and O18/O19 when conditional arm ran"}:
                continue
            assert producer in valid_stage_ids
    assert all(row["classification"] != "UNCERTAIN" for row in device_load_audit())
