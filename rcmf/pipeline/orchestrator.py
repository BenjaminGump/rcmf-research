from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from rcmf.pipeline.contracts import ArmContract, PipelineContract
from rcmf.pipeline.scheduler import EventDrivenScheduler, SchedulerResult
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph


def load_pipeline_contract(path: str | Path) -> PipelineContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    arms = {
        key: ArmContract(**value)
        for key, value in payload["arms"].items()
    }
    contract = PipelineContract(
        schema_version=str(payload["schema_version"]),
        run_uuid=str(payload["run_uuid"]),
        source_commit=str(payload["source_commit"]),
        global_seed=int(payload["global_seed"]),
        hard_cap_hours=float(payload["hard_cap_hours"]),
        stages=build_exp037a_stage_graph(),
        arms=arms,
        shared_initialization=dict(payload.get("shared_initialization", {})),
        metadata=dict(payload.get("metadata", {})),
    )
    contract.validate()
    return contract


def run_pipeline(
    contract_path: str | Path,
    run_root: str | Path,
    *,
    python_executable: str,
) -> SchedulerResult:
    contract = load_pipeline_contract(contract_path)
    stage_config = str(
        contract.metadata.get(
            "pipeline_config_path",
            "configs/pipeline/rcmf_appworld_repro_14b.yaml",
        )
    )
    scheduler = EventDrivenScheduler(
        contract,
        run_root,
        python_executable=python_executable,
        config_path=stage_config,
    )
    return scheduler.run()


def result_as_dict(result: SchedulerResult) -> Mapping[str, Any]:
    return {
        "status": result.status,
        "completed": result.completed,
        "skipped": result.skipped,
        "failed_stage": result.failed_stage,
        "transitions": result.transitions,
    }
