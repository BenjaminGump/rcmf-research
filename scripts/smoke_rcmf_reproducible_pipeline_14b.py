#!/usr/bin/env python3
"""Bounded non-scientific end-to-end smoke for EXP-037A."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

os.environ.setdefault("PYTHONHASHSEED", "25101")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch

from rcmf.benchmarks.appworld.pipeline_adapter import (
    AppWorldReproduciblePipelineAdapter,
)
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    _arm_config,
    initialize_runtime_layout,
)
from rcmf.config import load_config
from rcmf.factory import build_backend
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.manifests import content_sha256, file_identity
from rcmf.pipeline.scheduler import EventDrivenScheduler
from rcmf.pipeline.stage_graph import build_exp037a_stage_graph
from rcmf.training.datasets import load_decision_examples
from rcmf.training.multiview_representations_6c import (
    LAYER_CANDIDATES,
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    flatten_multiview_readouts,
    frozen_qwen_span_readouts,
    query_state_text_and_char_spans,
    tokenize_and_validate_char_spans,
    transition_text_and_char_spans,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    AlignedTransitionWriter,
    FieldReaderHooks,
    FrozenSelectorDecomposition,
    RCMFFieldRecord,
    ReversibleRCMFField,
    StandardFieldCrossAttentionReader,
    compile_differentiable_field,
    deterministic_payload_permutation,
    read_compiled_field,
    tensor_sha256,
)
from rcmf.training.signature_balanced_field_7c import train_field_selector
from rcmf.training.transition_memory_6a import (
    example_task_id,
    state_example_id,
)
from rcmf.utils.serialization import (
    atomic_write_json,
    ensure_dir,
    read_jsonl,
    sha256_file,
)
from scripts.prepare_rcmf_reproducible_pipeline_14b import load_resolved
from scripts.run_rcmf_joint_full_bank_first37_9a import _run_task
from scripts.run_signature_balanced_field_7c import _selector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"),
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--local-tests", type=Path, required=True)
    parser.add_argument("--lambda-tests", type=Path, required=True)
    parser.add_argument("--real-historical-integration-report", type=Path)
    return parser.parse_args()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _state_readout(
    backend: Any, example: Any, prompt_profile: str
) -> tuple[torch.Tensor, dict[str, Any]]:
    started = time.perf_counter()
    rendered, spans, metadata = query_state_text_and_char_spans(
        backend.tokenizer, example, prompt_profile
    )
    input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
        backend.tokenizer, rendered, spans
    )
    nested = frozen_qwen_span_readouts(
        model=backend.model,
        input_ids=input_ids,
        attention_mask=attention_mask,
        span_rows=span_rows,
        device=backend.device,
    )
    values = flatten_multiview_readouts(
        [{"readouts": nested}],
        layer="final_layer",
        view_names=STATE_VIEW_NAMES,
    )[0].to(torch.float32)
    return values, {
        "prompt_profile": prompt_profile,
        "rendered_sha256": content_sha256(rendered),
        "token_count": int(input_ids.shape[1]),
        "span_count": len(span_rows),
        "source_metadata": metadata,
        "tensor_sha256": tensor_sha256(values),
        "elapsed_seconds": time.perf_counter() - started,
        "truncated": False,
    }


def _transition_readout(
    backend: Any, transition: Mapping[str, Any]
) -> tuple[torch.Tensor, dict[str, Any]]:
    started = time.perf_counter()
    rendered, spans, metadata = transition_text_and_char_spans(transition)
    input_ids, attention_mask, span_rows = tokenize_and_validate_char_spans(
        backend.tokenizer, rendered, spans
    )
    nested = frozen_qwen_span_readouts(
        model=backend.model,
        input_ids=input_ids,
        attention_mask=attention_mask,
        span_rows=span_rows,
        device=backend.device,
    )
    values = flatten_multiview_readouts(
        [{"readouts": nested}],
        layer="final_layer",
        view_names=TRANSITION_VIEW_NAMES,
    )[0].to(torch.float32)
    return values, {
        "transition_id": str(transition["transition_id"]),
        "rendered_sha256": content_sha256(rendered),
        "token_count": int(input_ids.shape[1]),
        "span_count": len(span_rows),
        "source_metadata": metadata,
        "tensor_sha256": tensor_sha256(values),
        "elapsed_seconds": time.perf_counter() - started,
        "prompt_demonstrations_used": False,
        "truncated": False,
    }


def _target_batch(backend: Any, messages: Sequence[Mapping[str, str]], target: str) -> dict[str, torch.Tensor]:
    base = backend.render_messages(list(messages), add_generation_prompt=True)
    target_ids = backend.tokenizer(target, add_special_tokens=False)["input_ids"][:16]
    target_text = backend.tokenizer.decode(target_ids, skip_special_tokens=False)
    full = backend.tokenizer(base + target_text, return_tensors="pt")
    prefix = backend.tokenizer(base, return_tensors="pt")["input_ids"].shape[1]
    labels = full["input_ids"].clone()
    labels[:, :prefix] = -100
    return {
        "input_ids": full["input_ids"].to(backend.device),
        "attention_mask": full.get(
            "attention_mask", torch.ones_like(full["input_ids"])
        ).to(backend.device),
        "labels": labels.to(backend.device),
    }


class _SmokeFieldRuntime:
    def __init__(
        self,
        *,
        reader: StandardFieldCrossAttentionReader,
        correct: torch.Tensor,
        shuffled: torch.Tensor,
        state_views: torch.Tensor,
        query: torch.Tensor,
    ) -> None:
        self.reader = reader
        self.correct = correct
        self.shuffled = shuffled
        self.state_views = state_views
        self.query = query
        self.query_encoder = SimpleNamespace(
            identity_sha256=content_sha256(
                {
                    "format": "exp037a_technical_smoke_fixed_query_v1",
                    "query": tensor_sha256(query),
                }
            )
        )

    def read(
        self, messages: Sequence[Mapping[str, str]], condition: str
    ) -> tuple[torch.Tensor, dict[str, Any]]:
        del messages
        slots = self.shuffled if condition == "D2" else self.correct
        return slots, {
            "state_views": self.state_views,
            "query": self.query,
            "query_seconds": 0.0,
            "field_read_seconds": 0.0,
            "field_control": (
                "key_payload_shuffle" if condition == "D2" else "correct"
            ),
            "technical_smoke_fixed_query": True,
        }


def _write_mock_output(
    stage: StageSpec, stage_dir: Path, source_commit: str, gate: bool
) -> None:
    payload = stage_dir / "payload.json"
    atomic_write_json(payload, {"stage": stage.stage_id})
    if stage.stage_id == "D22_three_demo_reproduction_gate":
        atomic_write_json(
            stage_dir / "gate.json",
            {
                "decision": (
                    "THREE_DEMO_REPRODUCTION_PASS"
                    if gate
                    else "THREE_DEMO_REPRODUCTION_NOT_ESTABLISHED"
                ),
                "continue_to_one_demo": gate,
            },
        )
    atomic_write_json(
        stage_dir / "output_manifest.json",
        {
            "format": "exp037a_mock_stage_output_14b_v1",
            "stage_id": stage.stage_id,
            "source_commit": source_commit,
            "passed": True,
            "outputs": [
                {"path": "payload.json", "sha256": sha256_file(payload)}
            ],
        },
    )


def _scheduler_smoke(
    source_commit: str, hard_cap_hours: float
) -> dict[str, Any]:
    stages = build_exp037a_stage_graph()
    contract = PipelineContract(
        schema_version="exp037a_mock_smoke_v1",
        run_uuid="exp037a-mock-smoke",
        source_commit=source_commit,
        global_seed=25101,
        hard_cap_hours=hard_cap_hours,
        stages=stages,
        arms={
            "3d": ArmContract("3d", "full_demo", "arms/3d", "mock-3d"),
            "1d": ArmContract(
                "1d", "full_demo_first_only", "arms/1d", "mock-1d"
            ),
        },
        metadata={
            "maximum_recoverable_attempts_per_stage": 3,
            "recoverable_retry_delay_seconds": 0,
        },
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config_path = root / "config.json"
        atomic_write_json(config_path, {})
        atomic_write_json(
            root / "runtime_authorization.json",
            {
                "authorized": True,
                "hard_cap_hours": hard_cap_hours,
                "source_commit": source_commit,
            },
        )
        pass_calls: list[str] = []

        def pass_runner(
            stage: StageSpec,
            command: Sequence[str],
            stage_dir: Path,
            environment: Mapping[str, str],
        ) -> int:
            del command
            if environment.get("PYTHONHASHSEED") != "25101":
                return 65
            pass_calls.append(stage.stage_id)
            _write_mock_output(stage, stage_dir, source_commit, True)
            return 0

        passed = EventDrivenScheduler(
            contract,
            root,
            python_executable="python",
            config_path=config_path,
            runner=pass_runner,
            heartbeat_interval_seconds=0.01,
            transition_target_seconds=60,
        ).run()
        transition_max = max(
            row["stage_to_next_transition_seconds"] for row in passed.transitions
        )
        pass_summary = {
            "status": passed.status,
            "stage_count": len(pass_calls),
            "one_demo_launched": "O00_state_representations" in pass_calls,
            "maximum_transition_seconds": transition_max,
        }

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config_path = root / "config.json"
        atomic_write_json(config_path, {})
        atomic_write_json(
            root / "runtime_authorization.json",
            {
                "authorized": True,
                "hard_cap_hours": hard_cap_hours,
                "source_commit": source_commit,
            },
        )
        fail_calls: list[str] = []

        def fail_runner(
            stage: StageSpec,
            command: Sequence[str],
            stage_dir: Path,
            environment: Mapping[str, str],
        ) -> int:
            del command, environment
            fail_calls.append(stage.stage_id)
            _write_mock_output(stage, stage_dir, source_commit, False)
            return 0

        stopped = EventDrivenScheduler(
            contract,
            root,
            python_executable="python",
            config_path=config_path,
            runner=fail_runner,
            heartbeat_interval_seconds=0.01,
        ).run()
        fail_summary = {
            "status": stopped.status,
            "stage_count": len(fail_calls),
            "one_demo_launched": any(value.startswith("O") for value in fail_calls),
        }
    return {
        "pass_gate": pass_summary,
        "fail_gate": fail_summary,
        "passed": (
            pass_summary["status"] == "complete"
            and pass_summary["stage_count"] == len(stages)
            and pass_summary["one_demo_launched"]
            and transition_max <= 60
            and fail_summary["status"] == "complete"
            and not fail_summary["one_demo_launched"]
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    config = load_resolved(args.config)
    pipeline = config["pipeline"]
    ensure_dir(args.output.parent)
    initialize_runtime_layout(config, args.run_root)
    cfg = load_config(_arm_config(args.run_root, "3d"))
    backend = build_backend(cfg, load_model=True)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    if backend.device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(backend.device)

    corpus = Path(str(pipeline["roots"]["authoritative_corpus"]))
    split = _json(corpus / "train_validation_task_manifest.json")
    train_tasks = set(map(str, split["train_task_ids"]))
    examples = load_decision_examples(corpus / "decision_examples.jsonl")
    selected_index, example = next(
        (index, row)
        for index, row in enumerate(examples)
        if example_task_id(row) in train_tasks
    )
    selected_state_id = state_example_id(selected_index, example)
    state_values: dict[str, torch.Tensor] = {}
    state_rows: dict[str, Any] = {}
    for arm_id, prompt_profile in (
        ("3d", "full_demo"),
        ("1d", "full_demo_first_only"),
    ):
        state_values[arm_id], state_rows[arm_id] = _state_readout(
            backend, example, prompt_profile
        )

    labels = [
        dict(row)
        for row in read_jsonl(args.run_root / "preflight/shared/labels.jsonl")
        if str(row["state_example_id"]) == selected_state_id
        and str(row["cell"]) == "A"
    ]
    by_class: dict[str, dict[str, Any]] = {}
    for row in sorted(labels, key=lambda value: str(value["transition_id"])):
        by_class.setdefault(str(row["signature_class_id"]), row)
    selected_labels = list(by_class.values())[:4]
    if len(selected_labels) != 4:
        raise RuntimeError("Smoke state lacks four distinct A-cell classes")
    transition_ids = [str(row["transition_id"]) for row in selected_labels]
    transitions = {
        str(row["transition_id"]): dict(row)
        for row in read_jsonl(corpus / "transition_manifest.jsonl")
        if str(row["transition_id"]) in set(transition_ids)
    }
    transition_values = []
    transition_rows = []
    for transition_id in transition_ids:
        values, row = _transition_readout(backend, transitions[transition_id])
        transition_values.append(values)
        transition_rows.append(row)
    transition_tensor = torch.stack(transition_values)

    selector_settings = pipeline["selector"]
    candidate = {**selector_settings["candidates"][0], "epochs": 1}
    selector = _selector(selector_settings, 25101)
    selector_started = time.perf_counter()
    selector_result = train_field_selector(
        model=selector,
        rows=selected_labels,
        state_representations=state_values["3d"].unsqueeze(0),
        transition_representations=transition_tensor,
        ordered_state_ids=[selected_state_id],
        ordered_transition_ids=transition_ids,
        candidate=candidate,
        batch_states=1,
        maximum_pair_samples_per_state=16,
        maximum_hard_samples_per_state=8,
        weight_decay=float(selector_settings["weight_decay"]),
        seed=25101,
        device=backend.device,
        checkpoint_interval_epochs=1,
    )
    selector_seconds = time.perf_counter() - selector_started
    final_models = [
        _selector(selector_settings, int(seed))
        for seed in pipeline["final_selector_member_seeds"]
    ]
    decomposition = FrozenSelectorDecomposition(
        models=final_models,
        train_means=[0.0, 0.0, 0.0],
        train_stds=[1.0, 1.0, 1.0],
    ).to(backend.device)
    state_for_field = state_values["1d"].unsqueeze(0).to(backend.device)
    transition_for_field = transition_tensor.to(backend.device)
    query = decomposition.query(state_for_field)[0]
    keys = decomposition.key(transition_for_field)

    writer = AlignedTransitionWriter().to(backend.device, torch.float32)
    reader = StandardFieldCrossAttentionReader().to(
        backend.device, torch.float32
    )
    writer.load_state_dict(
        torch.load(
            args.run_root / "preflight/initialization_snapshots/writer_initial.pt",
            map_location="cpu",
            weights_only=False,
        ),
        strict=True,
    )
    reader.load_state_dict(
        torch.load(
            args.run_root / "preflight/initialization_snapshots/reader_initial.pt",
            map_location="cpu",
            weights_only=False,
        ),
        strict=True,
    )
    optimizer = torch.optim.AdamW(
        [*writer.parameters(), *reader.parameters()],
        lr=1.0e-4,
        weight_decay=1.0e-4,
    )
    adapter = AppWorldReproduciblePipelineAdapter(
        corpus_root=corpus,
        legacy_root=pipeline["required_environment"]["legacy_root"],
    )
    example_mapping = example.to_dict()
    example_mapping["task_message"] = example.state_text
    causal_conditions = adapter.build_causal_teacher_conditions(
        example_mapping,
        transitions[transition_ids[0]],
        "full_demo_first_only",
    )
    paired_generation = []
    for condition in causal_conditions:
        generation_started = time.perf_counter()
        generation = backend.generate(
            list(condition["messages"]),
            max_new_tokens=16,
            temperature=0.0,
            top_p=1.0,
        )
        paired_generation.append(
            {
                "condition": condition["condition"],
                "token_ids": generation.token_ids,
                "text_sha256": content_sha256(generation.text),
                "usage": generation.usage,
                "elapsed_seconds": time.perf_counter() - generation_started,
            }
        )
    teacher_started = time.perf_counter()
    teacher_score = backend.score_targets(
        list(causal_conditions[1]["messages"]), [example.target_text]
    )[0]
    teacher_seconds = time.perf_counter() - teacher_started

    gradient_rows = []
    batch = _target_batch(
        backend,
        list(causal_conditions[0]["messages"]),
        example.target_text,
    )
    memory_views = transition_for_field[:, :8, :]
    for update in (1, 2):
        backward_started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        payloads = writer(memory_views)
        rho = torch.full(
            (len(payloads),),
            1.0 / len(payloads),
            device=backend.device,
            dtype=torch.float32,
        )
        A, B = compile_differentiable_field(
            keys=keys, payloads=payloads, rho=rho
        )
        slots = read_compiled_field(
            query=query, A=A, B=B, nonempty=True
        )
        hooks = FieldReaderHooks(
            model=backend.model, reader=reader, slots=slots
        )
        with hooks, torch.autocast(
            device_type="cuda",
            dtype=torch.bfloat16,
            enabled=backend.device.type == "cuda",
        ):
            output = backend.forward_train(**batch)
            if output.loss is None:
                raise RuntimeError("Smoke target forward produced no loss")
            loss = (
                output.loss
                + 0.001 * hooks.residual_penalty()
                + 0.0001 * payloads.square().mean()
            )
            loss.backward()
        optimizer.step()
        gradient_rows.append(
            {
                "update": update,
                "loss": float(loss.detach().cpu()),
                "writer_any_gradient": any(
                    parameter.grad is not None
                    and bool(parameter.grad.detach().abs().any())
                    for parameter in writer.parameters()
                ),
                "reader_any_gradient": any(
                    parameter.grad is not None
                    and bool(parameter.grad.detach().abs().any())
                    for parameter in reader.parameters()
                ),
                "elapsed_seconds": time.perf_counter() - backward_started,
            }
        )
    if any(parameter.grad is not None for parameter in backend.model.parameters()):
        raise RuntimeError("Frozen Qwen accumulated gradients in technical smoke")

    with torch.no_grad():
        base_payloads = writer(memory_views)
        repeated_payloads = base_payloads[
            torch.arange(401, device=backend.device) % len(base_payloads)
        ]
        repeated_keys = keys[
            torch.arange(401, device=backend.device) % len(keys)
        ]
        parents = [f"parent-{index % 37:02d}" for index in range(401)]
        parent_counts = {
            parent: parents.count(parent) for parent in sorted(set(parents))
        }
        rho_401 = torch.tensor(
            [1.0 / parent_counts[parent] for parent in parents],
            device=backend.device,
        )
        A_401, B_401 = compile_differentiable_field(
            keys=repeated_keys,
            payloads=repeated_payloads,
            rho=rho_401,
        )
        permutation = deterministic_payload_permutation(
            [
                {
                    "transition_id": f"smoke-{index:03d}",
                    "parent_task_id": parents[index],
                    "signature_class_id": f"class-{index % 11:02d}",
                }
                for index in range(401)
            ],
            seed=25101,
        )
        shuffled_payloads = repeated_payloads[
            torch.tensor(permutation, device=backend.device)
        ]
        shuffled_A, shuffled_B = compile_differentiable_field(
            keys=repeated_keys,
            payloads=shuffled_payloads,
            rho=rho_401,
        )
        correct_slots = read_compiled_field(
            query=query, A=A_401, B=B_401, nonempty=True
        )
        shuffled_slots = read_compiled_field(
            query=query, A=shuffled_A, B=shuffled_B, nonempty=True
        )

    reversible = ReversibleRCMFField(device="cpu")
    for index in range(4):
        reversible.add_memory_fast(
            RCMFFieldRecord(
                memory_id=f"smoke-{index}",
                parent_id=f"parent-{index}",
                parent_task_id=f"task-{index}",
                key=keys[index].detach().cpu(),
                payload=base_payloads[index].detach().cpu(),
                rho=1.0,
            )
        )
    rebuild_A, rebuild_B = reversible.audit_rebuild()
    reversible_error = max(
        float((reversible.A - rebuild_A).abs().max()),
        float((reversible.B - rebuild_B).abs().max()),
    )

    smoke_root = ensure_dir(args.run_root / "technical_smoke")
    deployment = smoke_root / "fixture_field.pt"
    torch.save(
        {
            "format": "exp037a_technical_smoke_field_14b_v1",
            "A": A_401.detach().cpu(),
            "B": B_401.detach().cpu(),
            "shuffled_A": shuffled_A.detach().cpu(),
            "shuffled_B": shuffled_B.detach().cpu(),
            "memory_count": 401,
        },
        deployment,
    )
    provenance = smoke_root / "field_provenance.json"
    atomic_write_json(
        provenance,
        {
            "format": "exp037a_technical_smoke_field_provenance_14b_v1",
            "memory_count": 401,
            "scientific_result": False,
        },
    )
    runtime = _SmokeFieldRuntime(
        reader=reader.eval(),
        correct=correct_slots.detach(),
        shuffled=shuffled_slots.detach(),
        state_views=state_for_field.detach(),
        query=query.detach(),
    )
    settings = copy.deepcopy(cfg.raw["stage_c_9a"])
    settings["appworld"]["prompt_profile"] = "full_demo_first_only"
    heldout_ids = sorted(
        map(
            str,
            _json(Path(str(pipeline["roots"]["approved_downstream_split"])))[
                "validation_task_ids"
            ],
        )
    )
    task_id = heldout_ids[0]
    task_paths = {
        "root": smoke_root / "appworld",
        "static_assets": smoke_root
        / "appworld/raw_audit/static_prompt_assets.json",
        "deployment": deployment,
        "instant_add": provenance,
    }
    condition_manifest = {
        "format": "exp037a_technical_smoke_conditions_14b_v1",
        "task_id": task_id,
        "conditions": ["D0", "D1", "D2"],
        "manifest_sha256": content_sha256(
            {"task_id": task_id, "conditions": ["D0", "D1", "D2"]}
        ),
    }
    trajectories = {}
    for condition in ("D0", "D1", "D2"):
        row, reused = _run_task(
            task_id=task_id,
            condition=condition,
            settings=settings,
            backend=backend,
            runtime=None if condition == "D0" else runtime,
            paths=task_paths,
            manifest=condition_manifest,
            config_sha256=sha256_file(_arm_config(args.run_root, "1d")),
            attempt_id=f"exp037a-smoke-{condition}",
            smoke=True,
            memory_count=0 if condition == "D0" else 401,
            field_artifact_path=deployment,
            field_provenance_path=provenance,
            max_steps_override=1,
            experiment_prefix="exp037a",
            field_control_condition=condition,
            collect_resource_metrics=True,
        )
        trajectories[condition] = {
            "status": row["status"],
            "success": row["success"],
            "step_count": row["step_count"],
            "infrastructure_exception_count": row.get(
                "infrastructure_exception_count", 0
            ),
            "reused": reused,
            "task_result_sha256": content_sha256(row),
            "wall_seconds": float(row["wall_seconds"]),
            "prompt_tokens": int(row["usage"].get("prompt_tokens", 0)),
            "generated_tokens": int(
                row["usage"].get("completion_tokens", 0)
            ),
            "peak_gpu_memory_bytes": int(
                row.get("resource_metrics", {}).get(
                    "peak_allocated_bytes", 0
                )
            ),
        }

    scheduler = _scheduler_smoke(
        args.source_commit, float(pipeline["proposed_hard_cap_hours"])
    )
    local_tests = _json(args.local_tests)
    lambda_tests = _json(args.lambda_tests)
    historical_integration = None
    if args.real_historical_integration_report is not None:
        historical_integration = _json(args.real_historical_integration_report)
    passed = all(
        (
            local_tests.get("passed") is True,
            lambda_tests.get("passed") is True,
            selector_result["optimizer_updates"] == 1,
            gradient_rows[-1]["writer_any_gradient"],
            gradient_rows[-1]["reader_any_gradient"],
            reversible_error <= 1.0e-5,
            scheduler["passed"],
            all(row["status"] == "complete" for row in trajectories.values()),
            historical_integration is None
            or bool(historical_integration.get("passed")),
        )
    )
    elapsed = time.perf_counter() - started
    result = {
        "format": "exp037a_bounded_technical_smoke_14b_v1",
        "scientific_result": False,
        "source_commit": args.source_commit,
        "global_seed": 25101,
        "all_tests_passed": bool(
            local_tests.get("passed") and lambda_tests.get("passed")
        ),
        "local_tests": local_tests,
        "lambda_tests": lambda_tests,
        "state": {
            "state_example_id": selected_state_id,
            "task_id": example_task_id(example),
            "arms": state_rows,
        },
        "transitions": transition_rows,
        "selector": {
            "candidate": candidate["name"],
            "folds_exercised": 1,
            "optimizer_updates": selector_result["optimizer_updates"],
            "elapsed_seconds": selector_seconds,
            "final_member_seed": pipeline["final_selector_member_seeds"][0],
            "ensemble_member_construction_count": 3,
            "query_shape": list(query.shape),
            "key_shape": list(keys.shape),
        },
        "paired_causal_generation": paired_generation,
        "policy_teacher_target_score": teacher_score,
        "policy_teacher_seconds": teacher_seconds,
        "writer_reader_backward": gradient_rows,
        "integration_levels": {
            "low_level_writer_reader_smoke": {
                "present": True,
                "passed": gradient_rows[-1]["writer_any_gradient"]
                and gradient_rows[-1]["reader_any_gradient"],
            },
            "real_historical_joint_prepare_smoke": {
                "present": historical_integration is not None,
                "report": historical_integration,
                "passed": historical_integration is None
                or bool(historical_integration.get("passed")),
            },
        },
        "field_fixture": {
            "memory_count": 401,
            "A_shape": list(A_401.shape),
            "B_shape": list(B_401.shape),
            "correct_slot_shape": list(correct_slots.shape),
            "shuffle_fixed_points": sum(
                index == value for index, value in enumerate(permutation)
            ),
            "reversible_rebuild_max_abs": reversible_error,
            "artifact": file_identity(deployment),
        },
        "heldout_train_appworld": {
            "task_id": task_id,
            "max_steps": 1,
            "conditions": trajectories,
        },
        "scheduler": scheduler,
        "elapsed_seconds": elapsed,
        "peak_gpu_memory_bytes": (
            int(torch.cuda.max_memory_allocated(backend.device))
            if backend.device.type == "cuda"
            else 0
        ),
        "passed": passed,
    }
    atomic_write_json(args.output, result)
    if not passed:
        raise RuntimeError("EXP-037A bounded technical smoke failed")
    return result


def main() -> None:
    args = parse_args()
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
