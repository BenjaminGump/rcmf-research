from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor
from torch.nn import functional as F

from rcmf.benchmarks.webshop.adapter import (
    PROMPT_PROFILE,
    WebShopPortableAdapterV2,
    action_to_tool_call,
    load_task_catalog,
    load_trajectory_corpus,
)
from rcmf.benchmarks.webshop.generation import HFQwenToolGenerator
from rcmf.benchmarks.webshop.method import (
    GLOBAL_SEED,
    build_trainable_components,
    compile_deployment_fields,
    compile_query_slots,
    deterministic_digest,
    frozen_selector,
    parent_normalized_rho,
    selector_candidate_indices,
    task_partition,
    train_selector_member,
)
from rcmf.benchmarks.webshop.representations import (
    STATE_VIEW_NAMES,
    TRANSITION_VIEW_NAMES,
    encode_structured_text,
    state_representation_text,
    transition_representation_text,
)
from rcmf.pipeline.manifests import content_sha256
from rcmf.pipeline.portable_v2.schemas import DecisionStateRecord, TransitionRecord
from rcmf.training.rcmf_joint_full_bank_9a import FieldReaderHooks
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    to_jsonable,
)


def _object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _rows(path: Path) -> list[Mapping[str, Any]]:
    return [dict(row) for row in read_jsonl(path.resolve(strict=True))]


