from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
import torch

from rcmf.benchmarks.alfworld.compact_rcmf import (
    CompactMemoryContribution,
    ReversibleCompactField,
    signed_hash_features,
)
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
from rcmf.benchmarks.alfworld.training import create_modules, train_writer_epoch
from rcmf.benchmarks.alfworld.runtime_agent import (
    first_decoded_line,
    run_alfworld_episode,
    validate_exact_model_snapshot,
)
from rcmf.model.backends.base import GenerateOutput
from rcmf.pipeline.portable_v2.adapter import probe_adapter_capabilities
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import PortablePhase
from rcmf.pipeline.portable_v2.executor import (
    PortableExecutionIdentity,
    PortablePhaseContext,
    execute_and_validate_phase,
)
from rcmf.benchmarks.alfworld.portable_executor_v2_1 import (
    create_alfworld_portable_executor_v2_1,
    evidence_phase_handlers,
)
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


def test_exact_snapshot_and_first_line_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact revision"):
        validate_exact_model_snapshot(tmp_path)
    assert first_decoded_line("look\nignored continuation") == "look"
    assert first_decoded_line("\nlook") == ""


class _FakeBackend:
    def generate(self, messages, **kwargs):
        del messages, kwargs
        return GenerateOutput(
            text="look\nignored",
            token_ids=[1, 2],
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            ttft_ms=1.0,
            extra={"memory": {"injector": None}},
        )


def test_bare_episode_records_exact_action_and_official_result() -> None:
    adapter = create_alfworld_portable_adapter_v2_1()
    task = adapter.list_tasks()["train"][0]
    row = run_alfworld_episode(
        adapter=adapter,
        task=task,
        backend=_FakeBackend(),
        condition="bare",
        run_identity={"fixture": True},
    )
    assert row["official_success"] is True
    assert row["steps"][0]["raw_model_text"] == "look\nignored"
    assert row["steps"][0]["parsed_action"] == "look"


def test_compact_field_is_fixed_reversible_and_permutation_invariant() -> None:
    field = ReversibleCompactField(key_dim=4, program_dim=3)
    rows = [
        CompactMemoryContribution(
            memory_id=f"memory-{index}",
            parent_id="trajectory",
            key=torch.tensor([1.0, float(index), 0.0, -1.0]),
            value=torch.tensor([0.5, float(index + 1), -0.25]),
            mu=0.5,
            rho=0.5,
        )
        for index in range(2)
    ]
    empty_shapes = field.field_shape
    for row in rows:
        field.add(row)
    baseline_a, baseline_b = field.A.clone(), field.B.clone()
    removed = field.remove("memory-0")
    field.restore(removed)
    assert torch.equal(field.A, baseline_a)
    assert torch.equal(field.B, baseline_b)
    reverse_a, reverse_b = field.rebuild(reversed(sorted(field.records)))
    assert torch.allclose(reverse_a, baseline_a, atol=1e-15, rtol=0)
    assert torch.allclose(reverse_b, baseline_b, atol=1e-15, rtol=0)
    assert field.field_shape == empty_shapes
    assert tuple(field.read(torch.ones(4)).shape) == (3,)


def test_signed_hash_features_are_stable_and_fixed_shape() -> None:
    first = signed_hash_features("take apple 1 from table 1", 32)
    second = signed_hash_features("take apple 1 from table 1", 32)
    assert torch.equal(first, second)
    assert tuple(first.shape) == (32,)
    assert torch.isfinite(first).all()


def test_compact_writer_receives_finite_train_only_supervision() -> None:
    config = {
        "feature_dim": 16,
        "key_dim": 16,
        "program_dim": 8,
        "writer_hidden_dim": 12,
        "model_dim": 24,
        "injection_tokens": 2,
        "initial_injection_scale": 0.05,
        "batch_size": 2,
        "seed": 25101,
        "gradient_clip_norm": 1.0,
    }
    modules = create_modules(config)
    optimizer = torch.optim.AdamW(
        list(modules["writer"].parameters())
        + list(modules["query_encoder"].parameters()),
        lr=1e-3,
    )
    metrics = train_writer_epoch(
        rows=(
            {
                "split": "train",
                "transition_text": "goal state take apple observation",
                "state_text": "goal state",
                "action": "take apple",
            },
            {
                "split": "train",
                "transition_text": "goal next put apple observation",
                "state_text": "goal next",
                "action": "put apple",
            },
        ),
        writer=modules["writer"],
        query_encoder=modules["query_encoder"],
        optimizer=optimizer,
        config=config,
        epoch=1,
        device=torch.device("cpu"),
    )
    assert metrics["batches"] == 1
    assert torch.isfinite(torch.tensor(metrics["mean_loss"]))


def test_real_evidence_phase_handler_seals_a_portable_manifest(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"passed":true}\n', encoding="utf-8")
    output = (tmp_path / "run" / PortablePhase.PROVENANCE.value).resolve()
    identity = PortableExecutionIdentity(
        source_commit="a" * 40,
        run_uuid="fixture-run",
        run_root=(tmp_path / "run").resolve(),
        pipeline_config_sha256="b" * 64,
        dataset_profile_sha256="c" * 64,
        adapter_identity=(
            "rcmf.benchmarks.alfworld.portable_adapter_v2:"
            "create_alfworld_portable_adapter_v2_1"
        ),
    )
    executor = create_alfworld_portable_executor_v2_1(
        phase_handlers=evidence_phase_handlers()
    )
    manifest = execute_and_validate_phase(
        executor,
        PortablePhaseContext(
            identity=identity,
            phase=PortablePhase.PROVENANCE,
            dependency_manifests=(),
            input_artifacts={"environment": evidence},
            output_root=output,
            policy={"fixture": True},
        ),
    )
    assert manifest["passed"] is True
    assert manifest["metadata"]["real_evidence_bound"] is True
