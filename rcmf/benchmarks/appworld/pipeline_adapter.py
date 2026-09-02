from __future__ import annotations

import json
import platform
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from rcmf.benchmarks.appworld.prompt import build_appworld_messages, build_task_message
from rcmf.pipeline.audit import redact_record


class AppWorldReproduciblePipelineAdapter:
    """Thin EXP-037A adapter around the repository's validated AppWorld helpers."""

    def __init__(
        self,
        *,
        corpus_root: str | Path,
        legacy_root: str | Path,
        split_names: Sequence[str] = ("train", "dev"),
    ) -> None:
        self.corpus_root = Path(corpus_root)
        self.legacy_root = Path(legacy_root)
        self.split_names = tuple(split_names)

    def benchmark_identity(self) -> Mapping[str, Any]:
        try:
            version = metadata.version("appworld")
        except metadata.PackageNotFoundError:
            version = "unavailable"
        return {
            "name": "appworld",
            "package_version": version,
            "legacy_root": str(self.legacy_root),
            "python": platform.python_version(),
        }

    def list_splits(self) -> Mapping[str, Sequence[str]]:
        from appworld import load_task_ids

        return {
            split: tuple(load_task_ids(dataset_name=split))
            for split in self.split_names
        }

    def load_successful_training_trajectories(self) -> Iterable[Mapping[str, Any]]:
        path = self.corpus_root / "memory_records.jsonl"
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def canonicalize_trajectory(self, trajectory: Mapping[str, Any]) -> Mapping[str, Any]:
        from rcmf.utils.serialization import to_jsonable

        return to_jsonable(dict(trajectory))

    def extract_transition_records(
        self, trajectory: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]:
        transition_path = self.corpus_root / "transition_manifest.jsonl"
        parent_id = str(trajectory.get("memory_id", ""))
        with transition_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if str(row.get("parent_memory_id", "")) == parent_id:
                    yield row

    def lineage_keys(self, row: Mapping[str, Any]) -> Sequence[str]:
        keys = row.get("leakage_keys") or row.get("metadata", {}).get("leakage_keys")
        if keys:
            return tuple(str(value) for value in keys)
        return tuple(
            f"{name}:{row[name]}"
            for name in ("task_id", "episode_id", "replay_id", "lineage_id")
            if row.get(name)
        )

    def render_state(self, example: Mapping[str, Any], prompt_profile: str) -> Any:
        if str(example.get("benchmark", "")) == "appworld" and example.get(
            "state_text"
        ):
            from rcmf.schemas import DecisionExample
            from rcmf.training.datasets import _appworld_messages_from_example

            return _appworld_messages_from_example(
                DecisionExample.from_dict(dict(example)), prompt_profile
            )
        task_message = str(example.get("task_message") or example.get("state_text") or "")
        if not task_message and example.get("task_instruction"):
            task_message = build_task_message(
                str(example["task_instruction"]),
                dict(example.get("supervisor", {})),
                profile=prompt_profile,
            )
        return build_appworld_messages(
            task_message,
            list(example.get("trajectory_so_far", [])),
            prompt_profile=prompt_profile,
            max_context_turns=example.get("max_context_turns"),
        )

    def render_transition(self, record: Mapping[str, Any]) -> str:
        from rcmf.training.multiview_representations_6c import transition_text_and_char_spans

        return str(transition_text_and_char_spans(record)[0])

    def build_selector_supervision(
        self,
        examples: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        from rcmf.training.procedural_supervision_6f import (
            canonical_procedure_signature,
            observation_signature,
            procedural_compatibility,
            state_stage_signature,
        )

        rows: list[dict[str, Any]] = []
        for example in examples:
            target = canonical_procedure_signature(
                str(example["target_text"]), context_text=str(example["state_text"])
            )
            stage = state_stage_signature(str(example["state_text"]))
            for transition in transitions:
                action = canonical_procedure_signature(
                    str(transition["complete_action"]),
                    context_text=str(transition["canonical_pre_action_state"]),
                )
                rows.append(
                    {
                        "state_example_id": str(example["state_example_id"]),
                        "transition_id": str(transition["transition_id"]),
                        "compatibility": procedural_compatibility(
                            target,
                            stage,
                            action,
                            state_stage_signature(str(transition["canonical_pre_action_state"])),
                            observation_signature(
                                str(transition["complete_post_action_observation"])
                            ),
                        ),
                    }
                )
        return rows

    def build_causal_teacher_conditions(
        self,
        example: Mapping[str, Any],
        transition: Mapping[str, Any],
        prompt_profile: str,
    ) -> Sequence[Mapping[str, Any]]:
        from rcmf.training.transition_memory_6a import messages_with_transition_memory

        messages = self.render_state(example, prompt_profile)
        return (
            {"condition": "bare", "messages": messages},
            {
                "condition": "raw_transition",
                "messages": messages_with_transition_memory(
                    messages, transition, prompt_profile
                ),
            },
        )

    def execute_action(self, runtime: Any, action: str) -> Any:
        return runtime.execute(action)

    def evaluate_task(self, runtime: Any) -> Mapping[str, Any]:
        evaluation = runtime.evaluate()
        return {
            "success": bool(getattr(evaluation, "success", False)),
            "raw": evaluation,
        }

    def redact_audit_record(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return redact_record(record)[0]
