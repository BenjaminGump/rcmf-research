from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
from typing import Any

from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.adapter import (
    ReproducibleBenchmarkAdapterV2,
    validate_adapter_capabilities,
)
from rcmf.pipeline.portable_v2.dag import PortableRunPolicy, portable_stage_graph_manifest
from rcmf.pipeline.portable_v2.schemas import ensure_no_split_leakage
from rcmf.utils.serialization import atomic_write_json


def run_manifest_only_conformance(
    *,
    adapter: ReproducibleBenchmarkAdapterV2,
    policy: PortableRunPolicy,
    run_root: str | Path,
) -> dict[str, Any]:
    """Exercise the complete contract surface without model execution or training."""
    root = Path(run_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=False)
    capability_report = validate_adapter_capabilities(adapter)
    tasks_by_split = adapter.list_tasks()
    tasks = tuple(task for split in tasks_by_split.values() for task in split)
    ensure_no_split_leakage(tasks)
    task_by_id = {task.task_id: task for task in tasks}
    sources = tuple(adapter.trajectory_sources())
    for source in sources:
        source.validate()
    training_splits = sorted({split for source in sources for split in source.training_splits})
    trajectories = []
    transitions = []
    states = []
    for split in training_splits:
        for trajectory in adapter.successful_trajectories(split):
            trajectory.validate()
            if not trajectory.success:
                raise ValueError("successful trajectory provider emitted an unsuccessful row")
            if trajectory.replay_status.value != "VALIDATED":
                raise ValueError(
                    "successful trajectory provider emitted a row without replay validation"
                )
            task = task_by_id[trajectory.task_id]
            trajectories.append(trajectory)
            transitions.extend(adapter.transition_records(task, trajectory))
            states.extend(adapter.decision_states(task, trajectory, policy.prompt_profile))
    for transition in transitions:
        transition.validate()
    for state in states:
        state.validate()
    supervision = adapter.build_selector_supervision(states, transitions)
    rendered = []
    for state in states:
        messages = adapter.render_messages(state, policy.prompt_profile)
        rendered.append(
            {
                "state_id": state.state_id,
                "message_sha256": content_sha256(list(messages)),
                "tokens": adapter.count_runtime_tokens(messages, policy.prompt_profile),
            }
        )
    condition_count = 0
    if states and transitions:
        condition_count = len(
            adapter.causal_conditions(states[0], transitions[0], policy.prompt_profile)
        )
    graph = portable_stage_graph_manifest(policy)
    previous_sha = None
    completed = []
    for stage in graph["stages"]:
        body = {
            "format": "rcmf_portable_manifest_only_stage_v2",
            "stage_id": stage["stage_id"],
            "dependency_manifest_sha256": previous_sha,
            "benchmark": adapter.identity().benchmark_name,
            "prompt_profile": policy.prompt_profile,
            "counts": {
                "tasks": len(tasks),
                "trajectories": len(trajectories),
                "transitions": len(transitions),
                "decision_states": len(states),
                "selector_supervision": len(supervision),
                "causal_conditions_fixture": condition_count,
            },
            "scientific_execution": False,
            "training_executed": False,
            "passed": True,
        }
        body["manifest_sha256"] = content_sha256(body)
        stage_dir = root / "stages" / stage["stage_id"]
        atomic_write_json(stage_dir / "stage_manifest.json", body)
        previous_sha = body["manifest_sha256"]
        completed.append(stage["stage_id"])
    summary = {
        "format": "rcmf_portable_manifest_only_conformance_v2",
        "root": str(root),
        "capability_report": capability_report,
        "stage_count": len(completed),
        "completed_stages": completed,
        "counts": {
            "splits": {name: len(rows) for name, rows in sorted(tasks_by_split.items())},
            "tasks": len(tasks),
            "trajectories": len(trajectories),
            "transitions": len(transitions),
            "decision_states": len(states),
        },
        "rendered": rendered,
        "scientific_execution": False,
        "passed": True,
    }
    atomic_write_json(root / "conformance_summary.json", summary)
    return summary
