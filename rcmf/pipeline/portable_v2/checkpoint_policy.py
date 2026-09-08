from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from rcmf.utils.serialization import sha256_file


CHECKPOINT_POLICY_NAME = "terminal_completed_epoch"
CHECKPOINT_RECORD_VERSION = "rcmf_portable_checkpoint_v2"


class CheckpointIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class TerminalCheckpointPolicy:
    training_epochs: int

    def __post_init__(self) -> None:
        if self.training_epochs <= 0:
            raise ValueError("training_epochs must be positive")

    @property
    def deployment_epoch(self) -> int:
        return self.training_epochs

    def resolve(
        self,
        checkpoint_records: Sequence[Mapping[str, Any]],
        *,
        expected_identity: Mapping[str, str],
    ) -> dict[str, Any]:
        by_epoch: dict[int, Mapping[str, Any]] = {}
        for row in checkpoint_records:
            epoch = int(row.get("epoch", 0))
            if epoch in by_epoch:
                raise CheckpointIdentityError(f"duplicate checkpoint record for epoch {epoch}")
            by_epoch[epoch] = row
        terminal = by_epoch.get(self.deployment_epoch)
        if terminal is None:
            raise CheckpointIdentityError(
                f"terminal checkpoint for configured epoch {self.deployment_epoch} is missing"
            )
        return self._validate_terminal(terminal, expected_identity=expected_identity)

    def _validate_terminal(
        self, row: Mapping[str, Any], *, expected_identity: Mapping[str, str]
    ) -> dict[str, Any]:
        if row.get("format") != CHECKPOINT_RECORD_VERSION:
            raise CheckpointIdentityError("terminal checkpoint format differs")
        if int(row.get("epoch", 0)) != self.deployment_epoch:
            raise CheckpointIdentityError("terminal checkpoint epoch differs")
        if not bool(row.get("complete")):
            raise CheckpointIdentityError("terminal checkpoint is incomplete")
        if not bool(row.get("finite")):
            raise CheckpointIdentityError("terminal checkpoint is nonfinite")
        path = Path(str(row.get("path", ""))).expanduser().resolve(strict=False)
        if not path.is_file():
            raise CheckpointIdentityError(f"terminal checkpoint does not exist: {path}")
        recorded_sha = str(row.get("sha256", ""))
        if len(recorded_sha) != 64 or sha256_file(path) != recorded_sha:
            raise CheckpointIdentityError("terminal checkpoint content hash differs")
        identity = row.get("identity")
        if not isinstance(identity, Mapping):
            raise CheckpointIdentityError("terminal checkpoint identity is missing")
        for key, expected in expected_identity.items():
            if not expected or str(identity.get(key, "")) != str(expected):
                raise CheckpointIdentityError(f"terminal checkpoint identity differs at {key}")
        completed_units = int(row.get("completed_units", -1))
        expected_units = int(row.get("expected_completed_units", -2))
        if completed_units < 0 or completed_units != expected_units:
            raise CheckpointIdentityError("terminal checkpoint completed-unit count differs")
        loss = float(row.get("terminal_loss", float("nan")))
        if not math.isfinite(loss):
            raise CheckpointIdentityError("terminal checkpoint loss is nonfinite")
        return {
            "checkpoint_policy": CHECKPOINT_POLICY_NAME,
            "deployment_epoch": self.deployment_epoch,
            "checkpoint": str(path),
            "checkpoint_sha256": recorded_sha,
            "identity": dict(identity),
            "metric_based_selection": False,
            "fallback_permitted": False,
            "passed": True,
        }
