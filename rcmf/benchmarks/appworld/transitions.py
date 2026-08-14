from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable, Sequence
from uuid import NAMESPACE_URL, uuid5

from rcmf.benchmarks.appworld.traces import AppWorldTraceStep, render_state_for_step
from rcmf.schemas import MemoryRecord
from rcmf.utils.serialization import sha256_text


TRANSITION_MANIFEST_VERSION = "appworld_decision_transition_manifest_v1"
TRANSITION_PANEL_VERSION = "appworld_decision_transition_panel_v1"
TRANSITION_RENDERER_VERSION = "decision_transition_teacher_section_v1"

API_CALL_RE = re.compile(
    r"\bapis\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)"
)
COMPLETION_RE = re.compile(r"\bapis\.supervisor\.complete_task\s*\(")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _metadata_values(metadata: dict[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    output: set[str] = set()

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key in ("id", "task_id", "episode_id", "replay_id", "lineage_id"):
                add(value.get(key))
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value).strip()
        if text:
            output.add(text)

    for field in fields:
        add(metadata.get(field))
    return tuple(sorted(output))


def _action_type(action: str) -> str:
    calls = API_CALL_RE.findall(action)
    if COMPLETION_RE.search(action):
        return "completion"
    if any(app == "api_docs" for app, _ in calls):
        return "api_documentation"
    if calls:
        mutating_prefixes = (
            "add_",
            "archive_",
            "cancel_",
            "create_",
            "delete_",
            "draft_",
            "edit_",
            "invite_",
            "like_",
            "mark_",
            "move_",
            "pay_",
            "remove_",
            "request_",
            "send_",
            "set_",
            "share_",
            "transfer_",
            "unlike_",
            "update_",
        )
        if any(api.startswith(mutating_prefixes) for _, api in calls):
            return "api_mutation"
        return "api_read_or_login"
    return "python_or_reasoning"


@dataclass(frozen=True)
class DecisionTransition:
    parent_memory_id: str
    parent_task_id: str
    parent_episode_id: str
    parent_task_ids: tuple[str, ...]
    parent_episode_ids: tuple[str, ...]
    parent_replay_ids: tuple[str, ...]
    parent_lineage_ids: tuple[str, ...]
    parent_source_path: str
    parent_final_answer: str
    parent_system_prompt_sha256: str
    parent_trajectory_sha256: str
    transition_id: str
    step_index: int
    step_count: int
    source_task_goal: str
    canonical_pre_action_state: str
    complete_action: str
    complete_post_action_observation: str
    apps: tuple[str, ...]
    api_names: tuple[str, ...]
    action_type: str
    completion_related: bool
    source_task_goal_sha256: str
    canonical_pre_action_state_sha256: str
    complete_action_sha256: str
    complete_post_action_observation_sha256: str
    transition_content_sha256: str

    @property
    def leakage_keys(self) -> set[str]:
        keys = {f"task:{value}" for value in self.parent_task_ids}
        keys.update(f"episode:{value}" for value in self.parent_episode_ids)
        keys.update(f"replay:{value}" for value in self.parent_replay_ids)
        keys.update(f"lineage:{value}" for value in self.parent_lineage_ids)
        return keys

    def to_manifest_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.update(
            {
                "format": TRANSITION_MANIFEST_VERSION,
                "parent_task_ids": list(self.parent_task_ids),
                "parent_episode_ids": list(self.parent_episode_ids),
                "parent_replay_ids": list(self.parent_replay_ids),
                "parent_lineage_ids": list(self.parent_lineage_ids),
                "apps": list(self.apps),
                "api_names": list(self.api_names),
                "leakage_keys": sorted(self.leakage_keys),
            }
        )
        return row


def transition_teacher_section(transition: DecisionTransition | dict[str, Any]) -> str:
    get = (
        (lambda key: getattr(transition, key))
        if isinstance(transition, DecisionTransition)
        else (lambda key: transition[key])
    )
    return (
        "[DECISION TRANSITION MEMORY]\n\n"
        "SOURCE TASK GOAL:\n"
        f"{str(get('source_task_goal')).strip()}\n\n"
        "SOURCE STATE BEFORE ACTION:\n"
        f"{str(get('canonical_pre_action_state')).strip()}\n\n"
        "SOURCE ACTION:\n"
        f"{str(get('complete_action')).strip()}\n\n"
        "SOURCE OBSERVATION AFTER ACTION:\n"
        f"{str(get('complete_post_action_observation')).strip()}"
    )


def _transition_id(
    *,
    parent_memory_id: str,
    step_index: int,
    source_state_sha256: str,
    action_sha256: str,
    observation_sha256: str,
) -> str:
    identity = {
        "version": TRANSITION_MANIFEST_VERSION,
        "parent_memory_id": parent_memory_id,
        "step_index": int(step_index),
        "source_state_sha256": source_state_sha256,
        "action_sha256": action_sha256,
        "observation_sha256": observation_sha256,
    }
    return str(uuid5(NAMESPACE_URL, f"rcmf:decision-transition:{_stable_sha256(identity)}"))


def extract_decision_transitions(record: MemoryRecord) -> list[DecisionTransition]:
    raw = dict(record.raw_trajectory)
    query = str(raw.get("query", ""))
    system_prompt = str(raw.get("system_prompt", ""))
    source_path = str(raw.get("source_path", record.metadata.get("source_path", "")))
    final_answer = str(raw.get("final_answer", ""))
    raw_steps = raw.get("steps")
    if not query.strip():
        raise ValueError(f"MemoryRecord {record.memory_id} has no source query")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"MemoryRecord {record.memory_id} has no transition steps")

    steps: list[AppWorldTraceStep] = []
    for position, payload in enumerate(raw_steps, start=1):
        if not isinstance(payload, dict):
            raise ValueError(f"MemoryRecord {record.memory_id} step {position} is not an object")
        step_index = int(payload.get("step_id", position))
        if step_index != position:
            raise ValueError(
                f"MemoryRecord {record.memory_id} source ordering differs at position "
                f"{position}: step_id={step_index}"
            )
        response = str(payload.get("response", ""))
        observation = str(payload.get("observation", ""))
        if not response.strip():
            raise ValueError(f"MemoryRecord {record.memory_id} step {position} has no action")
        steps.append(
            AppWorldTraceStep(
                index=step_index,
                response=response,
                observation=observation,
            )
        )

    replay_ids = _metadata_values(
        record.metadata,
        ("replay_id", "source_replay_id", "original_replay_id", "parent_replay_id"),
    )
    lineage_ids = _metadata_values(
        record.metadata,
        (
            "lineage_id",
            "source_lineage_id",
            "original_lineage_id",
            "parent_lineage_id",
            "derived_from",
            "derived_from_id",
            "trace_id",
        ),
    )
    task_ids = tuple(
        sorted(
            {record.task_id}.union(
                _metadata_values(
                    record.metadata,
                    ("task_id", "source_task_id", "original_task_id", "parent_task_id"),
                )
            )
        )
    )
    episode_ids = tuple(
        sorted(
            {record.episode_id}.union(
                _metadata_values(
                    record.metadata,
                    (
                        "episode_id",
                        "source_episode_id",
                        "original_episode_id",
                        "parent_episode_id",
                        "derived_from_episode_id",
                    ),
                )
            )
        )
    )
    parent_trajectory_sha256 = _stable_sha256(raw)
    transitions: list[DecisionTransition] = []
    previous: list[AppWorldTraceStep] = []
    for step in steps:
        source_state = render_state_for_step(query, previous, system_prompt=system_prompt)
        state_sha = sha256_text(source_state)
        action_sha = sha256_text(step.response)
        observation_sha = sha256_text(step.observation)
        api_calls = API_CALL_RE.findall(step.response)
        apps = tuple(sorted({app for app, _ in api_calls if app != "supervisor"}))
        api_names = tuple(sorted({f"{app}.{api}" for app, api in api_calls}))
        content_identity = {
            "source_task_goal_sha256": sha256_text(query),
            "canonical_pre_action_state_sha256": state_sha,
            "complete_action_sha256": action_sha,
            "complete_post_action_observation_sha256": observation_sha,
        }
        transitions.append(
            DecisionTransition(
                parent_memory_id=record.memory_id,
                parent_task_id=record.task_id,
                parent_episode_id=record.episode_id,
                parent_task_ids=task_ids,
                parent_episode_ids=episode_ids,
                parent_replay_ids=replay_ids,
                parent_lineage_ids=lineage_ids,
                parent_source_path=source_path,
                parent_final_answer=final_answer,
                parent_system_prompt_sha256=sha256_text(system_prompt),
                parent_trajectory_sha256=parent_trajectory_sha256,
                transition_id=_transition_id(
                    parent_memory_id=record.memory_id,
                    step_index=step.index,
                    source_state_sha256=state_sha,
                    action_sha256=action_sha,
                    observation_sha256=observation_sha,
                ),
                step_index=step.index,
                step_count=len(steps),
                source_task_goal=query,
                canonical_pre_action_state=source_state,
                complete_action=step.response,
                complete_post_action_observation=step.observation,
                apps=apps,
                api_names=api_names,
                action_type=_action_type(step.response),
                completion_related=bool(COMPLETION_RE.search(step.response)),
                source_task_goal_sha256=sha256_text(query),
                canonical_pre_action_state_sha256=state_sha,
                complete_action_sha256=action_sha,
                complete_post_action_observation_sha256=observation_sha,
                transition_content_sha256=_stable_sha256(content_identity),
            )
        )
        previous.append(step)
    return transitions


