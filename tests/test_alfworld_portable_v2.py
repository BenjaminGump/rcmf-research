from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys
import types

import pytest
import torch
import scripts.analyze_alfworld_paired_results as paired_analyzer

from rcmf.benchmarks.alfworld.compact_rcmf import (
    CompactMemoryContribution,
    ReversibleCompactField,
    signed_hash_features,
)
from rcmf.benchmarks.alfworld.execution_lock import (
    GENERATION_IDENTITY_V1_SHA256,
    TRACK_R_LOCK_FILE_SHA256,
    TRACK_R_LOCK_IDENTITY_SHA256,
    load_execution_lock,
    lock_identity,
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
from rcmf.benchmarks.alfworld.training import (
    checkpoint_payload,
    contribution_audit_payload,
    create_modules,
    train_writer_epoch,
)
from rcmf.benchmarks.alfworld.runtime_agent import (
    first_decoded_line,
    raw_first_decoded_line,
    run_alfworld_episode,
    run_alfworld_episodes_batched,
    validate_exact_model_snapshot,
    validate_flash_attention_runtime,
)
from rcmf.model.backends.base import GenerateOutput
from rcmf.pipeline.portable_v2.adapter import probe_adapter_capabilities
from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import PortablePhase
from rcmf.pipeline.portable_v2.executor import (
    PortableExecutionIdentity,
    PortableExecutionError,
    PortablePhaseContext,
    execute_and_validate_phase,
)
from rcmf.benchmarks.alfworld.portable_executor_v2_1 import (
    create_alfworld_portable_executor_v2_1,
    evidence_phase_handlers,
)
from rcmf.pipeline.portable_v2.schemas import TaskRecord
from scripts.run_alfworld_expert_isolated_subset import _failure_row
from scripts.run_alfworld_agent import _order_tasks_to_frozen_manifest
from scripts.analyze_alfworld_paired_results import read_rows as read_agent_result_rows


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


def test_isolated_replay_hard_timeout_is_a_typed_valid_row() -> None:
    row = _failure_row(
        _task(),
        run_uuid="fixture-run",
        source_commit="a" * 40,
        hard_timeout_seconds=60,
        message="fixture timeout",
    )
    validate_corpus_row(row)
    assert row["status"] == "EXPERT_TIMEOUT"
    assert row["error"]["type"] == "IsolatedReplayProcessGroupTimeout"


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


def test_frozen_evaluation_order_is_manifest_owned_and_fail_closed(tmp_path: Path) -> None:
    tasks = [_task("valid_unseen"), _task("train")]
    rows = [
        {"task_id": tasks[1].task_id, "split": "train"},
        {"task_id": tasks[0].task_id, "split": "valid_unseen"},
    ]
    expected = canonical_sha256([tasks[0].task_id])
    ordered = _order_tasks_to_frozen_manifest(
        [tasks[0]],
        rows,
        split="valid_unseen",
        expected_order_sha256=expected,
    )
    assert [task.task_id for task in ordered] == [tasks[0].task_id]
    with pytest.raises(ValueError, match="evaluation order"):
        _order_tasks_to_frozen_manifest(
            [tasks[0]],
            rows,
            split="valid_unseen",
            expected_order_sha256="0" * 64,
        )


def test_paired_analyzer_rejects_sorted_instead_of_frozen_order(tmp_path: Path) -> None:
    rows = []
    for index in range(134):
        task_id = f"alfworld:fixture-{index:03d}"
        rows.append(
            {
                "task_id": task_id,
                "condition": "bare",
                "run_identity": {
                    "track_id": TRACK_R_ID,
                    "task_list_role": "formal_track_r_complete",
                    "task_ids_sha256": TRACK_R_TASK_IDS_SHA256,
                    "evaluation_order_sha256": canonical_sha256(
                        [f"alfworld:fixture-{item:03d}" for item in range(134)]
                    ),
                },
            }
        )
    path = tmp_path / "sorted.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="evaluation order differs from frozen lock"):
        read_agent_result_rows(path, "bare")


def test_paired_analyzer_rejects_wrong_embedded_lock_identity(
    tmp_path: Path, monkeypatch
) -> None:
    ids = [f"alfworld:fixture-{index:03d}" for index in range(134)]
    order_sha = canonical_sha256(ids)
    set_sha = canonical_sha256(sorted(ids))
    monkeypatch.setattr(paired_analyzer, "TRACK_R_EVALUATION_ORDER_SHA256", order_sha)
    monkeypatch.setattr(paired_analyzer, "TRACK_R_TASK_IDS_SHA256", set_sha)
    identity = {
        "track_id": TRACK_R_ID,
        "task_list_role": "formal_track_r_complete",
        "task_ids_sha256": set_sha,
        "evaluation_order_sha256": order_sha,
        "condition": "bare",
        "benchmark_lock_sha256": TRACK_R_LOCK_FILE_SHA256,
        "benchmark_lock_identity_sha256": TRACK_R_LOCK_IDENTITY_SHA256,
        "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
        "model_revision": "b968826d9c46dd6066d109eabc6255188de91218",
    }
    rows = [
        {
            "task_id": task_id,
            "condition": "bare",
            "generation_identity_sha256": GENERATION_IDENTITY_SHA256,
            "run_identity": dict(identity),
        }
        for task_id in ids
    ]
    correct = tmp_path / "correct.jsonl"
    correct.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    assert len(paired_analyzer.read_rows(correct, "bare")) == 134
    for row in rows:
        row["run_identity"]["benchmark_lock_identity_sha256"] = "0" * 64
    wrong = tmp_path / "wrong-lock.jsonl"
    wrong.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="embedded benchmark-lock identity"):
        paired_analyzer.read_rows(wrong, "bare")


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
    assert raw_first_decoded_line("> go to fridge 1\nignored") == "> go to fridge 1"
    assert first_decoded_line("> go to fridge 1\nignored") == "go to fridge 1"
    assert first_decoded_line("  >   think: inspect  ") == "think: inspect"
    assert first_decoded_line(">> go to fridge 1") == "> go to fridge 1"
    assert first_decoded_line(">   ") == ""
    assert first_decoded_line("\nlook") == ""


