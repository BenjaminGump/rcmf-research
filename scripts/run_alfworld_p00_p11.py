from __future__ import annotations

import argparse
import json
from pathlib import Path

from rcmf.benchmarks.alfworld.portable_executor_v2_1 import (
    create_alfworld_portable_executor_v2_1,
    evidence_phase_handlers,
)
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import execution_phases_for_policy
from rcmf.pipeline.portable_v2.executor import (
    PortableDependencyReference,
    PortableExecutionIdentity,
    PortablePhaseContext,
    execute_and_validate_phase,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ALFWorld real-evidence P00-P11 graph")
    parser.add_argument("--config", required=True)
    parser.add_argument("--evidence-map", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-uuid", required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = PortablePipelineConfig.load(args.config)
    evidence = json.loads(Path(args.evidence_map).read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise TypeError("ALFWorld phase evidence map must be an object")
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    identity = PortableExecutionIdentity(
        source_commit=args.source_commit,
        run_uuid=args.run_uuid,
        run_root=output_root,
        pipeline_config_sha256=config.sha256,
        dataset_profile_sha256=config.dataset_profile.sha256,
        adapter_identity=config.adapter_factory,
    )
    executor = create_alfworld_portable_executor_v2_1(
        phase_handlers=evidence_phase_handlers()
    )
    prior_manifest = None
    stage_rows = []
    for phase in execution_phases_for_policy(config.policy):
        raw_inputs = evidence.get(phase.value)
        if not isinstance(raw_inputs, dict) or not raw_inputs:
            raise ValueError(f"missing real ALFWorld evidence for {phase.value}")
        stage_root = output_root / phase.value
        context = PortablePhaseContext(
            identity=identity,
            phase=phase,
            dependency_manifests=(
                (PortableDependencyReference.same_run(prior_manifest),)
                if prior_manifest is not None
                else ()
            ),
            input_artifacts={str(key): Path(str(value)) for key, value in raw_inputs.items()},
            output_root=stage_root,
            policy={
                "prompt_profile": config.policy.prompt_profile,
                "training_epochs": config.policy.training_epochs,
                "global_seed": config.policy.global_seed,
                "checkpoint_policy": "terminal_completed_epoch",
            },
        )
        manifest = execute_and_validate_phase(executor, context)
        prior_manifest = stage_root / "stage_manifest.json"
        stage_rows.append(
            {
                "phase_id": phase.value,
                "manifest_path": str(prior_manifest),
                "manifest_sha256": manifest["manifest_sha256"],
            }
        )
    result = {
        "format": "alfworld_portable_p00_p11_run_v1",
        "source_commit": args.source_commit,
        "run_uuid": args.run_uuid,
        "stage_count": len(stage_rows),
        "stages": stage_rows,
        "passed": len(stage_rows) == 12,
    }
    summary = output_root / "p00_p11_summary.json"
    summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
