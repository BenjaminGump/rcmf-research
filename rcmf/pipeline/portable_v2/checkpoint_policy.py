from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from rcmf.pipeline.manifests import content_sha256
from rcmf.utils.serialization import sha256_file


CHECKPOINT_POLICY_NAME = "terminal_completed_epoch"
CHECKPOINT_RECORD_VERSION = "rcmf_portable_checkpoint_v2_1"
CHECKPOINT_POINTER_VERSION = "rcmf_portable_checkpoint_pointer_v2_1"
TRAINING_UNIT_MANIFEST_VERSION = "rcmf_portable_training_unit_manifest_v2_1"


class CheckpointIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class TerminalCheckpointPolicy:
    training_epochs: int
    require_intermediate_epochs: bool = True

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
        training_unit_manifest: Mapping[str, Any],
        checkpoint_pointer: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected_units = self._validate_training_units(
            training_unit_manifest, expected_identity=expected_identity
        )
        by_epoch: dict[int, Mapping[str, Any]] = {}
        for row in checkpoint_records:
            epoch = int(row.get("epoch", 0))
            if epoch <= 0 or epoch > self.training_epochs:
                raise CheckpointIdentityError(f"checkpoint epoch {epoch} is outside policy")
            if epoch in by_epoch:
                raise CheckpointIdentityError(f"duplicate checkpoint record for epoch {epoch}")
            by_epoch[epoch] = row
        if self.require_intermediate_epochs:
            missing = sorted(set(range(1, self.training_epochs + 1)) - set(by_epoch))
            if missing:
                raise CheckpointIdentityError(f"required checkpoint epochs are missing: {missing}")
        terminal = by_epoch.get(self.deployment_epoch)
        if terminal is None:
            raise CheckpointIdentityError(
                f"terminal checkpoint for configured epoch {self.deployment_epoch} is missing"
            )
        result = self._validate_terminal(
            terminal,
            expected_identity=expected_identity,
            expected_units=expected_units,
        )
        self._validate_pointer(
            checkpoint_pointer,
            expected_identity=expected_identity,
            terminal=result,
        )
        result["training_unit_manifest_sha256"] = training_unit_manifest["manifest_sha256"]
        result["checkpoint_pointer_sha256"] = checkpoint_pointer["pointer_sha256"]
        return result

    def _validate_training_units(
        self,
        manifest: Mapping[str, Any],
        *,
        expected_identity: Mapping[str, str],
    ) -> int:
        if manifest.get("format") != TRAINING_UNIT_MANIFEST_VERSION:
            raise CheckpointIdentityError("training-unit manifest format differs")
        recorded = manifest.get("manifest_sha256")
        body = dict(manifest)
        body.pop("manifest_sha256", None)
        if recorded != content_sha256(body):
            raise CheckpointIdentityError("training-unit manifest hash differs")
        identity = manifest.get("identity")
        self._validate_identity(identity, expected_identity, "training-unit manifest")
        if int(manifest.get("training_epochs", 0)) != self.training_epochs:
            raise CheckpointIdentityError("training-unit manifest epoch count differs")
        units_per_epoch = int(manifest.get("units_per_epoch", 0))
        unit_ids = manifest.get("unit_ids")
        if units_per_epoch <= 0 or not isinstance(unit_ids, Sequence) or isinstance(unit_ids, str):
            raise CheckpointIdentityError("training-unit manifest has invalid units")
        if len(unit_ids) != units_per_epoch or len(set(map(str, unit_ids))) != units_per_epoch:
            raise CheckpointIdentityError("training-unit IDs are incomplete or duplicated")
        return units_per_epoch * self.training_epochs

    def _validate_terminal(
        self,
        row: Mapping[str, Any],
        *,
        expected_identity: Mapping[str, str],
        expected_units: int,
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
        self._validate_identity(row.get("identity"), expected_identity, "terminal checkpoint")
        completed_units = int(row.get("completed_units", -1))
        if completed_units != expected_units:
            raise CheckpointIdentityError("terminal checkpoint completed-unit count differs")
        loss = float(row.get("terminal_loss", float("nan")))
        if not math.isfinite(loss):
            raise CheckpointIdentityError("terminal checkpoint loss is nonfinite")
        return {
            "checkpoint_policy": CHECKPOINT_POLICY_NAME,
            "deployment_epoch": self.deployment_epoch,
            "completed_units": completed_units,
            "checkpoint": str(path),
            "checkpoint_sha256": recorded_sha,
            "identity": dict(row["identity"]),
            "tensor_finiteness_validated_by_producer": True,
            "metric_based_selection": False,
            "fallback_permitted": False,
            "passed": True,
        }

    def _validate_pointer(
        self,
        pointer: Mapping[str, Any],
        *,
        expected_identity: Mapping[str, str],
        terminal: Mapping[str, Any],
    ) -> None:
        if pointer.get("format") != CHECKPOINT_POINTER_VERSION:
            raise CheckpointIdentityError("checkpoint pointer format differs")
        recorded = pointer.get("pointer_sha256")
        body = dict(pointer)
        body.pop("pointer_sha256", None)
        if recorded != content_sha256(body):
            raise CheckpointIdentityError("checkpoint pointer record hash differs")
        self._validate_identity(pointer.get("identity"), expected_identity, "checkpoint pointer")
        expected = {
            "epoch": terminal["deployment_epoch"],
            "completed_units": terminal["completed_units"],
            "path": terminal["checkpoint"],
            "sha256": terminal["checkpoint_sha256"],
        }
        for key, value in expected.items():
            if pointer.get(key) != value:
                raise CheckpointIdentityError(f"checkpoint pointer differs at {key}")

    @staticmethod
    def _validate_identity(
        identity: Any,
        expected_identity: Mapping[str, str],
        owner: str,
    ) -> None:
        if not isinstance(identity, Mapping):
            raise CheckpointIdentityError(f"{owner} identity is missing")
        for key, expected in expected_identity.items():
            if not expected or str(identity.get(key, "")) != str(expected):
                raise CheckpointIdentityError(f"{owner} identity differs at {key}")