def test_flash_attention_runtime_requires_exact_version(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "flash_attn",
        types.SimpleNamespace(__version__="2.8.3.post1", __file__=__file__),
    )
    monkeypatch.setitem(
        sys.modules,
        "einops",
        types.SimpleNamespace(__version__="0.8.1"),
    )
    monkeypatch.setattr(
        "rcmf.benchmarks.alfworld.runtime_agent.flash_attention_runtime_entries",
        lambda root: [{"path": "flash_attn/file", "bytes": 1, "sha256": "a" * 64}],
    )
    runtime = validate_flash_attention_runtime()
    assert runtime["version"] == "2.8.3.post1"
    assert runtime["installation_manifest_sha256"] == canonical_sha256(
        [{"path": "flash_attn/file", "bytes": 1, "sha256": "a" * 64}]
    )
    with pytest.raises(RuntimeError, match="installation identity differs"):
        validate_flash_attention_runtime("b" * 64)
    monkeypatch.setitem(
        sys.modules,
        "flash_attn",
        types.SimpleNamespace(__version__="different"),
    )
    with pytest.raises(RuntimeError, match="version differs"):
        validate_flash_attention_runtime()


def test_track_r_execution_lock_is_content_addressed(tmp_path: Path) -> None:
    payload = {
        "schema_version": "alfworld_track_r_execution_lock_v1",
        "status": "FROZEN_FOR_MATCHED_BARE_AND_RCMF_EXECUTION",
        "final_benchmark_lock": True,
        "track": {
            "track_id": "alfworld_upstream_react_valid_unseen_reference_v1",
            "role": "UPSTREAM_PROTOCOL_REFERENCE",
            "expected_task_count": 134,
            "task_ids_sha256": TRACK_R_TASK_IDS_SHA256,
        },
        "model_context": {
            "model_revision": "b968826d9c46dd6066d109eabc6255188de91218",
            "tokenizer_revision": "b968826d9c46dd6066d109eabc6255188de91218",
            "chat_template_sha256": (
                "a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8"
            ),
        },
        "generation": {
            "identity_sha256": GENERATION_IDENTITY_V1_SHA256,
            "environment_action_cap": 49,
            "max_new_tokens": 512,
            "do_sample": False,
            "stopping_criteria": [],
        },
        "runtime_execution": {
            "deterministic_evaluation_order_sha256": (
                "c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8"
            ),
            "attention_implementation": "flash_attention_2",
            "flash_attn_version": "2.8.3.post1",
            "einops_version": "0.8.1",
            "microbatch_max_size": 16,
            "left_padding": "exact attention mask; no truncation",
            "flash_attn_installation_manifest_sha256": "a" * 64,
        },
    }
    payload["lock_identity_sha256"] = lock_identity(payload)
    source = tmp_path / "lock.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    assert load_execution_lock(source)["lock_identity_sha256"] == lock_identity(payload)
    payload["runtime_execution"]["microbatch_max_size"] = 17
    payload["lock_identity_sha256"] = lock_identity(payload)
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime execution identity"):
        load_execution_lock(source)


