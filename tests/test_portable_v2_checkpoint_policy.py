from __future__ import annotations

from pathlib import Path

import pytest

from rcmf.pipeline.portable_v2.checkpoint_policy import (
    CHECKPOINT_RECORD_VERSION,
    CheckpointIdentityError,
    TerminalCheckpointPolicy,
)
from rcmf.utils.serialization import sha256_file


IDENTITY = {
    "run_uuid": "portable-run",
    "source_commit": "a" * 40,
    "pipeline_config_sha256": "b" * 64,
    "data_manifest_sha256": "c" * 64,
}


def _checkpoint(tmp_path: Path, epoch: int, **overrides: object) -> dict[str, object]:
    path = tmp_path / f"epoch_{epoch:02d}.pt"
    path.write_bytes(f"checkpoint-{epoch}".encode())
    row: dict[str, object] = {
        "format": CHECKPOINT_RECORD_VERSION,
        "epoch": epoch,
        "path": str(path),
        "sha256": sha256_file(path),
        "complete": True,
        "finite": True,
        "identity": IDENTITY,
        "completed_units": epoch * 7,
        "expected_completed_units": epoch * 7,
        "terminal_loss": 0.1,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("epochs", [1, 2, 4])
def test_terminal_checkpoint_policy_uses_final_configured_epoch(
    tmp_path: Path, epochs: int
) -> None:
    rows = [_checkpoint(tmp_path, epoch) for epoch in range(1, epochs + 1)]
    selected = TerminalCheckpointPolicy(epochs).resolve(rows, expected_identity=IDENTITY)
    assert selected["deployment_epoch"] == epochs
    assert selected["checkpoint_policy"] == "terminal_completed_epoch"
    assert not selected["metric_based_selection"]
    assert not selected["fallback_permitted"]


def test_metrics_cannot_change_terminal_checkpoint(tmp_path: Path) -> None:
    rows = [
        _checkpoint(tmp_path, 1, heldout_score=999.0, dev_score=999.0),
        _checkpoint(tmp_path, 2, heldout_score=-999.0, dev_score=-999.0),
    ]
    first = TerminalCheckpointPolicy(2).resolve(rows, expected_identity=IDENTITY)
    rows[0]["heldout_score"] = -1.0e20
    rows[1]["dev_score"] = 1.0e20
    second = TerminalCheckpointPolicy(2).resolve(rows, expected_identity=IDENTITY)
    assert first == second
    assert first["deployment_epoch"] == 2


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
    rows = [_checkpoint(tmp_path, 1), _checkpoint(tmp_path, 2, **overrides)]
    with pytest.raises(CheckpointIdentityError, match=match):
        TerminalCheckpointPolicy(2).resolve(rows, expected_identity=IDENTITY)


def test_no_fallback_when_terminal_missing_or_hash_invalid(tmp_path: Path) -> None:
    earlier = _checkpoint(tmp_path, 1)
    with pytest.raises(CheckpointIdentityError, match="configured epoch 2 is missing"):
        TerminalCheckpointPolicy(2).resolve([earlier], expected_identity=IDENTITY)

    terminal = _checkpoint(tmp_path, 2)
    Path(str(terminal["path"])).write_bytes(b"corrupt")
    with pytest.raises(CheckpointIdentityError, match="content hash differs"):
        TerminalCheckpointPolicy(2).resolve(
            [earlier, terminal], expected_identity=IDENTITY
        )


def test_checkpoint_identity_mutation_fails_closed(tmp_path: Path) -> None:
    row = _checkpoint(tmp_path, 1, identity={**IDENTITY, "run_uuid": "other"})
    with pytest.raises(CheckpointIdentityError, match="run_uuid"):
        TerminalCheckpointPolicy(1).resolve([row], expected_identity=IDENTITY)
