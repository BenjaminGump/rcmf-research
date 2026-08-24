from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_ungated_structured_e2e_7h2.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def _comparison(left: set[str], right: set[str]) -> dict[str, Any]:
    return {
        "difference": len(left) - len(right),
        "retained": sorted(left & right),
        "gained": sorted(left - right),
        "lost": sorted(right - left),
        "single_seed_descriptive_not_statistical": True,
    }


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_7h2"]
    u0_path = Path(str(settings["first37"]["matched_bare_summary"]))
    u1_path = args.artifact_dir / "ungated_correct_first37/summary.json"
    u2_path = args.artifact_dir / "ungated_transition_shuffle_first37/summary.json"
    gate_path = args.artifact_dir / "gate_distribution/gate_distribution_audit.json"
    fresh_path = args.artifact_dir / "fresh_test/fresh_test37_post_exp028b.json"
    u0, u1, u2 = _json(u0_path), _json(u1_path), _json(u2_path)
    gate, fresh = _json(gate_path), _json(fresh_path)
    for name, row in (("U0", u0), ("U1", u1), ("U2", u2)):
        if int(row["task_count"]) != 37:
            raise ValueError(f"{name} does not contain the fixed first37 task set")
    sets = {
        "U0": set(map(str, u0["success_ids"])),
        "U1": set(map(str, u1["success_ids"])),
        "U2": set(map(str, u2["success_ids"])),
    }
    if len(sets["U1"]) >= 9 and len(sets["U1"]) >= len(sets["U2"]) + 2:
        interpretation = "PRELIMINARY_END_TO_END_POSITIVE"
        branch = "ungated_structured_compiler_preliminary_positive"
    elif len(sets["U1"]) > len(sets["U2"]):
        interpretation = (
            "MEMORY_SPECIFIC_LIVE_SIGNAL"
            if len(sets["U1"]) >= len(sets["U0"]) - 1
            else "PARTIAL_LIVE_SIGNAL"
        )
        branch = "structured_compiler_has_live_memory_specific_signal"
    else:
        interpretation = "GENERIC_OR_NO_LIVE_SIGNAL"
        branch = "structured_compiler_live_specificity_failed"
    report = {
        "format": "ungated_structured_compiler_e2e_analysis_7h2_v1",
        "run_uuid": str(settings["run_uuid"]),
        "global_seed": int(settings["global_seed"]),
        "U0": {
            "success_count": len(sets["U0"]),
            "success_ids": sorted(sets["U0"]),
            "summary_sha256": sha256_file(u0_path),
        },
        "U1": {**u1, "summary_sha256": sha256_file(u1_path)},
        "U2": {**u2, "summary_sha256": sha256_file(u2_path)},
        "comparisons": {
            "U1_minus_U0": _comparison(sets["U1"], sets["U0"]),
            "U1_minus_U2": _comparison(sets["U1"], sets["U2"]),
            "U2_minus_U0": _comparison(sets["U2"], sets["U0"]),
        },
        "gate_distribution_diagnosis": gate["diagnosis"],
        "fresh_test_manifest_status": fresh["status"],
        "fresh_test_manifest_sha256": fresh["manifest_sha256"],
        "interpretation": interpretation,
        "decision_branch": branch,
        "single_seed_descriptive_not_statistical": True,
        "model_or_threshold_changed": False,
        "passed_infrastructure": bool(
            u1["passed_infrastructure"] and u2["passed_infrastructure"]
        ),
    }
    atomic_write_json(args.artifact_dir / "analysis/final_analysis.json", report)
    atomic_write_text(
        args.artifact_dir / "analysis/report.md",
        "\n".join(
            [
                "# EXP-028B ungated structured compiler analysis",
                "",
                f"- U0 matched bare: `{len(sets['U0'])}/37`",
                f"- U1 correct compiled: `{len(sets['U1'])}/37`",
                f"- U2 transition shuffle: `{len(sets['U2'])}/37`",
                f"- U1-U0: `{len(sets['U1']) - len(sets['U0']):+d}` tasks",
                f"- U1-U2: `{len(sets['U1']) - len(sets['U2']):+d}` tasks",
                f"- interpretation: `{interpretation}`",
                f"- decision branch: `{branch}`",
                "- task-level differences are single-seed descriptive diagnostics",
                "",
            ]
        ),
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
