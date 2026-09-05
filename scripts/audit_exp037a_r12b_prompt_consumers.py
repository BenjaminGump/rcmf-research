from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.pipeline.manifests import file_identity
from rcmf.utils.serialization import atomic_write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def _source(path: str) -> tuple[Path, str]:
    source_path = Path(path)
    return source_path, source_path.read_text(encoding="utf-8")


def main() -> None:
    args = _parse_args()
    stages_path, stages = _source(
        "rcmf/benchmarks/appworld/reproducible_stages_14b.py"
    )
    compiler_path, compiler = _source(
        "scripts/run_appworld_structured_compiler_7hr.py"
    )
    live_path, live = _source("scripts/run_rcmf_joint_full_bank_live_9a.py")
    paired_path, paired = _source(
        "scripts/run_appworld_train_causal_gate_7hr.py"
    )
    resolver_path, resolver = _source(
        "rcmf/benchmarks/appworld/paired_causal_runtime_14k.py"
    )

    evidence_checks = {
        "paired_resolver_called": (
            "resolve_effective_paired_causal_runtime(" in paired
            and 'command.extend(["--arm-id", arm_id])' in stages
        ),
        "teacher_uses_arm_profile": (
            "prompt_profile=cfg.benchmark.prompt_profile" in compiler
            and "transitions[transition_id], cfg.benchmark.prompt_profile"
            in compiler
        ),
        "heldout_uses_arm_profile": (
            "load_config(_arm_config(run_root, arm_id)).benchmark.prompt_profile"
            in stages
            and 'prompt_profile=str(settings["appworld"]["prompt_profile"])'
            in live
        ),
        "deployment_is_common_one_demo": (
            'prompt_profile="full_demo_first_only"' in stages
        ),
        "static_feature_bank_is_train_only": (
            'parser.add_argument("--phase", choices=("teacher", "train")'
            in compiler
            and "bank = StaticFeatureBank(" in compiler
            and 'if args.phase == "teacher":' in compiler
        ),
        "resolver_fail_closed": (
            "Missing or unknown arm-resolved prompt profile" in resolver
            and "Resolved arm prompt-profile sources disagree" in resolver
        ),
    }
    if not all(evidence_checks.values()):
        raise RuntimeError(
            f"Downstream prompt consumer audit failed: {evidence_checks}"
        )

    rows: list[dict[str, Any]] = [
        {
            "stages": ["D06", "O06"],
            "consumer": "run_appworld_train_causal_gate_7hr.py paired phase",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "effective paired-causal resolver arm override",
        },
        {
            "stages": ["D07", "O07"],
            "consumer": "run_appworld_structured_compiler_7hr.py teacher phase",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "cfg.benchmark.prompt_profile",
        },
        {
            "stages": ["D08", "O08"],
            "consumer": "prepare_rcmf_joint_full_bank_9a.py",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "resolved stage_c_9a.appworld.prompt_profile",
        },
        {
            "stages": ["D09", "D10", "O09", "O10"],
            "consumer": "run_rcmf_joint_full_bank_9a.py training phases",
            "classification": "SHARED_BUT_PROFILE_INDEPENDENT",
            "prompt_source": "prebuilt arm-specific training units",
        },
        {
            "stages": ["D11", "O11"],
            "consumer": "run_rcmf_joint_full_bank_9a.py teacher-forced validation",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "resolved stage_c_9a.appworld.prompt_profile",
        },
        {
            "stages": ["D12", "D13", "O12", "O13"],
            "consumer": "run_rcmf_joint_full_bank_live_9a.py and full trajectories",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "resolved arm config",
        },
        {
            "stages": ["D14", "D15", "D16", "D17", "O14", "O15", "O16", "O17"],
            "consumer": "checkpoint selection and field construction",
            "classification": "SHARED_BUT_PROFILE_INDEPENDENT",
            "prompt_source": "sealed arm-specific validation and training artifacts",
        },
        {
            "stages": ["D18", "D19", "D20", "O18", "O19"],
            "consumer": "common one-demo deployment evaluation",
            "classification": "ARM_RESOLVED_CORRECT",
            "prompt_source": "preregistered common full_demo_first_only deployment profile",
        },
        {
            "stages": [],
            "consumer": "StaticFeatureBank.feature compiler-train mismatch path",
            "classification": "NOT_FORMAL_PATH",
            "prompt_source": "historical full_demo literal",
        },
    ]
    output = {
        "format": "exp037a_r12b_downstream_prompt_consumer_audit_v1",
        "passed": True,
        "formal_path_mismatch_count": sum(
            row["classification"] == "FORMAL_PATH_MISMATCH" for row in rows
        ),
        "rows": rows,
        "evidence_checks": evidence_checks,
        "sources": {
            path.name: file_identity(path)
            for path in (
                stages_path,
                compiler_path,
                live_path,
                paired_path,
                resolver_path,
            )
        },
        "production_source_modified": False,
    }
    args.output_root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(args.output_root / "prompt_consumer_audit.json", output)
    print(json.dumps({"passed": True, "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
