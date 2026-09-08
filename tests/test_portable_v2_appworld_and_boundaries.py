from __future__ import annotations

import ast
import json
from pathlib import Path

from rcmf.benchmarks.appworld.pipeline_adapter import AppWorldReproduciblePipelineAdapter
from rcmf.benchmarks.appworld.portable_adapter_v2 import AppWorldPortableAdapterV2
from rcmf.benchmarks.appworld.prompt import build_appworld_messages
from rcmf.pipeline.portable_v2.adapter import validate_adapter_capabilities
from rcmf.pipeline.portable_v2.conformance import run_manifest_only_conformance
from rcmf.pipeline.portable_v2.dag import PortableRunMode, PortableRunPolicy
from rcmf.pipeline.portable_v2.prompts import PromptAssetManifest
from rcmf.pipeline.portable_v2.schemas import (
    DecisionStateRecord,
    ReplayStatus,
    ProvenanceClass,
    TaskRecord,
    TerminalStatus,
    TrajectoryRecord,
    TrajectoryStep,
)


ROOT = Path(__file__).resolve().parents[1]


def test_generic_pipeline_has_no_dataset_imports_or_name_dispatch() -> None:
    generic_root = ROOT / "rcmf/pipeline"
    violations = []
    for path in generic_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(token in name.lower() for token in ("appworld", "alfworld", "webshop")):
                    violations.append((str(path.relative_to(ROOT)), node.lineno, name))
        source = path.read_text(encoding="utf-8").lower()
        assert 'benchmark_name == "appworld"' not in source
        assert "benchmark_name == 'appworld'" not in source
    assert violations == []


def test_appworld_portable_adapter_matches_legacy_renderer_on_golden_fixture(
    tmp_path: Path,
) -> None:
    legacy = AppWorldReproduciblePipelineAdapter(
        corpus_root=tmp_path, legacy_root=tmp_path, split_names=()
    )
    task = TaskRecord(
        benchmark="appworld",
        dataset_version="0.1.0",
        split="train",
        task_id="fixture-task",
        instruction="Now here is the task:\nTask: fixture goal",
        lineage_keys=("fixture-task",),
        source_identity={"fixture": True},
    )
    state = DecisionStateRecord(
        state_id="fixture-state",
        task_id=task.task_id,
        trajectory_prefix=(
            {"response": "```python\nx = 1\n```", "observation": "Output:\n```\n1\n```"},
        ),
        current_observation="state",
        target_action_reference={"action": "x = 2"},
        model_split="train",
        provenance=ProvenanceClass.OFFICIAL_MODEL_OR_IL,
        prompt_profile="full_demo_first_only",
        environment_replay_reference={"fixture": True},
        metadata={"task_message": task.instruction},
    )
    adapter = AppWorldPortableAdapterV2(
        legacy,
        dataset_version="0.1.0",
        environment_version="0.1.0",
        prompt_manifest_hashes={"full_demo": "a" * 64, "full_demo_first_only": "b" * 64},
        task_records={"train": (task,)},
        trajectory_records={"train": ()},
        token_counter=lambda messages, profile: len(json.dumps(list(messages), sort_keys=True)),
    )
    expected = build_appworld_messages(
        task.instruction,
        list(state.trajectory_prefix),
        prompt_profile="full_demo_first_only",
    )
    actual = adapter.render_messages(state, "full_demo_first_only")
    assert actual == expected
    assert adapter.count_runtime_tokens(actual, "full_demo_first_only") == len(
        json.dumps(list(expected), sort_keys=True)
    )
    assert validate_adapter_capabilities(adapter)["passed"]


def test_appworld_compatibility_adapter_traverses_complete_generic_dag(tmp_path: Path) -> None:
    legacy = AppWorldReproduciblePipelineAdapter(
        corpus_root=tmp_path, legacy_root=tmp_path, split_names=()
    )
    task = TaskRecord(
        benchmark="appworld",
        dataset_version="0.1.0",
        split="train",
        task_id="fixture-task",
        instruction="Now here is the task:\nTask: fixture goal",
        lineage_keys=("fixture-task",),
        source_identity={"fixture": True},
    )
    trajectory = TrajectoryRecord(
        trajectory_id="fixture-trajectory",
        task_id=task.task_id,
        provenance=ProvenanceClass.OFFICIAL_MODEL_OR_IL,
        source_identity={"fixture": True},
        steps=(
            TrajectoryStep(
                step_index=0,
                pre_action_state="state",
                action="x = 1",
                post_action_observation="Output:\n```\n1\n```",
                raw_reward=1.0,
                terminal_status=TerminalStatus.SUCCESS,
            ),
        ),
        raw_reward=1.0,
        success=True,
        terminal_status=TerminalStatus.SUCCESS,
        replay_status=ReplayStatus.VALIDATED,
        environment_identity={"fixture": True},
        metadata={"goal": task.instruction},
    )
    adapter = AppWorldPortableAdapterV2(
        legacy,
        dataset_version="0.1.0",
        environment_version="0.1.0",
        prompt_manifest_hashes={"full_demo": "a" * 64, "full_demo_first_only": "b" * 64},
        task_records={"train": (task,)},
        trajectory_records={"train": (trajectory,)},
        token_counter=lambda messages, profile: len(json.dumps(list(messages), sort_keys=True)),
    )
    result = run_manifest_only_conformance(
        adapter=adapter,
        policy=PortableRunPolicy(
            PortableRunMode.FULL, "full_demo_first_only", 2, 25101
        ),
        run_root=tmp_path / "appworld-portable",
    )
    assert result["passed"] is True
    assert result["stage_count"] == 12
    assert result["counts"] == {
        "splits": {"train": 1},
        "tasks": 1,
        "trajectories": 1,
        "transitions": 1,
        "decision_states": 1,
    }


def test_pinned_react_prompt_assets_and_action_grammars_are_exact() -> None:
    alf_manifest = PromptAssetManifest.load(
        ROOT / "assets/prompts/source_manifests/react_alfworld.json"
    )
    shop_manifest = PromptAssetManifest.load(
        ROOT / "assets/prompts/source_manifests/react_webshop.json"
    )
    assert alf_manifest.upstream_blob == "0e7c204818e9aa3128d266da5512d1ec3d9ea13e"
    assert shop_manifest.upstream_blob == "67cb9f878de242951a7804a31bb5da4264111a29"
    alf_profile = json.loads(
        (ROOT / "assets/prompts/alfworld/react_task_type_two_demo_v1/profile.json").read_text()
    )
    assert alf_profile["example_suffix_order"] == ["1", "0"]
    assert alf_profile["task_family_prefixes"]["pick_two_obj"] == "puttwo"
    shop = (ROOT / "assets/prompts/webshop/react_official_one_demo_v1/prompt1.txt").read_text()
    for marker in ("Webshop", "Instruction:", "Action: search[", "Action: think[", "Action: click["):
        assert marker in shop
    assert shop.endswith("Action: click[Buy Now]\n")
