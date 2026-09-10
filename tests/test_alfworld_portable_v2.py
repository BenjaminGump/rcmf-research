from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from rcmf.benchmarks.alfworld.portable_adapter_v2 import (
    ACTION_CAP,
    GENERATION_IDENTITY_SHA256,
    TRACK_R_ID,
    create_alfworld_portable_adapter_v2_1,
)
from rcmf.benchmarks.alfworld.prompt_profile import (
    PROFILE_NAME,
    load_profile,
    render_react_messages,
    render_react_trajectory,
)
from rcmf.benchmarks.alfworld.task_manifest import (
    EXPECTED_SPLIT_COUNTS,
    TASK_MANIFEST_SHA256,
    TRACK_R_TASK_IDS_SHA256,
    canonical_sha256,
)
from rcmf.benchmarks.alfworld.trajectories import (
    ALFWorldOfficialExpertTrajectoryProvider,
    portable_trajectory,
    validate_corpus_row,
)
from rcmf.pipeline.portable_v2.adapter import probe_adapter_capabilities
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.schemas import TaskRecord


ROOT = Path(__file__).resolve().parents[1]
PROMPT_ROOT = ROOT / "assets" / "prompts" / "alfworld" / PROFILE_NAME


def _task(split: str = "train") -> TaskRecord:
    return TaskRecord(
        benchmark="alfworld",
        dataset_version="alfworld-json-2.1.1",
        split=split,
        task_id=f"alfworld:fixture-{split}",
        instruction="put an apple on a table",
        lineage_keys=(f"trial-{split}", f"lineage-{split}", "scene"),
        source_identity={"fixture": True},
        metadata={
            "game_path": f"{split}/pick_and_place_simple/fixture/game.tw-pddl",
            "task_family": "pick_and_place",
        },
    )


class _ExpertRuntime:
    def __init__(self, task: TaskRecord, **_: object) -> None:
        self.task = task
        self.initial_observation = "banner\n\nroom"
        self.observation = self.initial_observation
        self.commands = iter(("look", "take apple 1 from table 1"))
        self.index = 0

    def expert_command(self) -> str:
        return next(self.commands)

    def step(self, action: str) -> dict[str, object]:
        done = self.index == 1
        self.index += 1
        self.observation = f"observation {self.index}"
        return {
            "action": action,
            "observation": self.observation,
            "raw_reward": 1.0 if done else 0.0,
            "done": done,
            "official_won": done,
            "step": self.index - 1,
        }

    def close(self) -> None:
        return None


def test_exact_react_prompt_and_dynamic_notebook_transcript() -> None:
    _, asset = load_profile(PROMPT_ROOT)
    messages = render_react_messages(
        profile_root=PROMPT_ROOT,
        gamefile="valid_unseen/pick_and_place_simple/trial/game.tw-pddl",
        current_trajectory="fixture observation\n>",
    )
    assert canonical_sha256(list(messages)) == (
        "15edd83853c8d4de7a8b4df0438e88ea3d773a63024e49378218c01336b16848"
    )
    assert asset["react_put_1"] + asset["react_put_0"] in messages[0]["content"]
    assert render_react_trajectory(
        "banner\n\nroom",
        ({"action": "look", "observation": "seen"},),
    ) == "room\n> look\nseen\n>"


def test_official_expert_provider_is_train_only_and_complete(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    provider = ALFWorldOfficialExpertTrajectoryProvider(
        data_root=data,
        identity_bindings={"source": "fixture"},
        runtime_factory=_ExpertRuntime,
    )
    row = provider.replay(_task())
    validate_corpus_row(row)
    assert row["status"] == "SUCCESS"
    assert [step["action"] for step in row["steps"]] == ["look", "take apple 1 from table 1"]
    trajectory = portable_trajectory(row, source_identity={"fixture": True})
    assert trajectory.success and len(trajectory.steps) == 2
    with pytest.raises(ValueError, match="non-TRAIN"):
        provider.replay(_task("valid_unseen"))


def test_malformed_success_is_rejected() -> None:
    with pytest.raises(ValueError, match="successful replay"):
        validate_corpus_row(
            {
                "schema_version": "alfworld_official_expert_corpus_v1",
                "provider": "alfworld_official_expert_trajectory_provider_v1",
                "provenance": "OFFICIAL_EXPERT",
                "split": "train",
                "status": "SUCCESS",
                "success": True,
                "terminal": True,
                "initial_observation": "room",
                "steps": [],
                "sequence_sha256": canonical_sha256(
                    {"initial_observation": "room", "steps": []}
                ),
            }
        )


def test_adapter_capability_probe_and_config_binding() -> None:
    adapter = create_alfworld_portable_adapter_v2_1()
    report = probe_adapter_capabilities(adapter, prompt_profile=PROFILE_NAME)
    assert report["passed"] is True
    identity = adapter.identity()
    assert identity.benchmark_version == TRACK_R_ID
    assert identity.metadata["generation_identity_sha256"] == GENERATION_IDENTITY_SHA256
    assert identity.metadata["action_cap"] == ACTION_CAP
    config = PortablePipelineConfig.load(ROOT / "configs" / "pipeline" / "rcmf_alfworld_v1.yaml")
    assert config.validate_bindings()["passed"] is True


def test_sealed_population_constants_and_generic_diff_guard() -> None:
    assert EXPECTED_SPLIT_COUNTS == {
        "train": 3553,
        "valid_train": 200,
        "valid_seen": 140,
        "valid_unseen": 134,
    }
    assert len(TASK_MANIFEST_SHA256) == len(TRACK_R_TASK_IDS_SHA256) == 64
    violations = []
    for path in (ROOT / "rcmf" / "pipeline").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                module = " ".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
            if any(name in module.lower() for name in ("alfworld", "appworld", "webshop")):
                violations.append((str(path), node.lineno, module))
    assert violations == []


def test_no_floating_qwen_revision_in_alfworld_source() -> None:
    source = (ROOT / "rcmf" / "benchmarks" / "alfworld" / "portable_adapter_v2.py").read_text(
        encoding="utf-8"
    )
    assert "b968826d9c46dd6066d109eabc6255188de91218" in source
    assert "Qwen/Qwen3-8B@main" not in source