def _atomic_torch_save(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(value, temporary)
    os.replace(temporary, path)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _validate_source(args: argparse.Namespace) -> Mapping[str, Any]:
    source = _object(args.source_manifest)
    identity = source.get("source_identity")
    if not isinstance(identity, Mapping):
        raise TypeError("method source manifest lacks source_identity")
    checks = {
        "source_commit": identity.get("source_commit") == _git_head(),
        "method_config": identity.get("method_config_sha256") == sha256_file(args.method_config),
        "construction_corpus": identity.get("construction_corpus_sha256")
        == sha256_file(args.trajectory_corpus),
        "construction_manifest": identity.get("construction_manifest_sha256")
        == sha256_file(args.corpus_manifest),
        "task_catalog": identity.get("task_catalog_sha256") == sha256_file(args.task_catalog),
        "runtime_identity": identity.get("runtime_identity_sha256")
        == sha256_file(args.runtime_identity),
        "model_snapshot_commit": identity.get("model_snapshot_commit")
        == args.model_snapshot.resolve(strict=True).name,
        "standard200_outcome_inspected": identity.get("standard200_outcome_inspected") is False,
    }
    if not all(checks.values()):
        raise RuntimeError(f"WebShop method source identity failed: {checks}")
    return identity


def _adapter(
    args: argparse.Namespace, source_identity: Mapping[str, Any]
) -> WebShopPortableAdapterV2:
    return WebShopPortableAdapterV2(
        task_records=load_task_catalog(args.task_catalog),
        trajectory_records=load_trajectory_corpus(args.trajectory_corpus),
        trajectory_source_identity=_object(args.corpus_manifest)["source_identity"],
        runtime_identity=_object(args.runtime_identity),
        token_counter=None,
        runtime_factory=None,
    )


def _paths(root: Path) -> Mapping[str, Path]:
    return {
        "transitions": root / "ledger/transitions.jsonl",
        "states": root / "ledger/states.jsonl",
        "ledger_manifest": root / "ledger/manifest.json",
        "representation_items": root / "representations/items",
        "representations": root / "representations/cache.pt",
        "representation_manifest": root / "representations/manifest.json",
        "selector": root / "selector/ensemble.pt",
        "selector_manifest": root / "selector/manifest.json",
        "checkpoints": root / "training/checkpoints",
        "latest": root / "training/latest.json",
        "training_summary": root / "training/summary.json",
        "method_package": root / "freeze/method_package.pt",
        "freeze_manifest": root / "freeze/manifest.json",
    }


def _ledger(
    *,
    adapter: WebShopPortableAdapterV2,
    args: argparse.Namespace,
    source_identity: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> Mapping[str, Any]:
    task_by_id = {task.task_id: task for task in adapter.list_tasks()["train"]}
    transitions: list[Mapping[str, Any]] = []
    states: list[Mapping[str, Any]] = []
    for trajectory in adapter.successful_trajectories("train"):
        task = task_by_id[trajectory.task_id]
        task_transitions = list(adapter.transition_records(task, trajectory))
        task_states = list(adapter.decision_states(task, trajectory, PROMPT_PROFILE))
        if len(task_transitions) != len(task_states):
            raise RuntimeError("WebShop transition/state counts differ")
        transitions.extend(row.as_dict() for row in task_transitions)
        states.extend(row.as_dict() for row in task_states)
    transition_text = "".join(
        json.dumps(to_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in transitions
    )
    state_text = "".join(
        json.dumps(to_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n" for row in states
    )
    atomic_write_text(paths["transitions"], transition_text)
    atomic_write_text(paths["states"], state_text)
    transition_tasks = [str(row["task_id"]) for row in transitions]
    partition = task_partition(transition_tasks)
    manifest = {
        "format": "rcmf_agentbench_fc_webshop_transition_ledger_v1",
        "source_identity": dict(source_identity),
        "trajectory_corpus_sha256": sha256_file(args.trajectory_corpus),
        "corpus_manifest_sha256": sha256_file(args.corpus_manifest),
        "transition_count": len(transitions),
        "state_count": len(states),
        "trajectory_count": len({row["parent_trajectory_id"] for row in transitions}),
        "task_count": len(set(transition_tasks)),
        "action_counts": dict(
            sorted(Counter(row["metadata"]["action_type"] for row in transitions).items())
        ),
        "method_partition_task_counts": dict(sorted(Counter(partition.values()).items())),
        "method_partition": partition,
        "transitions_sha256": sha256_file(paths["transitions"]),
        "states_sha256": sha256_file(paths["states"]),
        "complete_raw_transition_ledger_retained": True,
        "derived_tensors_are_not_authoritative": True,
        "standard200_outcome_inspected": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    atomic_write_json(paths["ledger_manifest"], manifest)
    return manifest


def _load_frozen_model(model_snapshot: Path) -> HFQwenToolGenerator:
    generator = HFQwenToolGenerator(model_snapshot, dtype="bfloat16")
    for parameter in generator.model.parameters():
        parameter.requires_grad_(False)
    generator.model.eval()
    return generator


def _representations(
    *, args: argparse.Namespace, source_identity: Mapping[str, Any], paths: Mapping[str, Path]
) -> Mapping[str, Any]:
    ledger = _object(paths["ledger_manifest"])
    if ledger["transitions_sha256"] != sha256_file(paths["transitions"]):
        raise RuntimeError("WebShop transition ledger hash differs")
    transitions = [TransitionRecord.from_dict(row) for row in _rows(paths["transitions"])]
    states = [DecisionStateRecord.from_dict(row) for row in _rows(paths["states"])]
    if len(transitions) != len(states):
        raise ValueError("WebShop representation rows are not aligned")
    generator = _load_frozen_model(args.model_snapshot)
    model = generator.model
    tokenizer = generator.tokenizer
    device = next(model.parameters()).device
    item_root = paths["representation_items"]
    item_root.mkdir(parents=True, exist_ok=True)
    state_values: list[Tensor] = []
    transition_values: list[Tensor] = []
    item_hashes = []
    started = time.perf_counter()
    for ordinal, (state, transition) in enumerate(zip(states, transitions, strict=True)):
        if state.task_id != transition.task_id or state.state_id.rsplit(":state:", 1)[-1] != str(
            transition.step_index
        ):
            raise RuntimeError("WebShop representation state/transition lineage differs")
        path = item_root / f"row_{ordinal:06d}.pt"
        identity = {
            "ordinal": ordinal,
            "state_id": state.state_id,
            "transition_id": transition.transition_id,
            "source_identity_sha256": content_sha256(source_identity),
            "representation_format": args.method_config_data["representations"]["format"],
        }
        if path.exists():
            item = torch.load(path, map_location="cpu", weights_only=False)
            if item.get("identity") != identity:
                raise RuntimeError(f"WebShop representation item identity differs: {path}")
        else:
            state_text, state_spans, state_metadata = state_representation_text(state)
            transition_text, transition_spans, transition_metadata = transition_representation_text(
                transition
            )
            state_tensor, state_span_rows = encode_structured_text(
                model=model,
                tokenizer=tokenizer,
                text=state_text,
                spans=state_spans,
                view_names=STATE_VIEW_NAMES,
                device=device,
            )
            transition_tensor, transition_span_rows = encode_structured_text(
                model=model,
                tokenizer=tokenizer,
                text=transition_text,
                spans=transition_spans,
                view_names=TRANSITION_VIEW_NAMES,
                device=device,
            )
            item = {
                "format": "rcmf_webshop_representation_item_v1",
                "identity": identity,
                "state": state_tensor,
                "transition": transition_tensor,
                "state_span_rows": state_span_rows,
                "transition_span_rows": transition_span_rows,
                "state_metadata": dict(state_metadata),
                "transition_metadata": dict(transition_metadata),
            }
            _atomic_torch_save(item, path)
        state_values.append(item["state"].to(torch.float32))
        transition_values.append(item["transition"].to(torch.float32))
        item_hashes.append(sha256_file(path))
    cache = {
        "format": "rcmf_agentbench_fc_webshop_representation_cache_v1",
        "source_identity": dict(source_identity),
        "ordered_state_ids": [row.state_id for row in states],
        "ordered_transition_ids": [row.transition_id for row in transitions],
        "task_ids": [row.task_id for row in transitions],
        "action_types": [str(row.metadata["action_type"]) for row in transitions],
        "state_views": torch.stack(state_values),
        "transition_views": torch.stack(transition_values),
        "writer_views": torch.stack(transition_values)[:, :8],
        "item_sha256s": item_hashes,
    }
    _atomic_torch_save(cache, paths["representations"])
    manifest = {
        "format": "rcmf_agentbench_fc_webshop_representation_manifest_v1",
        "source_identity_sha256": content_sha256(source_identity),
        "ledger_manifest_sha256": sha256_file(paths["ledger_manifest"]),
        "cache_sha256": sha256_file(paths["representations"]),
        "row_count": len(states),
        "state_shape": list(cache["state_views"].shape),
        "transition_shape": list(cache["transition_views"].shape),
        "writer_shape": list(cache["writer_views"].shape),
        "item_sha256s_sha256": content_sha256(item_hashes),
        "model_snapshot_commit": args.model_snapshot.name,
        "qwen_frozen": True,
        "target_action_accessed_by_state_encoder": False,
        "future_observation_accessed_by_state_encoder": False,
        "elapsed_seconds": time.perf_counter() - started,
        "standard200_outcome_inspected": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    atomic_write_json(paths["representation_manifest"], manifest)
    return manifest


def _selector(
    *, args: argparse.Namespace, source_identity: Mapping[str, Any], paths: Mapping[str, Path]
) -> Mapping[str, Any]:
    cache = torch.load(paths["representations"], map_location="cpu", weights_only=False)
    ledger = _object(paths["ledger_manifest"])
    partition = ledger["method_partition"]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    state_views = cache["state_views"].to(device=device, dtype=torch.float32)
    transition_views = cache["transition_views"].to(device=device, dtype=torch.float32)
    task_ids = [str(value) for value in cache["task_ids"]]
    transition_ids = [str(value) for value in cache["ordered_transition_ids"]]
    candidates = selector_candidate_indices(
        transition_ids=transition_ids,
        task_ids=task_ids,
        action_types=[str(value) for value in cache["action_types"]],
    )
    train_positions = [
        index for index, task_id in enumerate(task_ids) if partition[task_id] == "method_train"
    ]
    validation_positions = [
        index for index, task_id in enumerate(task_ids) if partition[task_id] == "method_validation"
    ]
    settings = args.method_config_data["selector"]
    started = time.perf_counter()
    members = []
    for seed in settings["member_seeds"]:
        members.append(
            train_selector_member(
                state_views=state_views,
                transition_views=transition_views,
                candidates=candidates,
                train_positions=train_positions,
                validation_positions=validation_positions,
                seed=int(seed),
                epochs=int(settings["epochs"]),
                batch_size=int(settings["batch_size"]),
                learning_rate=float(settings["learning_rate"]),
                weight_decay=float(settings["weight_decay"]),
                projection_dim=int(settings["projection_dim"]),
                interaction_rank=int(settings["interaction_rank"]),
            )
        )
    payload = {
        "format": "rcmf_agentbench_fc_webshop_selector_ensemble_v1",
        "source_identity": dict(source_identity),
        "members": members,
        "train_positions": train_positions,
        "validation_positions": validation_positions,
        "candidates": candidates,
    }
    selector = frozen_selector(payload, device=device)
    probe_positions = validation_positions[: min(8, len(validation_positions))]
    with torch.no_grad():
        direct = selector.direct_scores(
            state_views[probe_positions], transition_views[probe_positions]
        )
        decomposed = selector.decomposed_scores(
            state_views[probe_positions], transition_views[probe_positions]
        )
    maximum_error = float((direct - decomposed).abs().max().cpu())
    if maximum_error > 1.0e-4:
        raise RuntimeError("WebShop selector decomposition is not exact within tolerance")
    _atomic_torch_save(payload, paths["selector"])
    manifest = {
        "format": "rcmf_agentbench_fc_webshop_selector_manifest_v1",
        "source_identity_sha256": content_sha256(source_identity),
        "representation_cache_sha256": sha256_file(paths["representations"]),
        "selector_sha256": sha256_file(paths["selector"]),
        "member_count": len(members),
        "member_seeds": [int(member["seed"]) for member in members],
        "train_state_count": len(train_positions),
        "validation_state_count": len(validation_positions),
        "key_dim": selector.key_dim,
        "decomposition_maximum_absolute_error": maximum_error,
        "train_metrics": [member["train_metrics"] for member in members],
        "validation_metrics": [member["validation_metrics"] for member in members],
        "elapsed_seconds": time.perf_counter() - started,
        "frozen_after_training": True,
        "runtime_per_memory_scoring_allowed": False,
        "standard200_outcome_inspected": False,
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    atomic_write_json(paths["selector_manifest"], manifest)
    return manifest


def _teacher_row(
    *, adapter: WebShopPortableAdapterV2, tokenizer: Any, state: DecisionStateRecord
) -> Mapping[str, Any]:
    messages = list(adapter.render_messages(state, PROMPT_PROFILE))
    action = str(state.target_action_reference["action"])
    call_id = f"teacher-{hashlib_sha(state.state_id)[:16]}"
    target = {
        "role": "assistant",
        "content": "",
        "tool_calls": [action_to_tool_call(action, call_id=call_id)],
    }
    prompt_ids = tokenizer.apply_chat_template(
        messages,
        tools=list(adapter.tools),
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=True,
    )
    full_ids = tokenizer.apply_chat_template(
        [*messages, target],
        tools=list(adapter.tools),
        add_generation_prompt=False,
        enable_thinking=False,
        tokenize=True,
    )
    prompt_ids = [int(value) for value in prompt_ids]
    full_ids = [int(value) for value in full_ids]
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise RuntimeError("WebShop teacher target does not extend the generation prompt")
    labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids) :]
    if all(value == -100 for value in labels):
        raise RuntimeError("WebShop teacher target has no action tokens")
    return {
        "state_id": state.state_id,
        "input_ids": full_ids,
        "labels": labels,
        "target_token_count": len(full_ids) - len(prompt_ids),
        "target_action": action,
    }


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target_loss(
    model: Any, row: Mapping[str, Any], hooks: FieldReaderHooks
) -> tuple[Tensor, Tensor]:
    device = next(model.parameters()).device
    input_ids = torch.tensor([row["input_ids"]], dtype=torch.long, device=device)
    labels = torch.tensor([row["labels"]], dtype=torch.long, device=device)
    attention_mask = torch.ones_like(input_ids)
    base_model = getattr(model, "model", None)
    lm_head = getattr(model, "lm_head", None)
    if base_model is None or lm_head is None:
        raise RuntimeError("WebShop training requires the Qwen base model and LM head")
    with (
        hooks,
        torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"),
    ):
        outputs = base_model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            use_cache=False,
            return_dict=True,
        )
        hidden = outputs.last_hidden_state
        shifted_labels = labels[:, 1:]
        mask = shifted_labels.ne(-100)
        target_hidden = hidden[:, :-1][mask]
        target_labels = shifted_labels[mask]
        logits = lm_head(target_hidden)
        loss = F.cross_entropy(logits.to(torch.float32), target_labels)
    return loss, logits


def _training_schedule(
    state_ids: Sequence[str],
    task_ids: Sequence[str],
    partition: Mapping[str, str],
    settings: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    eligible = [
        index for index, task_id in enumerate(task_ids) if partition[task_id] == "method_train"
    ]
    selected = sorted(
        eligible,
        key=lambda index: (deterministic_digest(GLOBAL_SEED, state_ids[index]), state_ids[index]),
    )[: int(settings["maximum_method_train_states"])]
    schedule = []
    for epoch in range(1, int(settings["epochs"]) + 1):
        ordered = sorted(
            selected,
            key=lambda index: (
                deterministic_digest(GLOBAL_SEED, "writer-reader", epoch, state_ids[index]),
                state_ids[index],
            ),
        )
        schedule.extend(
            {"epoch": epoch, "position": index, "state_id": state_ids[index]} for index in ordered
        )
    return schedule


def _train(
    *, args: argparse.Namespace, source_identity: Mapping[str, Any], paths: Mapping[str, Path]
) -> Mapping[str, Any]:
    if (
        os.environ.get("PYTHONHASHSEED") != "25101"
        or os.environ.get("CUBLAS_WORKSPACE_CONFIG") != ":4096:8"
    ):
        raise RuntimeError(
            "WebShop training requires process-start deterministic environment variables"
        )
    adapter = _adapter(args, source_identity)
    states = [DecisionStateRecord.from_dict(row) for row in _rows(paths["states"])]
    cache = torch.load(paths["representations"], map_location="cpu", weights_only=False)
    selector_payload = torch.load(paths["selector"], map_location="cpu", weights_only=False)
    partition = _object(paths["ledger_manifest"])["method_partition"]
    settings = args.method_config_data["writer_reader_training"]
    full_schedule = _training_schedule(
        cache["ordered_state_ids"], cache["task_ids"], partition, settings
    )
    maximum_units = int(args.maximum_units)
    schedule = full_schedule[:maximum_units] if maximum_units > 0 else full_schedule
    diagnostic = maximum_units > 0
    generator = _load_frozen_model(args.model_snapshot)
    model = generator.model
    model.config.use_cache = False
    device = next(model.parameters()).device
    selector = frozen_selector(selector_payload, device=device)
    state_views = cache["state_views"].to(device=device, dtype=torch.float32)
    transition_views = cache["transition_views"].to(device=device, dtype=torch.float32)
    memory_views = cache["writer_views"].to(device=device, dtype=torch.float32)
    with torch.no_grad():
        keys = selector.key(transition_views)
        queries = selector.query(state_views)
    rho = parent_normalized_rho(cache["task_ids"], device=device)
    writer, reader = build_trainable_components(device)
    optimizer = torch.optim.AdamW(
        [
            {"params": writer.parameters(), "lr": float(settings["writer_learning_rate"])},
            {"params": reader.parameters(), "lr": float(settings["reader_learning_rate"])},
        ],
        weight_decay=float(settings["weight_decay"]),
    )
    completed = 0
    history: list[Mapping[str, Any]] = []
    source_hashes = {
        "method_config": sha256_file(args.method_config),
        "ledger": sha256_file(paths["ledger_manifest"]),
        "representations": sha256_file(paths["representations"]),
        "selector": sha256_file(paths["selector"]),
    }
    if paths["latest"].exists() and not diagnostic:
        latest = _object(paths["latest"])
        checkpoint_path = Path(str(latest["checkpoint"])).resolve(strict=True)
        if latest["checkpoint_sha256"] != sha256_file(checkpoint_path):
            raise RuntimeError("WebShop latest training checkpoint hash differs")
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
        if checkpoint["source_hashes"] != source_hashes or checkpoint[
            "schedule_sha256"
        ] != content_sha256(full_schedule):
            raise RuntimeError("WebShop training resume identity differs")
        writer.load_state_dict(checkpoint["writer"])
        reader.load_state_dict(checkpoint["reader"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        completed = int(checkpoint["completed_units"])
        history = list(checkpoint["history"])
    teacher_rows: dict[int, Mapping[str, Any]] = {}
    started = time.perf_counter()
    paths["checkpoints"].mkdir(parents=True, exist_ok=True)
    for cursor in range(completed, len(schedule)):
        unit = schedule[cursor]
        position = int(unit["position"])
        if position not in teacher_rows:
            teacher_rows[position] = _teacher_row(
                adapter=adapter, tokenizer=generator.tokenizer, state=states[position]
            )
        optimizer.zero_grad(set_to_none=True)
        unit_started = time.perf_counter()
        slots, field = compile_query_slots(
            writer=writer,
            memory_views=memory_views,
            keys=keys,
            rho=rho,
            query=queries[position],
            memory_task_ids=cache["task_ids"],
            excluded_task_id=str(cache["task_ids"][position]),
        )
        hooks = FieldReaderHooks(model=model, reader=reader, slots=slots)
        action_ce, _logits = _target_loss(model, teacher_rows[position], hooks)
        residual = hooks.residual_penalty()
        payload_norm = field["payloads"].to(torch.float32).square().mean()
        loss = (
            float(settings["action_cross_entropy_weight"]) * action_ce
            + float(settings["reader_residual_norm_weight"]) * residual
            + float(settings["writer_payload_norm_weight"]) * payload_norm
        )
        loss.backward()
        parameters = list(writer.parameters()) + list(reader.parameters())
        torch.nn.utils.clip_grad_norm_(parameters, float(settings["max_grad_norm"]))
        gradients_finite = all(
            parameter.grad is None or bool(torch.isfinite(parameter.grad).all())
            for parameter in parameters
        )
        writer_gradient = any(
            parameter.grad is not None and bool(parameter.grad.detach().abs().gt(0).any())
            for parameter in writer.parameters()
        )
        reader_gradient = any(
            parameter.grad is not None and bool(parameter.grad.detach().abs().gt(0).any())
            for parameter in reader.parameters()
        )
        optimizer.step()
        if not math.isfinite(float(loss.detach().cpu())) or not gradients_finite:
            raise RuntimeError("WebShop writer/reader training produced non-finite values")
        history.append(
            {
                "unit": cursor,
                "epoch": int(unit["epoch"]),
                "state_id": unit["state_id"],
                "task_id": cache["task_ids"][position],
                "action_ce": float(action_ce.detach().cpu()),
                "loss": float(loss.detach().cpu()),
                "residual_penalty": float(residual.detach().cpu()),
                "payload_square_mean": float(payload_norm.detach().cpu()),
                "writer_gradient_nonzero": writer_gradient,
                "reader_gradient_nonzero": reader_gradient,
                "seconds": time.perf_counter() - unit_started,
            }
        )
        completed = cursor + 1
        checkpoint_every = int(settings["checkpoint_every_units"])
        if completed % checkpoint_every == 0 or completed == len(schedule):
            checkpoint = {
                "format": "rcmf_agentbench_fc_webshop_training_checkpoint_v1",
                "source_identity": dict(source_identity),
                "source_hashes": source_hashes,
                "schedule_sha256": content_sha256(full_schedule if not diagnostic else schedule),
                "completed_units": completed,
                "writer": {key: value.detach().cpu() for key, value in writer.state_dict().items()},
                "reader": {key: value.detach().cpu() for key, value in reader.state_dict().items()},
                "optimizer": optimizer.state_dict(),
                "history": history,
                "diagnostic": diagnostic,
            }
            checkpoint_path = paths["checkpoints"] / (
                "diagnostic.pt" if diagnostic else "progress.pt"
            )
            _atomic_torch_save(checkpoint, checkpoint_path)
            if not diagnostic:
                atomic_write_json(
                    paths["latest"],
                    {
                        "checkpoint": str(checkpoint_path),
                        "checkpoint_sha256": sha256_file(checkpoint_path),
                        "completed_units": completed,
                    },
                )
    summary = {
        "format": "rcmf_agentbench_fc_webshop_training_summary_v1",
        "source_identity_sha256": content_sha256(source_identity),
        "diagnostic": diagnostic,
        "completed_units": completed,
        "full_schedule_units": len(full_schedule),
        "epoch_count": int(settings["epochs"]),
        "training_state_count": len(full_schedule) // int(settings["epochs"]),
        "elapsed_seconds": time.perf_counter() - started,
        "mean_unit_seconds": (time.perf_counter() - started) / max(1, completed),
        "all_losses_finite": all(math.isfinite(float(row["loss"])) for row in history),
        "writer_received_gradients": any(bool(row["writer_gradient_nonzero"]) for row in history),
        "reader_received_gradients": all(bool(row["reader_gradient_nonzero"]) for row in history),
        "qwen_frozen": all(
            not parameter.requires_grad and parameter.grad is None
            for parameter in model.parameters()
        ),
        "same_task_memory_excluded": True,
        "raw_memory_prompt_used": False,
        "standard200_outcome_inspected": False,
    }
    summary["passed"] = bool(
        summary["all_losses_finite"]
        and summary["writer_received_gradients"]
        and summary["reader_received_gradients"]
        and summary["qwen_frozen"]
        and completed == len(schedule)
    )
    target = (
        paths["checkpoints"] / "diagnostic_summary.json"
        if diagnostic
        else paths["training_summary"]
    )
    atomic_write_json(target, summary)
    return summary


def _freeze(
    *, args: argparse.Namespace, source_identity: Mapping[str, Any], paths: Mapping[str, Path]
) -> Mapping[str, Any]:
    summary = _object(paths["training_summary"])
    if not bool(summary["passed"]) or bool(summary["diagnostic"]):
        raise RuntimeError("WebShop formal training is not complete")
    latest = _object(paths["latest"])
    checkpoint_path = Path(str(latest["checkpoint"])).resolve(strict=True)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if int(checkpoint["completed_units"]) != int(summary["full_schedule_units"]):
        raise RuntimeError("WebShop terminal checkpoint is incomplete")
    cache = torch.load(paths["representations"], map_location="cpu", weights_only=False)
    selector_payload = torch.load(paths["selector"], map_location="cpu", weights_only=False)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    selector = frozen_selector(selector_payload, device=device)
    writer, reader = build_trainable_components(device)
    writer.load_state_dict(checkpoint["writer"])
    reader.load_state_dict(checkpoint["reader"])
    writer.eval()
    reader.eval()
    for module in (writer, reader):
        for parameter in module.parameters():
            parameter.requires_grad_(False)
    transition_views = cache["transition_views"].to(device=device, dtype=torch.float32)
    memory_views = cache["writer_views"].to(device=device, dtype=torch.float32)
    with torch.no_grad():
        keys = selector.key(transition_views)
    rho = parent_normalized_rho(cache["task_ids"], device=device)
    fields = compile_deployment_fields(
        writer=writer,
        memory_views=memory_views,
        keys=keys,
        rho=rho,
        transition_ids=cache["ordered_transition_ids"],
        task_ids=cache["task_ids"],
    )
    package = {
        "format": "rcmf_agentbench_fc_webshop_frozen_method_v1",
        "source_identity": dict(source_identity),
        "writer": {key: value.detach().cpu() for key, value in writer.state_dict().items()},
        "reader": {key: value.detach().cpu() for key, value in reader.state_dict().items()},
        "selector": selector_payload,
        "correct_A": fields["correct_A"],
        "correct_B": fields["correct_B"],
        "shuffled_A": fields["shuffled_A"],
        "shuffled_B": fields["shuffled_B"],
        "key_payload_permutation": fields["permutation"],
        "memory_count": len(cache["ordered_transition_ids"]),
        "memory_ids": list(cache["ordered_transition_ids"]),
        "memory_task_ids": list(cache["task_ids"]),
        "rho": rho.detach().cpu(),
    }
    _atomic_torch_save(package, paths["method_package"])
    permutation = list(fields["permutation"])
    manifest = {
        "format": "rcmf_agentbench_fc_webshop_frozen_method_manifest_v1",
        "source_identity": dict(source_identity),
        "method_package": str(paths["method_package"]),
        "method_package_sha256": sha256_file(paths["method_package"]),
        "training_checkpoint_sha256": sha256_file(checkpoint_path),
        "selector_sha256": sha256_file(paths["selector"]),
        "representation_cache_sha256": sha256_file(paths["representations"]),
        "transition_ledger_sha256": sha256_file(paths["transitions"]),
        "memory_count": package["memory_count"],
        "field_shapes": {
            "A": list(package["correct_A"].shape),
            "B": list(package["correct_B"].shape),
        },
        "control_identities": {
            "B0": "zero_field_exact_bare_qwen",
            "RCMF-C": "correct_key_payload_association",
            "RCMF-S": "matched_deterministic_key_payload_derangement",
        },
        "shuffle_fixed_points": sum(index == target for index, target in enumerate(permutation)),
        "shuffle_key_multiset_preserved": True,
        "shuffle_payload_multiset_preserved": sorted(permutation) == list(range(len(permutation))),
        "shuffle_weights_preserved": True,
        "qwen_frozen": True,
        "selector_frozen": True,
        "writer_frozen": True,
        "reader_frozen": True,
        "independent_feed_forward_memory_compilation": True,
        "reversible_add_remove_restore_supported": True,
        "fixed_shape_whole_bank_state": True,
        "runtime_per_memory_scan": False,
        "runtime_top_k": False,
        "raw_memory_in_deployment_prompt": False,
        "standard200_outcome_inspected": False,
        "status": "FROZEN_BEFORE_STANDARD200",
    }
    manifest["manifest_sha256"] = content_sha256(manifest)
    atomic_write_json(paths["freeze_manifest"], manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("ledger", "representations", "selector", "train", "freeze"),
        required=True,
    )
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--trajectory-corpus", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--runtime-identity", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--method-config", type=Path, required=True)
    parser.add_argument("--model-snapshot", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--maximum-units", type=int, default=0)
    args = parser.parse_args()
    args.method_config_data = _object(args.method_config)
    if args.method_config_data.get("format") != "rcmf_agentbench_fc_webshop_method_config_v1":
        raise ValueError("unexpected WebShop method config format")
    source_identity = _validate_source(args)
    paths = _paths(args.output_root.resolve())
    adapter = _adapter(args, source_identity) if args.phase == "ledger" else None
    if args.phase == "ledger":
        result = _ledger(adapter=adapter, args=args, source_identity=source_identity, paths=paths)
    elif args.phase == "representations":
        result = _representations(args=args, source_identity=source_identity, paths=paths)
    elif args.phase == "selector":
        result = _selector(args=args, source_identity=source_identity, paths=paths)
    elif args.phase == "train":
        result = _train(args=args, source_identity=source_identity, paths=paths)
    else:
        result = _freeze(args=args, source_identity=source_identity, paths=paths)
    print(json.dumps(to_jsonable(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
