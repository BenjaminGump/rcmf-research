from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn
import torch.nn.functional as F

from rcmf.config import load_config
from rcmf.training.appworld_structured_rescue_7hr import (
    FeatureSchema,
    GLOBAL_SEED,
    LABELS,
    MemoryUseGate,
    StructuredLatentComposer,
    build_feature_vector,
    stable_key,
)
from rcmf.training.deep_residual_amortization_7f import (
    K_TOKENS,
    LAYER_INDICES,
    differentiable_layer_ratio_projection,
)
from rcmf.training.deep_residual_carrier_7e import DeepResidualHooks
from rcmf.training.datasets import (
    _appworld_messages_from_example,
    load_decision_examples,
)
from rcmf.training.oracle_convergence_5fa import (
    atomic_torch_save,
    update_count_summary,
)
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.procedural_causal_audit_7b import build_live_appworld_messages
from rcmf.training.procedural_supervision_6f import _stage_compatibility
from rcmf.training.state_conditioned_program_direct_7dg import (
    seed_everything,
)
from rcmf.training.state_conditioned_program_policy_distill_7dg3 import (
    sparse_policy_kl,
)
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.training.transition_memory_6a import messages_with_transition_memory
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.prepare_appworld_structured_rescue_7hr import _memory_flags
from scripts.run_deep_residual_carrier_7e import (
    _bare_target_forward,
    _capture_states,
    _selected_indices,
)
from scripts.run_deep_residual_compiler_7f import _build_decoder, _build_model
from scripts.run_direct_injection_channel_7dh import _build_backend_from_generation
from scripts.run_memory_specific_deep_amortization_7g import _target_ids
from scripts.run_procedural_causal_audit_7b import _examples_by_state
from scripts.run_state_conditioned_program_direct_7dg import _load_representations
from scripts.run_state_conditioned_program_policy_distill_7dg3 import _policy_loss
from scripts.run_stage_c_oracle_capacity_5e import _collate