def reconstruct_parent_trajectory(
    transitions: Iterable[DecisionTransition],
) -> dict[str, Any]:
    ordered = sorted(transitions, key=lambda item: item.step_index)
    if not ordered:
        raise ValueError("Cannot reconstruct a parent trajectory from no transitions")
    parent_ids = {item.parent_memory_id for item in ordered}
    if len(parent_ids) != 1:
        raise ValueError(f"Transitions span multiple parents: {sorted(parent_ids)}")
    expected = list(range(1, len(ordered) + 1))
    actual = [item.step_index for item in ordered]
    if actual != expected:
        raise ValueError(f"Transition steps are not contiguous: {actual}")
    first = ordered[0]
    return {
        "query": first.source_task_goal,
        "steps": [
            {
                "step_id": item.step_index,
                "response": item.complete_action,
                "observation": item.complete_post_action_observation,
            }
            for item in ordered
        ],
        "final_answer": first.parent_final_answer,
        "source_path": first.parent_source_path,
    }


def validate_transition_extraction(
    records: Sequence[MemoryRecord],
    transitions: Sequence[DecisionTransition],
) -> dict[str, Any]:
    by_parent: dict[str, list[DecisionTransition]] = defaultdict(list)
    for transition in transitions:
        by_parent[transition.parent_memory_id].append(transition)
    errors: list[dict[str, Any]] = []
    transition_ids = [item.transition_id for item in transitions]
    if len(transition_ids) != len(set(transition_ids)):
        errors.append({"type": "duplicate_transition_id"})
    for record in records:
        parent_rows = by_parent.get(record.memory_id, [])
        raw = dict(record.raw_trajectory)
        if len(parent_rows) != len(raw.get("steps", [])):
            errors.append(
                {
                    "type": "step_count_mismatch",
                    "memory_id": record.memory_id,
                    "expected": len(raw.get("steps", [])),
                    "actual": len(parent_rows),
                }
            )
            continue
        reconstructed = reconstruct_parent_trajectory(parent_rows)
        expected = {
            "query": str(raw.get("query", "")),
            "steps": raw.get("steps", []),
            "final_answer": str(raw.get("final_answer", "")),
            "source_path": str(raw.get("source_path", record.metadata.get("source_path", ""))),
        }
        if reconstructed != expected:
            errors.append(
                {
                    "type": "parent_reconstruction_mismatch",
                    "memory_id": record.memory_id,
                    "expected_sha256": _stable_sha256(expected),
                    "actual_sha256": _stable_sha256(reconstructed),
                }
            )
        previous: list[AppWorldTraceStep] = []
        for transition in sorted(parent_rows, key=lambda item: item.step_index):
            expected_state = render_state_for_step(
                str(raw.get("query", "")),
                previous,
                system_prompt=str(raw.get("system_prompt", "")),
            )
            if transition.canonical_pre_action_state != expected_state:
                errors.append(
                    {
                        "type": "pre_action_history_boundary_mismatch",
                        "memory_id": record.memory_id,
                        "step_index": transition.step_index,
                    }
                )
            previous.append(
                AppWorldTraceStep(
                    index=transition.step_index,
                    response=transition.complete_action,
                    observation=transition.complete_post_action_observation,
                )
            )
        record_keys = {f"task:{value}" for value in parent_rows[0].parent_task_ids}
        record_keys.update(f"episode:{value}" for value in parent_rows[0].parent_episode_ids)
        record_keys.update(f"replay:{value}" for value in parent_rows[0].parent_replay_ids)
        record_keys.update(f"lineage:{value}" for value in parent_rows[0].parent_lineage_ids)
        if any(item.leakage_keys != record_keys for item in parent_rows):
            errors.append(
                {
                    "type": "parent_leakage_key_mismatch",
                    "memory_id": record.memory_id,
                    "record_keys": sorted(record_keys),
                }
            )
    return {
        "format": "appworld_decision_transition_extraction_validation_v1",
        "parent_count": len(records),
        "transition_count": len(transitions),
        "unique_transition_count": len(set(transition_ids)),
        "first_step_empty_history_count": sum(
            "[TRACE SO FAR]" not in item.canonical_pre_action_state
            for item in transitions
            if item.step_index == 1
        ),
        "completion_transition_count": sum(item.completion_related for item in transitions),
        "error_count": len(errors),
        "errors_first_50": errors[:50],
        "passed": not errors,
    }


