from __future__ import annotations

from pathlib import Path

import pytest

from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.checkpoint_policy import (
    CHECKPOINT_POINTER_VERSION,
    CHECKPOINT_RECORD_VERSION,
    TRAINING_UNIT_MANIFEST_VERSION,
    CheckpointIdentityError,
    TerminalCheckpointPolicy,
)
from rcmf.utils.serialization import sha256_file


IDENTITY = {
    "run_uuid": "portable-run",
    "source_commit": "a" * 40,
    "pipeline_config_sha256": "b" * 64,
    "data_manifest_sha256": "c" * 64,
    "training_unit_manifest_sha256": "d" * 64,
}


def _training_units(epochs: int, count: int = 7) -> dict[str, object]:
    row: dict[str, object] = {
        "format": TRAINING_UNIT_MANIFEST_VERSION,
        "identity": IDENTITY,
        "training_epochs": epochs,
        "units_per_epoch": count,
        "unit_ids": [f"unit-{index}" for index in range(count)],
    }
    row["manifest_sha256"] = content_sha256(row)
    return row


def _checkpoint(tmp_path: Path, epoch: int, units_per_epoch: int = 7, **overrides: object) -> dict[str, object]:
    path = tmp_path / f"epoch_{epoch:02d}.pt"
    path.write_bytes(f"checkpoint-{epoch}".encode())
    row: dict[str, object] = {
        "format": CHECKPOINT_RECORD_VERSION,
        "epoch": epoch,
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "complete": True,
        "finite": True,
        "identity": IDENTITY,
        "completed_units": epoch * units_per_epoch,
        "terminal_loss": 0.1,
    }
    row.update(overrides)
    return row


def _pointer(terminal: dict[str, object]) -> dict[str, object]:
    row: dict[str, object] = {
        "format": CHECKPOINT_POINTER_VERSION,
        "identity": IDENTITY,
        "epoch": terminal["epoch"],
        "completed_units": terminal["completed_units"],
        "path": terminal["path"],
        "sha256": terminal["sha256"],
    }
    row["pointer_sha256"] = content_sha256(row)
    return row


def _resolve(tmp_path: Path, epochs: int, *, mutate: dict[str, object] | None = None) -> dict[str, object]:
    rows = [_checkpoint(tmp_path, epoch) for epoch in range(1, epochs + 1)]
    if mutate:
        rows[-1].update(mutate)
    return TerminalCheckpointPolicy(epochs).resolve(
        rows,
        expected_identity=IDENTITY,
        training_unit_manifest=_training_units(epochs),
        checkpoint_pointer=_pointer(rows[-1]),
    )


@pytest.mark.parametrize("epochs", [1, 2, 4])
def test_terminal_checkpoint_uses_externally_owned_final_epoch(tmp_path: Path, epochs: int) -> None:
    selected = _resolve(tmp_path, epochs)
    assert selected["deployment_epoch"] == epochs
    assert selected["completed_units"] == epochs * 7
    assert not selected["metric_based_selection"]
    assert not selected["fallback_permitted"]


def test_metrics_cannot_change_terminal_checkpoint(tmp_path: Path) -> None:
    first = _resolve(tmp_path, 2, mutate={"heldout_score": 999.0, "dev_score": -999.0})
    second = _resolve(tmp_path, 2, mutate={"heldout_score": -999.0, "dev_score": 999.0})
    assert first["deployment_epoch"] == second["deployment_epoch"] == 2


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"complete": False}, "incomplete"),
        ({"finite": False}, "nonfinite"),
        ({"terminal_loss": float("nan")}, "loss is nonfinite"),
        ({"completed_units": 1}, "completed-unit count differs"),
        ({"format": "old"}, "format differs"),
    ],
)
def test_invalid_terminal_checkpoint_fails_closed(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(CheckpointIdentityError, match=match):
        _resolve(tmp_path, 2, mutate=overrides)


def test_missing_extra_or_corrupt_epochs_fail_without_fallback(tmp_path: Path) -> None:
    units = _training_units(2)
    first = _checkpoint(tmp_path, 1)
    with pytest.raises(CheckpointIdentityError, match="missing"):
        TerminalCheckpointPolicy(2).resolve(
            [first], expected_identity=IDENTITY, training_unit_manifest=units, checkpoint_pointer={}
        )
    extra = _checkpoint(tmp_path, 3)
    with pytest.raises(CheckpointIdentityError, match="outside policy"):
        TerminalCheckpointPolicy(2).resolve(
            [first, _checkpoint(tmp_path, 2), extra],
            expected_identity=IDENTITY,
            training_unit_manifest=units,
            checkpoint_pointer={},
        )
    terminal = _checkpoint(tmp_path, 2)
    Path(str(terminal["path"])).write_bytes(b"corrupt")
    with pytest.raises(CheckpointIdentityError, match="content hash differs"):
        TerminalCheckpointPolicy(2).resolve(
            [first, terminal],
            expected_identity=IDENTITY,
            training_unit_manifest=units,
            checkpoint_pointer=_pointer(terminal),
        )


def test_external_unit_manifest_and_pointer_mutations_fail(tmp_path: Path) -> None:
    rows = [_checkpoint(tmp_path, 1), _checkpoint(tmp_path, 2)]
    units = _training_units(2)
    units["units_per_epoch"] = 99
    with pytest.raises(CheckpointIdentityError, match="manifest hash differs"):
        TerminalCheckpointPolicy(2).resolve(
            rows,
            expected_identity=IDENTITY,
            training_unit_manifest=units,
            checkpoint_pointer=_pointer(rows[-1]),
        )
    pointer = _pointer(rows[-1])
    pointer["sha256"] = "e" * 64
    with pytest.raises(CheckpointIdentityError, match="pointer record hash differs"):
        TerminalCheckpointPolicy(2).resolve(
            rows,
            expected_identity=IDENTITY,
            training_unit_manifest=_training_units(2),
            checkpoint_pointer=pointer,
        )


def test_checkpoint_identity_mutation_fails_closed(tmp_path: Path) -> None:
    row = _checkpoint(tmp_path, 1, identity={**IDENTITY, "run_uuid": "other"})
    with pytest.raises(CheckpointIdentityError, match="run_uuid"):
        TerminalCheckpointPolicy(1).resolve(
            [row],
            expected_identity=IDENTITY,
            training_unit_manifest=_training_units(1),
            checkpoint_pointer=_pointer(row),
        )
