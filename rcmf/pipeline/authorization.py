from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rcmf.pipeline.contracts import PipelineContract
from rcmf.utils.serialization import sha256_file


def canonical_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _float_matches(value: Any, expected: float) -> bool:
    try:
        return float(value) == float(expected)
    except (TypeError, ValueError):
        return False


def _run_bound_checks(
    payload: Mapping[str, Any],
    contract: PipelineContract,
    *,
    run_root: str | Path,
    contract_sha256: str,
    pipeline_config_sha256: str,
) -> dict[str, bool]:
    return {
        "authorization_status": payload.get("authorization_status") == "AUTHORIZED",
        "authorized": payload.get("authorized") is True,
        "granted_by_user": payload.get("granted_by_user") is True,
        "full_pipeline_authorized": payload.get("full_pipeline_authorized") is True,
        "d06_or_later_authorized": payload.get("d06_or_later_authorized") is True,
        "one_demo_authorized": payload.get("one_demo_authorized") is True,
        "run_uuid": str(payload.get("run_uuid")) == contract.run_uuid,
        "run_root": canonical_path(str(payload.get("run_root", "")))
        == canonical_path(run_root),
        "source_commit": str(payload.get("source_commit")) == contract.source_commit,
        "contract_sha256": str(payload.get("contract_sha256")) == contract_sha256,
        "pipeline_config_sha256": str(payload.get("pipeline_config_sha256"))
        == pipeline_config_sha256,
        "hard_cap_hours": _float_matches(
            payload.get("hard_cap_hours"), contract.hard_cap_hours
        ),
        "previous_200_hour_authorization_not_inherited": payload.get(
            "previous_200_hour_authorization_inherited"
        )
        is False,
    }


def validate_explicit_authorization(
    payload: Mapping[str, Any],
    contract: PipelineContract,
    *,
    run_root: str | Path,
    contract_path: str | Path,
    pipeline_config_path: str | Path,
) -> dict[str, bool]:
    checks = _run_bound_checks(
        payload,
        contract,
        run_root=run_root,
        contract_sha256=sha256_file(contract_path),
        pipeline_config_sha256=sha256_file(pipeline_config_path),
    )
    if not all(checks.values()):
        raise PermissionError(f"Run-bound explicit authorization failed: {checks}")
    return checks


def validate_runtime_authorization(
    payload: Mapping[str, Any],
    contract: PipelineContract,
    *,
    run_root: str | Path,
    contract_sha256: str,
    pipeline_config_path: str | Path,
) -> dict[str, bool]:
    checks = _run_bound_checks(
        payload,
        contract,
        run_root=run_root,
        contract_sha256=contract_sha256,
        pipeline_config_sha256=sha256_file(pipeline_config_path),
    )
    checks["authorization_source"] = (
        payload.get("authorization_source") == "explicit_run_bound_user_authorization"
    )
    if not all(checks.values()):
        raise PermissionError(f"Run-bound runtime authorization failed: {checks}")
    return checks