TEACHER_FORMAT = "appworld_structured_policy_teacher_cache_7hr_v1"
CHECKPOINT_FORMAT = "appworld_structured_deep_compiler_checkpoint_7hr_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_appworld_structured_rescue_7hr.yaml"),
    )
    parser.add_argument(
        "--replay-config",
        type=Path,
        default=Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("teacher", "train"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp028a_compiler")
    return parser.parse_args()


def _paths(settings: Mapping[str, Any], artifact_dir: Path) -> dict[str, Path]:
    parent_b = Path(str(settings["parent_exp025b"]))
    parent_c = Path(str(settings["parent_exp025c"]))
    parent_g = Path(str(settings["parent_exp027b"]))
    corpus = Path(str(settings["reconciled_corpus_dir"]))
    root = artifact_dir / "structured_compiler"
    return {
        "root": root,
        "outcomes": artifact_dir / "paired_causal/paired_outcomes.json",
        "condition_outputs": artifact_dir / "paired_causal/condition_outputs",
        "gate": artifact_dir / "gate/memory_use_gate.pt",
        "gate_report": artifact_dir / "gate/gate_report.json",
        "features": artifact_dir / "preflight/structured_feature_rows.jsonl",
        "selections": artifact_dir / "preflight/frozen_train_selections.jsonl",
        "feature_schema": artifact_dir / "preflight/structured_feature_schema.json",
        "decisions": corpus / "decision_examples.jsonl",
        "transitions": parent_b
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl",
        "signatures": parent_b
        / "clean_procedural_audit/clean_transition_signature_manifest.jsonl",
        "classes": parent_b / "clean_procedural_audit/clean_signature_equivalence_manifest.json",
        "query_signatures": parent_c / "clean_query_signature_manifest.jsonl",
        "intent_predictions": parent_c / "clean_intent_probe/calibrated_predictions.jsonl",
        "state_cache": parent_c / "representation_cache/multiview/state_multiview.pt",
        "transition_cache": parent_c
        / "representation_cache/multiview/transition_multiview.pt",
        "parent_training": parent_g / "compiler/pairmlp/training_summary.json",
        "teacher_cache": root / "policy_teacher_cache.pt",
        "teacher_report": root / "policy_teacher_report.json",
        "mismatches": root / "mismatch_manifest.json",
        "latest_checkpoint": root / "latest_checkpoint.json",
        "training_summary": root / "training_summary.json",
    }


def _condition_path(root: Path, key: str) -> Path:
    return root / f"{sha256_text(key)}.json"


def _topk_teacher(
    *,
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    response_text: str,
    top_k: int,
) -> tuple[dict[str, Any], list[int]]:
    prompt = backend.tokenize_messages(messages, add_generation_prompt=True)
    generated_ids = [
        int(value)
        for value in backend.tokenizer(
            response_text, add_special_tokens=False, truncation=False
        )["input_ids"]
    ]
    if not generated_ids:
        raise RuntimeError("Paired response retokenized to an empty sequence")
    full_ids = torch.cat(
        [
            prompt.input_ids,
            torch.tensor([generated_ids], dtype=torch.long, device=backend.device),
        ],
        dim=1,
    )
    labels = torch.full_like(full_ids, -100)
    labels[:, prompt.input_ids.shape[1] :] = torch.tensor(
        [generated_ids], dtype=torch.long, device=backend.device
    )
    with torch.no_grad():
        scored = backend.forward_train(
            input_ids=full_ids,
            attention_mask=torch.ones_like(full_ids),
            labels=labels,
        )
        logits = scored.logits.to(torch.float32)
    if int(logits.shape[0]) != len(generated_ids):
        raise ValueError("Retokenized paired response logits are misaligned")
    count = min(int(top_k), int(logits.shape[-1]))
    top_logits, top_ids = torch.topk(logits, k=count, dim=-1)
    normalizer = torch.logsumexp(logits.to(torch.float64), dim=-1)
    top_logprobs = top_logits.to(torch.float64) - normalizer[:, None]
    probabilities = top_logprobs.exp()
    positions = []
    for index in range(len(generated_ids)):
        positions.append(
            {
                "position": index,
                "teacher_token_id": generated_ids[index],
                "top_token_ids": [int(value) for value in top_ids[index].cpu().tolist()],
                "top_logprobs": [float(value) for value in top_logprobs[index].cpu().tolist()],
                "other_probability": max(
                    0.0, 1.0 - float(probabilities[index].sum().cpu())
                ),
            }
        )
    return (
        {
            "generated_token_ids": generated_ids,
            "generated_token_count": len(generated_ids),
            "positions": positions,
            "prompt_sha256": sha256_text(str(prompt.metadata["text"])),
            "prompt_tokens": int(prompt.attention_mask.sum().item()),
            "response_text_sha256": sha256_text(response_text),
            "retokenization_roundtrip_sha256": sha256_text(
                backend.tokenizer.decode(generated_ids, skip_special_tokens=False)
            ),
        },
        [int(value) for value in prompt.input_ids[0].cpu().tolist()],
    )


def _tokenized_row(
    *, pair_id: str, prompt_ids: Sequence[int], teacher: Mapping[str, Any], pad_token_id: int, last_user: Sequence[int]
) -> dict[str, Any]:
    generated = [int(value) for value in teacher["generated_token_ids"]]
    return {
        "pair_id": pair_id,
        "input_ids": [*map(int, prompt_ids), *generated],
        "labels": [-100] * len(prompt_ids) + generated,
        "pad_token_id": int(pad_token_id),
        "last_user_token_indices": [int(value) for value in last_user],
        "target_len": len(generated),
        "response_cache": dict(teacher),
    }


def _ground_truth_row(
    *, backend: Any, pair_id: str, messages: Sequence[Mapping[str, str]], target_text: str
) -> dict[str, Any]:
    prompt = backend.tokenize_messages(messages, add_generation_prompt=True)
    target_ids = [
        int(value)
        for value in backend.tokenizer(
            target_text, add_special_tokens=False, truncation=False
        )["input_ids"]
    ]
    return {
        "pair_id": pair_id,
        "input_ids": [
            *[int(value) for value in prompt.input_ids[0].cpu().tolist()],
            *target_ids,
        ],
        "labels": [-100] * int(prompt.input_ids.shape[1]) + target_ids,
        "pad_token_id": int(backend.tokenizer.pad_token_id),
        "last_user_token_indices": [
            int(value) for value in prompt.metadata["last_user_token_indices"]
        ],
        "target_len": len(target_ids),
        "response_cache": {"target_token_ids": target_ids},
    }


def _teacher_phase(
    *,
    cfg: Any,
    replay: Mapping[str, Any],
    settings: Mapping[str, Any],
    paths: Mapping[str, Path],
    attempt: AttemptLedger,
) -> dict[str, Any]:
    outcomes = _json(paths["outcomes"])
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    examples = _examples_by_state(load_decision_examples(paths["decisions"]))
    transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
    teacher_rows = {}
    policy_rows = {}
    ground_truth_rows = {}
    base_states = {}
    started = time.perf_counter()
    for ordinal, paired in enumerate(outcomes["rows"], start=1):
        state_id = str(paired["state_example_id"])
        bare = _json(_condition_path(paths["condition_outputs"], str(paired["bare_condition_key"])))
        raw = _json(_condition_path(paths["condition_outputs"], str(paired["raw_condition_key"])))
        observations = list(raw["live_worker"]["actual_replay_observations"])
        base_messages = build_live_appworld_messages(
            examples[state_id], observations, prompt_profile=cfg.benchmark.prompt_profile
        )
        transition_id = str(paired["selected_transition_id"])
        raw_messages = messages_with_transition_memory(
            base_messages, transitions[transition_id], cfg.benchmark.prompt_profile
        )
        bare_teacher, bare_prompt_ids = _topk_teacher(
            backend=backend,
            messages=base_messages,
            response_text=str(bare["raw_model_response"]),
            top_k=int(settings["compiler"]["top_k"]),
        )
        raw_teacher, _ = _topk_teacher(
            backend=backend,
            messages=raw_messages,
            response_text=str(raw["raw_model_response"]),
            top_k=int(settings["compiler"]["top_k"]),
        )
        prompt = backend.tokenize_messages(base_messages, add_generation_prompt=True)
        selected = "raw" if paired["label"] == "POSITIVE" else "bare"
        selected_teacher = raw_teacher if selected == "raw" else bare_teacher
        pair_id = f"7hr::{state_id}::{transition_id}"
        teacher_rows[state_id] = {
            "format": TEACHER_FORMAT,
            "pair_id": pair_id,
            "state_example_id": state_id,
            "transition_id": transition_id,
            "label": str(paired["label"]),
            "selected_target": selected,
            "bare": bare_teacher,
            "raw": raw_teacher,
            "paired_bare_condition_key": str(paired["bare_condition_key"]),
            "paired_raw_condition_key": str(paired["raw_condition_key"]),
            "student_prompt_contains_raw_transition": False,
        }
        policy_rows[state_id] = {
            "bare": _tokenized_row(
                pair_id=pair_id,
                prompt_ids=bare_prompt_ids,
                teacher=bare_teacher,
                pad_token_id=int(backend.tokenizer.pad_token_id),
                last_user=prompt.metadata["last_user_token_indices"],
            ),
            "raw": _tokenized_row(
                pair_id=pair_id,
                prompt_ids=bare_prompt_ids,
                teacher=raw_teacher,
                pad_token_id=int(backend.tokenizer.pad_token_id),
                last_user=prompt.metadata["last_user_token_indices"],
            ),
        }
        ground_truth_rows[state_id] = _ground_truth_row(
            backend=backend,
            pair_id=pair_id,
            messages=base_messages,
            target_text=str(examples[state_id].target_text),
        )
        batch = _collate([policy_rows[state_id][selected]], device=backend.device, k=K_TOKENS)
        base_states[state_id] = _capture_states(
            backend=backend, batch=batch, layer_indices=LAYER_INDICES
        )[0].cpu()
        attempt.progress(
            status="structured_policy_teacher_cache",
            completed_states=ordinal,
            total_states=len(outcomes["rows"]),
        )
        if ordinal % 20 == 0 or ordinal == len(outcomes["rows"]):
            print(f"structured policy teachers {ordinal}/{len(outcomes['rows'])}", flush=True)
    payload = {
        "format": TEACHER_FORMAT,
        "global_seed": GLOBAL_SEED,
        "ordered_state_ids": [str(row["state_example_id"]) for row in outcomes["rows"]],
        "teacher_rows": teacher_rows,
        "policy_rows": policy_rows,
        "ground_truth_rows": ground_truth_rows,
        "base_states": torch.stack([base_states[str(row["state_example_id"])] for row in outcomes["rows"]]),
        "paired_outcomes_sha256": sha256_file(paths["outcomes"]),
        "elapsed_seconds": time.perf_counter() - started,
    }
    atomic_torch_save(payload, paths["teacher_cache"])
    report = {
        "format": TEACHER_FORMAT,
        "state_count": len(outcomes["rows"]),
        "bare_policy_count": len(teacher_rows),
        "raw_policy_count": len(teacher_rows),
        "selected_raw_positive_count": sum(row["label"] == "POSITIVE" for row in outcomes["rows"]),
        "selected_bare_neutral_harmful_count": sum(row["label"] != "POSITIVE" for row in outcomes["rows"]),
        "cache": str(paths["teacher_cache"]),
        "cache_sha256": sha256_file(paths["teacher_cache"]),
        "qwen_frozen": not any(parameter.requires_grad for parameter in backend.model.parameters()),
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": True,
    }
    atomic_write_json(paths["teacher_report"], report)
    return report


class StaticFeatureBank:
    def __init__(self, *, cfg: Any, settings: Mapping[str, Any], paths: Mapping[str, Path], gate: Mapping[str, Any]) -> None:
        schema = _json(paths["feature_schema"])
        self.schema = FeatureSchema(
            app_vocabulary=tuple(schema["app_vocabulary"]),
            api_vocabulary=tuple(schema["api_vocabulary"]),
            action_vocabulary=tuple(schema["action_vocabulary"]),
            control_vocabulary=tuple(schema["control_vocabulary"]),
            version=str(schema["version"]),
        )
        self.settings = settings
        self.selections = {str(row["state_example_id"]): row for row in _rows(paths["selections"])}
        self.correct = {str(row["state_example_id"]): row for row in _rows(paths["features"]) if bool(row["scoreable"])}
        self.intents = {str(row["state_example_id"]): row for row in _rows(paths["intent_predictions"])}
        self.query_stages = {str(row["state_example_id"]): row["state_stage_signature"] for row in _rows(paths["query_signatures"])}
        self.transitions = {str(row["transition_id"]): row for row in _rows(paths["transitions"])}
        self.signatures = {str(row["transition_id"]): row for row in _rows(paths["signatures"])}
        classes = _json(paths["classes"])["classes"]
        self.classes = {str(row["signature_class_id"]): row for row in classes}
        self.class_by_transition = {
            str(transition_id): str(row["signature_class_id"])
            for row in classes
            for transition_id in row["member_transition_ids"]
        }
        self.examples = _examples_by_state(load_decision_examples(paths["decisions"]))
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(settings["expected_model_name"]), trust_remote_code=True
        )
        self.gate = MemoryUseGate(len(gate["feature_names"]), int(settings["gate"]["hidden_dim"]))
        self.gate.load_state_dict(gate["model_state_dict"])
        self.gate.eval()
        self.mean = gate["standardizer_mean"]
        self.std = gate["standardizer_std"]
        self.temperature = float(gate["temperature"])
        self.activation_threshold = float(gate["activation_threshold"])
        self.maximum_harmful_probability = float(
            gate["maximum_harmful_probability"]
        )
        self.label_position = {name: index for index, name in enumerate(gate["labels"])}

    def feature(self, state_id: str, transition_id: str) -> list[float]:
        selection = self.selections[state_id]
        if (
            str(selection["selected_transition_id"]) == transition_id
            and state_id in self.correct
        ):
            return list(self.correct[state_id]["feature_values"])
        transition = self.transitions[transition_id]
        signature = self.signatures[transition_id]
        action = signature["action_signature"]
        base_messages = _appworld_messages_from_example(
            self.examples[state_id], "full_demo"
        )
        base = self.tokenizer.apply_chat_template(
            base_messages, tokenize=True, add_generation_prompt=True
        )
        raw = self.tokenizer.apply_chat_template(
            messages_with_transition_memory(base_messages, transition, "full_demo"),
            tokenize=True,
            add_generation_prompt=True,
        )
        class_id = self.class_by_transition[transition_id]
        source = {
            "state_step_index": int(selection["state_step_id"]),
            "history_turn_count": max(0, int(selection["state_step_id"]) - 1),
            "prompt_tokens": len(base),
            "context_headroom": int(self.settings["appworld"]["context_limit"]) - len(base),
            "context_limit": int(self.settings["appworld"]["context_limit"]),
            "intent_distributions": self.intents[state_id]["distributions"],
            "selector_class_scores": list(selection["ordered_class_scores"]),
            "memory_apps": [str(value) for value in transition["apps"]],
            "memory_apis": [str(value) for value in transition["api_names"]],
            "memory_action_type": str(action["coarse_action_type"]),
            "memory_control_flow": [str(value) for value in action["control_flow_constructs"]],
            "memory_flags": _memory_flags(action),
            "memory_class_size": int(self.classes[class_id]["class_size"]),
            "memory_token_length": int(transition["teacher_section_tokens"]),
            "memory_parent_step": int(transition["step_index"]),
            "memory_api_call_count": len(action["ordered_api_sequence"]),
            "projected_prompt_overhead": len(raw) - len(base),
            "stage_compatibility": _stage_compatibility(
                self.query_stages[state_id], signature["pre_action_stage_signature"]
            ),
        }
        values, names = build_feature_vector(self.schema, source)
        if names != list(self.schema.names):
            raise RuntimeError("Compiler feature order differs")
        return values

    def register_selection(self, state_id: str, selection: Mapping[str, Any]) -> None:
        if state_id in self.selections and self.selections[state_id] != dict(selection):
            raise ValueError(f"Structured selection differs for {state_id}")
        self.selections[state_id] = dict(selection)

    @torch.no_grad()
    def gate_probabilities(self, values: Sequence[float]) -> dict[str, Any]:
        feature = torch.tensor([values], dtype=torch.float32)
        probability = F.softmax(
            self.gate((feature - self.mean) / self.std) / self.temperature, dim=-1
        )[0]
        probabilities = {
            label: float(probability[index])
            for label, index in self.label_position.items()
        }
        return {
            "probabilities": probabilities,
            "positive_probability": probabilities["POSITIVE"],
            "gate_on": (
                probabilities["POSITIVE"]
                >= self.activation_threshold
                and probabilities["HARMFUL"]
                <= self.maximum_harmful_probability
            ),
        }

    @torch.no_grad()
    def gate_probability(self, values: Sequence[float]) -> float:
        return float(self.gate_probabilities(values)["positive_probability"])


def _mismatch_manifest(rows: Sequence[Mapping[str, Any]], bank: StaticFeatureBank) -> dict[str, Any]:
    training = [row for row in rows if row["model_split"] == "model_train"]
    positive = [row for row in training if row["label"] == "POSITIVE"]
    values = []
    for row in positive:
        state_id = str(row["state_example_id"])
        transition_id = str(row["selected_transition_id"])
        class_id = str(row["selected_class_id"])
        transition_candidates = [
            other
            for other in training
            if str(other["selected_transition_id"]) != transition_id
            and str(other["selected_class_id"]) != class_id
        ]
        state_candidates = [
            other
            for other in training
            if str(other["state_task_id"]) != str(row["state_task_id"])
        ]
        if not transition_candidates or not state_candidates:
            raise RuntimeError("Structured mismatch candidates are unavailable")
        transition_mismatch = min(
            transition_candidates,
            key=lambda other: stable_key(
                GLOBAL_SEED, "7hr-transition-mismatch", state_id, other["selected_transition_id"]
            ),
        )
        state_mismatch = min(
            state_candidates,
            key=lambda other: stable_key(
                GLOBAL_SEED, "7hr-state-mismatch", state_id, other["state_example_id"]
            ),
        )
        values.append(
            {
                "state_example_id": state_id,
                "transition_id": transition_id,
                "transition_mismatch_transition_id": str(transition_mismatch["selected_transition_id"]),
                "transition_mismatch_class_id": str(transition_mismatch["selected_class_id"]),
                "state_mismatch_state_example_id": str(state_mismatch["state_example_id"]),
                "state_mismatch_task_id": str(state_mismatch["state_task_id"]),
                "transition_feature_values": bank.feature(
                    state_id, str(transition_mismatch["selected_transition_id"])
                ),
                "state_feature_values": bank.feature(
                    str(state_mismatch["state_example_id"]), transition_id
                ),
                "behavioral_outcomes_used": False,
            }
        )
    payload = {
        "format": "appworld_structured_compiler_mismatch_manifest_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "positive_training_state_count": len(positive),
        "rows": values,
    }
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def _load_parent(
    *, cfg: Any, paths: Mapping[str, Path], representations: Mapping[str, Any], device: torch.device
) -> tuple[nn.Module, nn.Module, Mapping[str, Any]]:
    summary = _json(paths["parent_training"])
    checkpoint_path = Path(str(summary["selected_checkpoint"]))
    if sha256_file(checkpoint_path) != str(summary["selected_checkpoint_sha256"]):
        raise ValueError("EXP-027B selected PairMLP checkpoint changed")
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    parent_settings = cfg.raw["stage_c_7g"]
    model = _build_model(
        kind="pairmlp",
        settings=parent_settings,
        view_names=representations["transition_view_names"],
        device=device,
    )
    decoder = _build_decoder(parent_settings, 4096, device)
    model.load_state_dict(checkpoint["model_state_dict"])
    decoder.load_state_dict(checkpoint["decoder_state_dict"])
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model, decoder, checkpoint


def _program(
    *, composer: nn.Module, decoder: nn.Module, features: Tensor, base_latent: Tensor, gate_probability: Tensor, base_states: Tensor, maximum_ratio: float
) -> tuple[Tensor, Tensor, Mapping[str, Tensor]]:
    latent = composer(features, base_latent, gate_probability)
    raw = decoder(latent)
    delta, ratios = differentiable_layer_ratio_projection(
        raw, base_states, maximum_ratio=maximum_ratio
    )
    return latent, delta, ratios


def _forward_policy_ground_truth(
    *, backend: Any, delta: Tensor, base: Tensor, policy_row: Mapping[str, Any], ground_truth_row: Mapping[str, Any]
) -> tuple[Tensor, Mapping[str, Tensor], Tensor]:
    batch = _collate([policy_row, ground_truth_row], device=backend.device, k=K_TOKENS)
    with DeepResidualHooks(
        model=backend.model,
        layer_indices=LAYER_INDICES,
        selected_token_indices=_selected_indices(batch),
        delta=delta.repeat(2, 1, 1, 1),
        expected_prefill_length=int(batch["input_ids"].shape[1]),
    ):
        _, logits = _bare_target_forward(backend=backend, batch=batch)
    policy_length = int(policy_row["target_len"])
    return logits[:policy_length], {}, logits[policy_length:]


def _train_phase(
    *, cfg: Any, replay: Mapping[str, Any], settings: Mapping[str, Any], paths: Mapping[str, Path], attempt: AttemptLedger
) -> dict[str, Any]:
    seed_everything(GLOBAL_SEED)
    backend = _build_backend_from_generation(replay["causal_audit"]["generation"])
    if any(parameter.requires_grad for parameter in backend.model.parameters()):
        raise RuntimeError("Structured compiler loaded trainable Qwen")
    # Frozen-Qwen target forwards enable non-reentrant gradient checkpointing.
    # Keep the model in the same mode through backward recomputation; restoring
    # eval mode immediately after forward changes the checkpointed graph.
    if bool(getattr(backend, "_gradient_checkpointing_enabled", False)):
        backend.model.train()
    outcomes = _json(paths["outcomes"])
    rows = list(outcomes["rows"])
    training_rows = [row for row in rows if row["model_split"] == "model_train"]
    teacher = torch.load(paths["teacher_cache"], map_location="cpu", weights_only=False)
    ordered = list(teacher["ordered_state_ids"])
    base_states = {
        state_id: teacher["base_states"][index].to(torch.float32)
        for index, state_id in enumerate(ordered)
    }
    representations = _load_representations(
        {"state_cache": paths["state_cache"], "transition_cache": paths["transition_cache"]},
        backend.device,
    )
    parent_model, decoder, parent_checkpoint = _load_parent(
        cfg=cfg, paths=paths, representations=representations, device=backend.device
    )
    gate = torch.load(paths["gate"], map_location="cpu", weights_only=False)
    bank = StaticFeatureBank(cfg=cfg, settings=settings, paths=paths, gate=gate)
    if paths["mismatches"].exists():
        mismatch_payload = _json(paths["mismatches"])
    else:
        mismatch_payload = _mismatch_manifest(rows, bank)
        atomic_write_json(paths["mismatches"], mismatch_payload)
    mismatch = {str(row["state_example_id"]): row for row in mismatch_payload["rows"]}
    composer = StructuredLatentComposer(
        len(gate["feature_names"]),
        int(settings["compiler"]["structured_hidden_dim"]),
        int(settings["compiler"]["program_dim"]),
    ).to(backend.device)
    decoder.train()
    composer.train()
    optimizer = torch.optim.AdamW(
        [
            {"params": [composer.beta], "lr": float(settings["compiler"]["beta_learning_rate"])},
            {"params": list(composer.structured.parameters()), "lr": float(settings["compiler"]["structured_learning_rate"])},
            {"params": list(decoder.parameters()), "lr": float(settings["compiler"]["decoder_learning_rate"])},
        ],
        weight_decay=float(settings["compiler"]["weight_decay"]),
    )
    state_position = representations["state_position"]
    transition_position = representations["transition_position"]
    pair_ids = [str(row["state_example_id"]) for row in training_rows]
    update_counts = [0] * len(pair_ids)
    history = []
    completed_rounds = 0
    if paths["latest_checkpoint"].exists():
        latest = _json(paths["latest_checkpoint"])
        checkpoint_path = Path(str(latest["checkpoint"]))
        payload = torch.load(
            checkpoint_path, map_location=backend.device, weights_only=False
        )
        checks = {
            "format": str(payload.get("format")) == CHECKPOINT_FORMAT,
            "seed": int(payload.get("global_seed", -1)) == GLOBAL_SEED,
            "pairs": list(payload.get("pair_ids", [])) == pair_ids,
            "gate": str(payload.get("gate_checkpoint_sha256"))
            == sha256_file(paths["gate"]),
            "teachers": str(payload.get("teacher_cache_sha256"))
            == sha256_file(paths["teacher_cache"]),
            "mismatches": str(payload.get("mismatch_manifest_sha256"))
            == sha256_file(paths["mismatches"]),
        }
        if not all(checks.values()):
            raise ValueError(f"Structured compiler resume identity differs: {checks}")
        composer.load_state_dict(payload["composer_state_dict"])
        decoder.load_state_dict(payload["decoder_state_dict"])
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        update_counts = [int(value) for value in payload["update_counts"]]
        completed_rounds = int(payload["updates_per_pair"])
        history = list(payload["history"])
        random.setstate(payload["python_random_state"])
        torch.set_rng_state(payload["torch_rng_state"])
        if torch.cuda.is_available() and payload["cuda_rng_state"]:
            torch.cuda.set_rng_state_all(payload["cuda_rng_state"])
    started = time.perf_counter()
    for update_round in range(
        completed_rounds + 1,
        int(settings["compiler"]["maximum_updates_per_pair"]) + 1,
    ):
        order = sorted(
            range(len(training_rows)),
            key=lambda index: stable_key(GLOBAL_SEED, "7hr-compiler", update_round, pair_ids[index]),
        )
        round_metrics = []
        for index in order:
            row = training_rows[index]
            state_id = str(row["state_example_id"])
            transition_id = str(row["selected_transition_id"])
            units = [("correct", state_id, transition_id, list(row["feature_values"]))]
            if row["label"] == "POSITIVE":
                control = mismatch[state_id]
                units.extend(
                    [
                        (
                            "transition_mismatch",
                            state_id,
                            str(control["transition_mismatch_transition_id"]),
                            list(control["transition_feature_values"]),
                        ),
                        (
                            "state_mismatch",
                            str(control["state_mismatch_state_example_id"]),
                            transition_id,
                            list(control["state_feature_values"]),
                        ),
                    ]
                )
            for unit_name, program_state_id, program_transition_id, feature_values in units:
                target_name = (
                    "raw"
                    if unit_name == "correct" and row["label"] == "POSITIVE"
                    else "bare"
                )
                target_teacher = teacher["teacher_rows"][state_id][target_name]
                policy_row = teacher["policy_rows"][state_id][target_name]
                ground_truth = teacher["ground_truth_rows"][state_id]
                state = representations["state_values"][state_position[program_state_id]].unsqueeze(0).to(backend.device)
                transition = representations["transition_values"][transition_position[program_transition_id]].unsqueeze(0).to(backend.device)
                with torch.no_grad():
                    base_latent = parent_model(state, transition)
                feature = torch.tensor([feature_values], dtype=torch.float32, device=backend.device)
                normalized = (feature - gate["standardizer_mean"].to(backend.device)) / gate["standardizer_std"].to(backend.device)
                with torch.no_grad():
                    gate_model = bank.gate.to(backend.device)
                    gate_probability = F.softmax(
                        gate_model(normalized) / float(gate["temperature"]), dim=-1
                    )[:, list(gate["labels"]).index("POSITIVE")]
                base = base_states[state_id].unsqueeze(0).to(backend.device)
                optimizer.zero_grad(set_to_none=True)
                latent, delta, ratios = _program(
                    composer=composer,
                    decoder=decoder,
                    features=normalized,
                    base_latent=base_latent,
                    gate_probability=gate_probability,
                    base_states=base,
                    maximum_ratio=float(settings["compiler"]["ratio_budget_per_layer"]),
                )
                batch = _collate(
                    [policy_row, ground_truth], device=backend.device, k=K_TOKENS
                )
                # Gradient-checkpoint recomputation happens during backward, so
                # the residual hooks must remain installed until it completes.
                with DeepResidualHooks(
                    model=backend.model,
                    layer_indices=LAYER_INDICES,
                    selected_token_indices=_selected_indices(batch),
                    delta=delta.repeat(2, 1, 1, 1),
                    expected_prefill_length=int(batch["input_ids"].shape[1]),
                ):
                    _, logits = _bare_target_forward(backend=backend, batch=batch)
                    policy_length = int(policy_row["target_len"])
                    policy_logits = logits[:policy_length]
                    gt_logits = logits[policy_length:]
                    kl, terms = _policy_loss(policy_logits, target_teacher)
                    gt_ce = F.cross_entropy(
                        gt_logits.to(torch.float32),
                        _target_ids(ground_truth, backend.device),
                    )
                    loss = (
                        float(settings["compiler"]["policy_kl_weight"]) * kl
                        + float(settings["compiler"]["teacher_token_ce_weight"])
                        * terms["teacher_token_ce"]
                        + float(settings["compiler"]["ground_truth_ce_weight"])
                        * gt_ce
                        + float(settings["compiler"]["ratio_restraint_weight"])
                        * (
                            F.relu(ratios["raw_layer_ratio"] - 1.0).pow(2).mean()
                            + 0.01 * latent.pow(2).mean()
                        )
                    )
                    loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    list(composer.parameters()) + list(decoder.parameters()),
                    float(settings["compiler"]["max_grad_norm"]),
                )
                optimizer.step()
                maximum_ratio = float(ratios["maximum_ratio"].detach().cpu())
                if maximum_ratio > 1.0001 or not math.isfinite(float(loss.detach().cpu())):
                    raise RuntimeError("Structured compiler ratio or finite-value contract failed")
                round_metrics.append(
                    {
                        "unit": unit_name,
                        "loss": float(loss.detach().cpu()),
                        "policy_kl": float(kl.detach().cpu()),
                        "teacher_token_ce": float(terms["teacher_token_ce"].detach().cpu()),
                        "ground_truth_ce": float(gt_ce.detach().cpu()),
                        "maximum_ratio": maximum_ratio,
                    }
                )
            update_counts[index] += 1
        if update_round not in set(settings["compiler"]["checkpoint_updates"]):
            continue
        checkpoint = paths["root"] / f"checkpoints/model_u{update_round:02d}.pt"
        history_entry = {
            "updates_per_pair": update_round,
            "beta": float(composer.beta.detach().cpu()),
            "round_metrics": {
                name: statistics.fmean(float(row[name]) for row in round_metrics)
                for name in ("loss", "policy_kl", "teacher_token_ce", "ground_truth_ce")
            },
            "maximum_ratio": max(float(row["maximum_ratio"]) for row in round_metrics),
            "elapsed_seconds": time.perf_counter() - started,
        }
        payload = {
            "format": CHECKPOINT_FORMAT,
            "global_seed": GLOBAL_SEED,
            "updates_per_pair": update_round,
            "pair_ids": pair_ids,
            "update_counts": update_counts,
            "update_accounting": update_count_summary(pair_ids, update_counts),
            "history": [*history, history_entry],
            "composer_state_dict": {key: value.detach().cpu() for key, value in composer.state_dict().items()},
            "decoder_state_dict": {key: value.detach().cpu() for key, value in decoder.state_dict().items()},
            "optimizer_state_dict": optimizer.state_dict(),
            "parent_pairmlp_checkpoint_sha256": sha256_file(Path(str(_json(paths["parent_training"])["selected_checkpoint"]))),
            "parent_pairmlp_model_sha256": module_state_sha256(parent_model),
            "feature_schema_sha256": sha256_file(paths["feature_schema"]),
            "gate_checkpoint_sha256": sha256_file(paths["gate"]),
            "teacher_cache_sha256": sha256_file(paths["teacher_cache"]),
            "mismatch_manifest_sha256": sha256_file(paths["mismatches"]),
            **history_entry,
            "python_random_state": random.getstate(),
            "torch_rng_state": torch.get_rng_state(),
            "cuda_rng_state": (
                torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []
            ),
        }
        atomic_torch_save(payload, checkpoint)
        entry = {
            **history_entry,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": sha256_file(checkpoint),
        }
        history.append(entry)
        atomic_write_json(paths["latest_checkpoint"], entry)
        attempt.progress(
            status=f"structured_compiler_u{update_round}",
            latest_validated_checkpoint=str(checkpoint),
        )
        print(json.dumps(entry, sort_keys=True), flush=True)
    summary = {
        "format": "appworld_structured_deep_compiler_training_7hr_v1",
        "global_seed": GLOBAL_SEED,
        "train_state_count": len(training_rows),
        "positive_train_state_count": sum(row["label"] == "POSITIVE" for row in training_rows),
        "training_unit_count_per_round": len(training_rows) + 2 * sum(row["label"] == "POSITIVE" for row in training_rows),
        "history": history,
        "parent_pairmlp_frozen": not any(parameter.requires_grad for parameter in parent_model.parameters()),
        "qwen_frozen": not any(parameter.requires_grad for parameter in backend.model.parameters()),
        "observation_excluded": True,
        "student_prompt_contains_raw_transition": False,
        "elapsed_seconds": time.perf_counter() - started,
        "passed": len(history) == 2 and [row["updates_per_pair"] for row in history] == [2, 4],
    }
    atomic_write_json(paths["training_summary"], summary)
    return summary


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    replay_cfg = load_config(args.replay_config)
    settings = cfg.raw["stage_c_7hr"]
    replay = replay_cfg.raw["stage_c_7b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-028A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    paths = _paths(settings, args.artifact_dir)
    required = (
        "outcomes",
        "features",
        "selections",
        "feature_schema",
        "decisions",
        "transitions",
        "signatures",
        "classes",
        "query_signatures",
        "intent_predictions",
        "state_cache",
        "transition_cache",
        "parent_training",
    )
    gate_required = (
        args.phase != "teacher" or "stage_c_11b" not in cfg.raw
    )
    if gate_required:
        required += ("gate", "gate_report")
    missing = {name: str(paths[name]) for name in required if not paths[name].exists()}
    if missing:
        raise FileNotFoundError(f"Missing structured compiler inputs: {missing}")
    if gate_required and not bool(_json(paths["gate_report"])["passed"]):
        raise RuntimeError("Train-side causal gate did not pass")
    source_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase=f"structured_compiler_{args.phase}",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=source_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        if args.phase == "teacher":
            result = _teacher_phase(
                cfg=cfg,
                replay=replay,
                settings=settings,
                paths=paths,
                attempt=attempt,
            )
        else:
            if not paths["teacher_cache"].exists():
                raise FileNotFoundError("Structured policy teacher cache is unavailable")
            result = _train_phase(
                cfg=cfg,
                replay=replay,
                settings=settings,
                paths=paths,
                attempt=attempt,
            )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