def test_track_r_v2_execution_lock_matches_harness_portable_identity() -> None:
    source = ROOT / "configs" / "benchmark" / "alfworld" / "track_r_execution_lock_v2.json"
    loaded = load_execution_lock(source)
    assert loaded["lock_identity_sha256"] == TRACK_R_LOCK_IDENTITY_SHA256
    assert loaded["file_sha256"] == TRACK_R_LOCK_FILE_SHA256
    assert loaded["payload"]["generation"]["identity_sha256"] == (
        GENERATION_IDENTITY_SHA256
    )


class _FakeBackend:
    def generate(self, messages, **kwargs):
        del messages, kwargs
        return GenerateOutput(
            text="> look\nignored",
            token_ids=[1, 2],
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            ttft_ms=1.0,
            extra={"memory": {"injector": None}},
        )

    def generate_batch(self, messages_batch, **kwargs):
        del kwargs
        return [
            GenerateOutput(
                text="> look\nignored",
                token_ids=[1, 2],
                usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                ttft_ms=1.0,
                extra={"memory": {"injector": None}, "batch_size": len(messages_batch)},
            )
            for _ in messages_batch
        ]


class _ThinkBackend(_FakeBackend):
    def generate(self, messages, **kwargs):
        del messages, kwargs
        return GenerateOutput(
            text="> think: inspect the room\nignored",
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
    assert row["steps"][0]["raw_model_text"] == "> look\nignored"
    assert row["steps"][0]["first_decoded_line"] == "> look"
    assert row["steps"][0]["parsed_action"] == "look"
    assert row["steps"][0]["executed_action"] == "look"


def test_marker_normalization_precedes_think_observation_rule() -> None:
    adapter = create_alfworld_portable_adapter_v2_1()
    task = adapter.list_tasks()["train"][0]
    row = run_alfworld_episode(
        adapter=adapter,
        task=task,
        backend=_ThinkBackend(),
        condition="bare",
        run_identity={"fixture": True},
    )
    step = row["steps"][0]
    assert step["first_decoded_line"] == "> think: inspect the room"
    assert step["parsed_action"] == "think: inspect the room"
    assert step["executed_action"] == "think: inspect the room"
    assert step["environment_observation"] == "probe observation"
    assert step["prompt_observation"] == "OK."


def test_batched_bare_episodes_keep_independent_runtime_records() -> None:
    adapter = create_alfworld_portable_adapter_v2_1()
    task = adapter.list_tasks()["train"][0]
    rows = run_alfworld_episodes_batched(
        adapter=adapter,
        tasks=[task, task],
        backend=_FakeBackend(),
        condition="bare",
        run_identity={"fixture": True, "generation_batch_size": 2},
        batch_size=2,
    )
    assert len(rows) == 2
    assert all(row["official_success"] is True for row in rows)
    assert all(row["steps"][0]["parsed_action"] == "look" for row in rows)
    assert all(row["steps"][0]["executed_action"] == "look" for row in rows)
    assert all(row["steps"][0]["generation_batch_size"] == 2 for row in rows)


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


def test_deployment_checkpoint_excludes_per_memory_audit_state() -> None:
    config = {
        "feature_dim": 4,
        "key_dim": 4,
        "program_dim": 3,
        "writer_hidden_dim": 5,
        "model_dim": 6,
        "injection_tokens": 2,
        "initial_injection_scale": 0.05,
    }
    modules = create_modules(config)
    field = ReversibleCompactField(key_dim=4, program_dim=3)
    contribution = CompactMemoryContribution(
        memory_id="memory",
        parent_id="trajectory",
        key=torch.ones(4),
        value=torch.ones(3),
        mu=0.5,
        rho=1.0,
    )
    field.add(contribution)
    deployment = checkpoint_payload(
        modules=modules,
        field=field,
        contributions=[contribution],
        config=config,
        metadata={"fixture": True},
    )
    audit = contribution_audit_payload(
        contributions=[contribution],
        field=field,
        metadata={"fixture": True},
    )
    assert deployment["compiled_memory_count"] == 1
    assert not any(key.startswith("contribution_") for key in deployment)
    assert audit["contribution_ids"] == ["memory"]
    assert audit["contribution_mu"].dtype == torch.float64
    assert audit["contribution_rho"].dtype == torch.float64


def test_contribution_audit_preserves_fractional_coefficients_for_field_closure() -> None:
    field = ReversibleCompactField(key_dim=2, program_dim=2)
    contributions = [
        CompactMemoryContribution(
            memory_id=f"memory-{index}",
            parent_id="trajectory",
            key=torch.tensor([float(index + 1), -0.25]),
            value=torch.tensor([0.125, float(index + 2)]),
            mu=1.0 / 7.0,
            rho=1.0 / 3.0,
        )
        for index in range(3)
    ]
    for contribution in contributions:
        field.add(contribution)
    audit = contribution_audit_payload(
        contributions=contributions,
        field=field,
        metadata={"fixture": True},
    )
    rebuilt = ReversibleCompactField(key_dim=2, program_dim=2)
    for index, contribution in enumerate(contributions):
        rebuilt.add(
            CompactMemoryContribution(
                memory_id=contribution.memory_id,
                parent_id=contribution.parent_id,
                key=audit["contribution_keys"][index],
                value=audit["contribution_values"][index],
                mu=float(audit["contribution_mu"][index]),
                rho=float(audit["contribution_rho"][index]),
            )
        )
    assert torch.equal(rebuilt.A, audit["field_A"])
    assert torch.equal(rebuilt.B, audit["field_B"])


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


def test_compact_reader_injection_gradient_ownership() -> None:
    config = {
        "feature_dim": 8,
        "key_dim": 8,
        "program_dim": 4,
        "writer_hidden_dim": 12,
        "model_dim": 16,
        "injection_tokens": 2,
        "initial_injection_scale": 0.05,
    }
    modules = create_modules(config)
    class FrozenEmbeddingModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.embedding = torch.nn.Embedding(32, 16)
            self.requires_grad_(False)

        def get_input_embeddings(self) -> torch.nn.Embedding:
            return self.embedding

    frozen_embedding = FrozenEmbeddingModel()
    memory_z = modules["reader"](torch.randn(2, 4))
    input_ids = torch.tensor([[1, 2, 3], [3, 2, 1]], dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    labels = torch.full_like(input_ids, -100)
    base = frozen_embedding.get_input_embeddings()(input_ids).detach()
    prepared = modules["injector"].prepare_train_inputs(
        frozen_embedding,
        input_ids,
        attention_mask,
        labels,
        memory_z,
    )
    assert not torch.equal(prepared.inputs["inputs_embeds"], base)
    prepared.inputs["inputs_embeds"].square().mean().backward()
    assert all(parameter.grad is None for parameter in frozen_embedding.parameters())
    assert all(parameter.grad is None for parameter in modules["writer"].parameters())
    for name in ("reader", "injector"):
        gradients = [parameter.grad for parameter in modules[name].parameters()]
        assert any(gradient is not None and bool(torch.isfinite(gradient).all()) for gradient in gradients)


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
            input_artifacts={
                "environment_and_data_manifest": evidence,
                "benchmark_execution_lock": evidence,
            },
            output_root=output,
            policy={"fixture": True},
        ),
    )
    assert manifest["passed"] is True
    assert manifest["metadata"]["real_evidence_bound"] is True


def test_real_evidence_phase_handler_rejects_semantically_wrong_evidence(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"passed":true}\n', encoding="utf-8")
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
    with pytest.raises(PortableExecutionError, match="missing required evidence"):
        executor.execute_phase(
            PortablePhaseContext(
                identity=identity,
                phase=PortablePhase.MEMORY_LEDGER,
                dependency_manifests=(),
                input_artifacts={"successful_corpus_manifest": evidence},
                output_root=(tmp_path / "run" / PortablePhase.MEMORY_LEDGER.value).resolve(),
                policy={"fixture": True},
            )
        )
