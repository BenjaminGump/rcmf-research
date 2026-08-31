"""Export Git-safe EXP-036A traces, result tables, and claims boundaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

import _bootstrap  # noqa: F401

from rcmf.training.rcmf_appworld_testnormal_final_13a import CONDITIONS
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.utils.serialization import sha256_file
from scripts.export_rcmf_benefit_preserving_audit_9b import (
    strict_redact,
    strict_verify_tree,
)
from scripts.export_rcmf_joint_full_bank_audit_9a import (
    first37_record,
    materialized_step,
    register_sensitive_observation,
)
from scripts.run_rcmf_q90_trajectory_common_9c import first_divergence


RUN_UUID = "rcmf_appworld_testnormal_final_13a_20260831_002"
FORMAT = "rcmf_appworld_testnormal_git_safe_audit_13a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--audit-root", type=Path, default=Path("research/audits") / RUN_UUID
    )
    parser.add_argument(
        "--result-root",
        type=Path,
        default=Path("research/results/exp036a_appworld_testnormal_final"),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_path(artifact_dir: Path, condition: str, task_id: str) -> Path:
    return (
        artifact_dir
        / "formal/conditions"
        / condition
        / "task_results"
        / f"{task_id}.json"
    )


def register_task_secrets(task: Mapping[str, Any]) -> None:
    for step in task["steps"]:
        register_sensitive_observation(
            str(step.get("exact_executed_code", "")),
            str(step.get("complete_environment_observation", "")),
        )
        for turn in step.get("complete_trajectory_so_far", []):
            register_sensitive_observation(
                str(turn.get("response", "")), str(turn.get("observation", ""))
            )


def safe_step(
    path: Path, task: Mapping[str, Any], step: Mapping[str, Any]
) -> dict[str, Any]:
    tensor_key = (
        "lambda-only:"
        + str(step["field"]["tensor_artifact"])
        + ":"
        + str(step["field"]["tensor_artifact_sha256"])
    )
    row = first37_record(path, task, step, tensor_key, [])
    row["format"] = FORMAT
    row["audit_scope"] = "official_appworld_test_normal_complete_agent"
    row["evaluation_only"] = True
    row["test_normal_partially_exposed"] = True
    row["condition"] = str(task["condition"])
    row["lambda_only_field_tensor"] = {
        "path": str(Path(str(step["field"]["tensor_artifact"])).resolve()),
        "sha256": str(step["field"]["tensor_artifact_sha256"]),
        "query_shape": None
        if step["field"]["query"] is None
        else step["field"]["query"]["shape"],
        "slot_shape": step["field"]["slots"]["shape"],
        "slot_dtype": step["field"]["slots"]["dtype"],
    }
    row["resource_metrics"] = task.get("resource_metrics")
    return strict_redact(row)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def comparison_markdown(
    task_id: str, tasks: Mapping[str, Mapping[str, Any]]
) -> str:
    pairs = (
        ("B0", "BEST-C"),
        ("BEST-S", "BEST-C"),
        ("FULL1D-S", "FULL1D-C"),
    )
    divergences = {
        f"{left}_vs_{right}": first_divergence(tasks[left], tasks[right])
        for left, right in pairs
    }
    lines = [
        f"# EXP-036A Test-Normal comparison: {task_id}",
        "",
        "Git-safe materialization. Typed SHA256 placeholders replace credentials; exact unredacted traces and tensors remain on Lambda.",
        "",
        "## Outcomes",
        "",
    ]
    for condition in CONDITIONS:
        task = tasks[condition]
        lines.append(
            f"- {condition}: success={bool(task['success'])}, steps={int(task['step_count'])}, wall_seconds={float(task['wall_seconds']):.3f}"
        )
    lines.extend(
        [
            "",
            "## First Divergences",
            "",
            "```json",
            json.dumps(strict_redact(divergences), indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    selected: dict[str, set[int]] = {condition: set() for condition in CONDITIONS}
    for pair_name, divergence in divergences.items():
        if divergence is None or divergence.get("step_id") is None:
            continue
        step_id = int(divergence["step_id"])
        left, right = pair_name.split("_vs_", maxsplit=1)
        selected[left].add(step_id)
        selected[right].add(step_id)
    for condition, task in tasks.items():
        if task["steps"]:
            selected[condition].add(int(task["steps"][-1]["step_id"]))
        for step in task["steps"]:
            if (
                step.get("termination_after_step") != "continue"
                or bool(step.get("repeated_action"))
                or bool(step.get("repeated_invalid_action"))
                or step.get("execution_exception")
            ):
                selected[condition].add(int(step["step_id"]))
    for condition in CONDITIONS:
        lines.extend([f"## Materialized {condition} Steps", ""])
        for step_id in sorted(selected[condition]):
            step = next(
                (
                    row
                    for row in tasks[condition]["steps"]
                    if int(row["step_id"]) == step_id
                ),
                None,
            )
            if step is None:
                continue
            lines.extend(
                [
                    f"### Step {step_id}",
                    "",
                    "```json",
                    json.dumps(
                        strict_redact(materialized_step(step)),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "## Interpretation Boundary",
            "",
            "- **VERIFIED:** recorded prompts, outputs, executed code, observations, task outcomes, and first textual divergences.",
            "- **INFERENCE:** a BEST-C/BEST-S difference is consistent with a memory-specific whole-bank effect.",
            "- **UNVERIFIED:** causal mechanisms after the first logged divergence unless directly established by the trace.",
            "",
        ]
    )
    return "\n".join(lines)


def paper_values(
    analysis: Mapping[str, Any],
    efficiency: Mapping[str, Any],
    serving: Mapping[str, Any],
    reversibility: Mapping[str, Any],
) -> dict[str, Any]:
    comparisons = analysis["comparisons"]
    values = {
        "format": "rcmf_exp036a_paper_table_values_13a_v1",
        "appworld_test_normal": {
            condition: {
                "success_count": analysis["success"][condition]["count"],
                "task_count": analysis["task_count"],
                "success_rate": analysis["success"][condition]["rate"],
            }
            for condition in CONDITIONS
        },
        "paired_effects": {
            name: {
                "effect_count": row["effect_count"],
                "effect_rate": row["effect_rate"],
                "bootstrap_95_ci": row["paired_bootstrap_95_ci"],
                "exact_mcnemar": row["exact_mcnemar"],
            }
            for name, row in comparisons.items()
        },
        "formal_efficiency": efficiency,
        "serving_state": serving,
        "numerical_reversibility": {
            "remove_ms": reversibility["remove_ms"],
            "restore_ms": reversibility["restore_ms"],
            "maximum_absolute_error": reversibility["maximum_absolute_error"],
            "relative_frobenius_error": reversibility[
                "relative_frobenius_error"
            ],
        },
        "unsupported": {
            "ALFWorld": "NOT_RUN",
            "WebShop": "NOT_RUN",
            "TTR": "NOT_RUN",
            "ReMe": "NOT_RUN",
            "Experience-LoRA": "NOT_RUN",
            "state_query_shuffle_full_task": "NOT_RUN",
            "behavioral_record_deletion": "NOT_RUN",
            "raw_memory_compilation_retention": "NOT_RUN",
        },
    }
    values["values_sha256"] = canonical_sha256(values)
    return values


def latex_values(values: Mapping[str, Any]) -> str:
    lines = ["% Generated by EXP-036A; unsupported values are explicit."]
    names = {
        "B0": "AppWorldBare",
        "BEST-C": "AppWorldBestCorrect",
        "BEST-S": "AppWorldBestShuffle",
        "FULL1D-C": "AppWorldFullOneDemoCorrect",
        "FULL1D-S": "AppWorldFullOneDemoShuffle",
    }
    for condition, macro in names.items():
        row = values["appworld_test_normal"][condition]
        lines.append(
            f"\\newcommand{{\\{macro}Success}}{{{row['success_count']}/{row['task_count']}}}"
        )
    for name in values["unsupported"]:
        macro = "Unsupported" + "".join(part.title() for part in name.replace("-", "_").split("_"))
        lines.append(f"\\newcommand{{\\{macro}}}{{NOT\\_RUN}}")
    return "\n".join(lines) + "\n"


CLAIMS_BOUNDARY = """# EXP-036A Claims Boundary