def _nearest_position(step_count: int, fraction: float) -> int:
    if step_count <= 1:
        return 1
    zero_based = int(math.floor((step_count - 1) * fraction + 0.5))
    return zero_based + 1


def select_transition_panel(
    transitions: Sequence[DecisionTransition],
) -> tuple[list[DecisionTransition], dict[str, Any]]:
    by_parent: dict[str, list[DecisionTransition]] = defaultdict(list)
    for transition in transitions:
        by_parent[transition.parent_memory_id].append(transition)
    selected: list[DecisionTransition] = []
    reasons_by_id: dict[str, list[str]] = defaultdict(list)
    for parent_id in sorted(by_parent):
        rows = sorted(by_parent[parent_id], key=lambda item: item.step_index)
        count = len(rows)
        last_noncompletion = next(
            (item.step_index for item in reversed(rows) if not item.completion_related),
            rows[-1].step_index,
        )
        requested = (
            (1, "first"),
            (_nearest_position(count, 1.0 / 3.0), "one_third"),
            (_nearest_position(count, 2.0 / 3.0), "two_thirds"),
            (last_noncompletion, "final_noncompletion"),
        )
        by_step = {item.step_index: item for item in rows}
        for step_index, reason in requested:
            transition = by_step[step_index]
            reasons_by_id[transition.transition_id].append(reason)
        selected.extend(
            by_step[step_index]
            for step_index in sorted({step_index for step_index, _ in requested})
        )
    selected.sort(key=lambda item: (item.parent_task_id, item.step_index, item.transition_id))
    report = {
        "format": TRANSITION_PANEL_VERSION,
        "selection_definition": (
            "per_parent_first_nearest_one_third_nearest_two_thirds_"
            "final_noncompletion_deduplicated_v1"
        ),
        "parent_count": len(by_parent),
        "transition_count": len(selected),
        "completion_transition_count": sum(item.completion_related for item in selected),
        "completion_transition_count_all_extracted": sum(
            item.completion_related for item in transitions
        ),
        "selection_reasons_by_transition_id": dict(reasons_by_id),
        "parent_transition_counts": {
            parent_id: len(rows) for parent_id, rows in sorted(by_parent.items())
        },
    }
    return selected, report