1. Final AppWorld evaluation uses the original first complete demonstration, not a canonical multi-demonstration prompt.
2. BEST is evaluated under one-demo deployment, but its trained components originate from the historical EXP-031A pipeline.
3. FULL1D is the fully one-demo-trained secondary configuration.
4. Q90 is not part of the final primary method and must not be described as a required final component.
5. Test-Normal was partially exposed during exploratory development; this is an official-split result, not an untouched-test claim.
6. Active field state is constant with respect to memory count; the raw ledger and per-record deletion/provenance storage are linear in N.
7. This run does not answer ALFWorld/WebShop generality, textual-memory baselines, LoRA baselines, section ablations, or behavioral deletion.
"""


def result_report(
    analysis: Mapping[str, Any],
    package: Mapping[str, Any],
    test_manifest: Mapping[str, Any],
    efficiency: Mapping[str, Any],
    reversibility: Mapping[str, Any],
) -> str:
    lines = [
        "# EXP-036A Frozen AppWorld Test-Normal Final Evaluation",
        "",
        "## Scope",
        "",
        "Evaluation-only frozen five-condition run. BEST was preregistered as primary; FULL1D remained the secondary ablation. No parameter, prompt, field, gate, scale, or checkpoint was changed after the manifest freeze.",
        "",
        "## Formal Results",
        "",
        "| Condition | Success | Rate |",
        "|---|---:|---:|",
    ]
    for condition in CONDITIONS:
        row = analysis["success"][condition]
        lines.append(
            f"| {condition} | {row['count']}/{analysis['task_count']} | {row['rate']:.6f} |"
        )
    lines.extend(["", "## Paired Effects", ""])
    for name, row in analysis["comparisons"].items():
        ci = row["paired_bootstrap_95_ci"]
        lines.append(
            f"- `{name}`: {row['effect_count']} tasks ({row['effect_rate']:.6f}); paired bootstrap 95% CI [{ci['lower_95']:.6f}, {ci['upper_95']:.6f}]."
        )
    lines.extend(
        [
            "",
            "## Frozen Identities",
            "",
            f"- Ordered Test-Normal manifest: `{test_manifest['ordered_task_ids_sha256']}` ({test_manifest['task_count']} tasks).",
            f"- BEST selector: `{package['packages']['BEST']['hashes']['selector_ensemble']}`.",
            f"- BEST writer/reader: `{package['packages']['BEST']['hashes']['writer_reader_checkpoint']}`.",
            f"- BEST field: `{package['packages']['BEST']['hashes']['deployment_field']}`.",
            f"- FULL1D selector: `{package['packages']['FULL1D']['hashes']['selector_ensemble']}`.",
            f"- FULL1D writer/reader: `{package['packages']['FULL1D']['hashes']['writer_reader_checkpoint']}`.",
            f"- FULL1D field: `{package['packages']['FULL1D']['hashes']['deployment_field']}`.",
            "",
            "## Efficiency And Reversibility",
            "",
            f"- Efficiency result identity: `{efficiency['result_sha256']}`.",
            f"- Remove median/p95: {reversibility['remove_ms']['median']:.6f}/{reversibility['remove_ms']['p95']:.6f} ms.",
            f"- Restore median/p95: {reversibility['restore_ms']['median']:.6f}/{reversibility['restore_ms']['p95']:.6f} ms.",
            f"- Restoration maximum absolute error: {reversibility['maximum_absolute_error']['maximum']:.9g}.",
            "",
            "## Disclosure",
            "",
            "This is the complete official AppWorld 0.1.0 Test-Normal split. Earlier exploratory development used a Test-Normal subset and a historical full bare result. The five-condition full manifest was frozen before this complete run, and no parameter or configuration changed after generation began. The result is therefore an official-split result, but not an entirely untouched-test result.",
            "",
            "See `claims_boundary.md` for the exact paper-facing limitations.",
        ]
    )
    return "\n".join(lines) + "\n"


def export(artifact_dir: Path, audit_root: Path, result_root: Path) -> dict[str, Any]:
    if audit_root.exists() or result_root.exists():
        raise FileExistsError("Refusing to overwrite an EXP-036A Git-safe export")
    audit_tmp = audit_root.with_name(audit_root.name + ".tmp")
    result_tmp = result_root.with_name(result_root.name + ".tmp")
    if audit_tmp.exists() or result_tmp.exists():
        raise FileExistsError("Stale EXP-036A export temporary root")
    audit_tmp.mkdir(parents=True)
    result_tmp.mkdir(parents=True)

    formal = read_json(artifact_dir / "results/formal_summary.json")
    if not bool(formal.get("evaluation_complete")) or int(
        formal.get("trajectory_count", 0)
    ) != 840:
        raise RuntimeError("EXP-036A export requires all 840 formal rows")
    analysis = read_json(artifact_dir / "analysis/paired_analysis.json")
    package = read_json(artifact_dir / "manifests/package_manifest.json")
    test_manifest = read_json(
        artifact_dir / "manifests/test_normal_manifest.json"
    )
    condition_manifest = read_json(
        artifact_dir / "manifests/condition_manifest.json"
    )
    prompt_manifest = read_json(artifact_dir / "manifests/prompt_manifest.json")
    runtime_preflight = read_json(
        artifact_dir / "preflight/runtime_preflight.json"
    )
    compilation = read_json(
        artifact_dir / "efficiency/compilation_results.json"
    )
    scaling = read_json(artifact_dir / "efficiency/scaling_results.json")
    ttft = read_json(artifact_dir / "efficiency/ttft_results.json")
    efficiency = read_json(artifact_dir / "efficiency/formal_efficiency.json")
    serving = read_json(
        artifact_dir / "efficiency/serving_state_results.json"
    )
    reversibility = read_json(
        artifact_dir / "reversibility/reversibility_results.json"
    )
    task_ids = [str(value) for value in test_manifest["task_ids"]]
    tasks = {
        task_id: {
            condition: read_json(task_path(artifact_dir, condition, task_id))
            for condition in CONDITIONS
        }
        for task_id in task_ids
    }
    for task_conditions in tasks.values():
        for task in task_conditions.values():
            register_task_secrets(task)

    static_source = artifact_dir / "raw_audit/static_prompt_assets.json"
    write_json(
        audit_tmp / "static_prompt_assets.json",
        strict_redact(read_json(static_source)),
    )
    trace_index = []
    for task_id in task_ids:
        for condition in CONDITIONS:
            raw_path = task_path(artifact_dir, condition, task_id)
            task = tasks[task_id][condition]
            relative = Path("formal") / condition / f"{task_id}.jsonl"
            safe_rows = [safe_step(raw_path, task, step) for step in task["steps"]]
            write_jsonl(audit_tmp / relative, safe_rows)
            trace_index.append(
                {
                    "task_id": task_id,
                    "condition": condition,
                    "success": bool(task["success"]),
                    "step_count": int(task["step_count"]),
                    "git_safe_path": relative.as_posix(),
                    "git_safe_sha256": sha256_file(audit_tmp / relative),
                    "raw_lambda_path": str(raw_path.resolve()),
                    "raw_sha256": sha256_file(raw_path),
                    "audit_complete": bool(task["raw_audit_complete"]),
                }
            )
        comparison = comparison_markdown(task_id, tasks[task_id])
        path = audit_tmp / "comparisons" / f"{task_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(comparison, encoding="utf-8")

    audit_index = {
        "format": "rcmf_exp036a_audit_index_13a_v1",
        "run_uuid": RUN_UUID,
        "task_count": len(task_ids),
        "condition_count": len(CONDITIONS),
        "trajectory_count": len(trace_index),
        "step_count": sum(int(row["step_count"]) for row in trace_index),
        "test_normal_manifest_sha256": test_manifest["manifest_sha256"],
        "condition_manifest_sha256": condition_manifest["manifest_sha256"],
        "prompt_manifest_sha256": prompt_manifest["manifest_sha256"],
        "static_prompt_assets": {
            "git_safe_path": "static_prompt_assets.json",
            "git_safe_sha256": sha256_file(
                audit_tmp / "static_prompt_assets.json"
            ),
            "raw_lambda_path": str(static_source.resolve()),
            "raw_sha256": sha256_file(static_source),
        },
        "traces": trace_index,
        "raw_artifact_root": str(artifact_dir.resolve()),
        "raw_unredacted_logs_lambda_only": True,
        "independently_verified": True,
    }
    audit_index["index_content_sha256"] = canonical_sha256(audit_index)
    write_json(audit_tmp / "index.json", audit_index)

    result_files = {
        "summary.json": formal,
        "frozen_model_manifest.json": package,
        "test_normal_manifest.json": test_manifest,
        "condition_manifest.json": condition_manifest,
        "prompt_manifest.json": prompt_manifest,
        "paired_analysis.json": analysis,
        "trajectory_metrics.json": analysis["trajectory_metrics"],
        "formal_efficiency.json": read_json(
            artifact_dir / "analysis/formal_efficiency.json"
        ),
        "scaling_results.json": scaling,
        "compilation_results.json": compilation,
        "ttft_results.json": ttft,
        "serving_state_results.json": serving,
        "reversibility_results.json": reversibility,
        "runtime_preflight.json": runtime_preflight,
    }
    for name, value in result_files.items():
        write_json(result_tmp / name, strict_redact(value))
    shutil.copyfile(artifact_dir / "analysis/per_task.jsonl", result_tmp / "per_task.jsonl")
    shutil.copyfile(artifact_dir / "attempts.jsonl", result_tmp / "attempts.jsonl")
    values = paper_values(analysis, efficiency, serving, reversibility)
    write_json(result_tmp / "paper_table_values.json", values)
    (result_tmp / "paper_table_values.tex").write_text(
        latex_values(values), encoding="utf-8"
    )
    (result_tmp / "claims_boundary.md").write_text(
        CLAIMS_BOUNDARY, encoding="utf-8"
    )
    (result_tmp / "EXP_036A_APPWORLD_TESTNORMAL_FINAL.md").write_text(
        result_report(
            analysis, package, test_manifest, efficiency, reversibility
        ),
        encoding="utf-8",
    )
    strict_audit = strict_verify_tree(audit_tmp)
    strict_results = strict_verify_tree(result_tmp)
    audit_tmp.replace(audit_root)
    result_tmp.replace(result_root)
    return {
        "format": "rcmf_exp036a_git_safe_export_summary_13a_v1",
        "audit_root": str(audit_root),
        "audit_index_sha256": sha256_file(audit_root / "index.json"),
        "result_root": str(result_root),
        "audit_secret_scan": strict_audit,
        "result_secret_scan": strict_results,
        "trajectory_count": len(trace_index),
        "git_safe_audit_bytes": sum(
            path.stat().st_size for path in audit_root.rglob("*") if path.is_file()
        ),
        "git_safe_result_bytes": sum(
            path.stat().st_size for path in result_root.rglob("*") if path.is_file()
        ),
    }


def main() -> None:
    args = parse_args()
    result = export(args.artifact_dir, args.audit_root, args.result_root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
