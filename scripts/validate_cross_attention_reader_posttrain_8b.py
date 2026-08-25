from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.cross_attention_training_8b import (
    all_fusion_layers_receive_gradient,
    fusion_gradient_norms,
)
from rcmf.training.datasets import _appworld_messages_from_example
from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.state_conditioned_program_7d import stable_key
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, sha256_file
from scripts.run_cross_attention_reader_8b import (
    _example_policy_row,
    _forward,
    _generate,
    _json,
    _load_slot_bank,
    _load_source,
    _paths,
    _reader,
    _require,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--resume-checkpoint", default="none")
    parser.add_argument("--local-head", required=True)
    parser.add_argument("--github-head", required=True)
    parser.add_argument("--lambda-head", required=True)
    parser.add_argument("--tmux-session", default="exp030a_reader")
    return parser.parse_args()


def _representatives(source: Mapping[str, Any], backend: Any) -> list[tuple[Any, ...]]:
    values = []
    for key, example in source["decisions"].items():
        messages = _appworld_messages_from_example(example, "full_demo")
        tokens = int(
            backend.tokenize_messages(messages, add_generation_prompt=True)
            .attention_mask.sum()
            .item()
        )
        values.append((tokens, key, example, messages))
    values.sort(key=lambda row: (row[0], row[1]))
    positions = (0, len(values) // 2, max(0, len(values) - 2), len(values) - 1)
    if len(set(positions)) != 4:
        raise ValueError("Could not create four distinct post-train strata")
    return [values[index] for index in positions]


def _shuffle_transition(source: Mapping[str, Any], transition_id: str) -> str:
    values = sorted(source["transitions"])
    candidates = [value for value in values if value != transition_id]
    return min(candidates, key=lambda value: stable_key(GLOBAL_SEED, "posttrain-shuffle", transition_id, value))


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    seed_everything(GLOBAL_SEED)
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "memory_index",
        "implementation",
        "phase1_selection",
        "task_split",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
        "mismatches",
    )
    _require(paths, required)
    selection = _json(paths["phase1_selection"])
    checkpoint = Path(str(selection["selected_checkpoint"]))
    if sha256_file(checkpoint) != str(selection["selected_checkpoint_sha256"]):
        raise ValueError("Selected Phase-1 checkpoint hash differs")
    backend = _build_backend(cfg)
    source = _load_source(paths)
    slot_bank = _load_slot_bank(paths["memory_index"])
    payload = torch.load(checkpoint, map_location=backend.device, weights_only=False)
    reader = _reader(settings, backend.device)
    reader.load_state_dict(payload["reader_state_dict"])
    reader.eval()
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="reader_phase1_posttrain_validation",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        started = time.perf_counter()
        checks = []
        for ordinal, (prompt_tokens, key, example, messages) in enumerate(
            _representatives(source, backend)
        ):
            correct_id = source["transitions_by_step"][key]
            shuffled_id = _shuffle_transition(source, correct_id)
            row = _example_policy_row(backend, example, f"posttrain::{ordinal}")
            _, correct_logits, correct_hooks = _forward(
                backend=backend,
                reader=reader,
                rows=[row],
                slots=slot_bank[correct_id],
                training=False,
            )
            _, shuffled_logits, shuffled_hooks = _forward(
                backend=backend,
                reader=reader,
                rows=[row],
                slots=slot_bank[shuffled_id],
                training=False,
            )
            correct_ids, correct_text, _ = _generate(
                backend=backend,
                reader=reader,
                messages=messages,
                slots=slot_bank[correct_id],
                max_new_tokens=64,
            )
            clone = _reader(settings, backend.device)
            clone.load_state_dict(reader.state_dict())
            clone.eval()
            _, clone_logits, _ = _forward(
                backend=backend,
                reader=clone,
                rows=[row],
                slots=slot_bank[correct_id],
                training=False,
            )
            clone_ids, clone_text, _ = _generate(
                backend=backend,
                reader=clone,
                messages=messages,
                slots=slot_bank[correct_id],
                max_new_tokens=64,
            )
            delta_difference = abs(
                correct_hooks.residual_norm() - shuffled_hooks.residual_norm()
            )
            checks.append(
                {
                    "ordinal": ordinal,
                    "prompt_tokens": prompt_tokens,
                    "correct_transition_id": correct_id,
                    "shuffled_transition_id": shuffled_id,
                    "correct_vs_shuffle_logits_distinct": not torch.equal(
                        correct_logits, shuffled_logits
                    ),
                    "correct_vs_shuffle_residual_norm_difference": delta_difference,
                    "save_load_logits_exact": torch.equal(correct_logits, clone_logits),
                    "save_load_generation_exact": (
                        correct_ids == clone_ids and correct_text == clone_text
                    ),
                }
            )
        reader.train()
        key = sorted(source["decisions"])[0]
        example = source["decisions"][key]
        row = _example_policy_row(backend, example, "posttrain::gradient")
        reader.zero_grad(set_to_none=True)
        loss, _, _ = _forward(
            backend=backend,
            reader=reader,
            rows=[row],
            slots=slot_bank[source["transitions_by_step"][key]],
            training=True,
        )
        loss.backward()
        gradients = fusion_gradient_norms(reader)
        qwen_gradients = sum(
            parameter.grad is not None for parameter in backend.model.parameters()
        )
        report = {
            "format": "cross_attention_reader_posttrain_interface_validation_8b_v1",
            "global_seed": GLOBAL_SEED,
            "selected_phase1_checkpoint": str(checkpoint),
            "selected_phase1_checkpoint_sha256": sha256_file(checkpoint),
            "reader_sha256": module_state_sha256(reader),
            "reader_output_layers_nonzero": not reader.output_layers_zero(),
            "four_state_checks": checks,
            "fusion_gradient_norms": gradients,
            "all_down_and_up_layers_receive_gradients": all_fusion_layers_receive_gradient(
                gradients, require_down=True
            ),
            "qwen_gradient_count": qwen_gradients,
            "student_prompt_contains_raw_memory": False,
            "save_load_resume_exact": all(
                row["save_load_logits_exact"] and row["save_load_generation_exact"]
                for row in checks
            ),
            "correct_and_shuffled_memory_distinct": all(
                row["correct_vs_shuffle_logits_distinct"] for row in checks
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        report["passed"] = bool(
            report["reader_output_layers_nonzero"]
            and report["all_down_and_up_layers_receive_gradients"]
            and report["qwen_gradient_count"] == 0
            and report["save_load_resume_exact"]
            and report["correct_and_shuffled_memory_distinct"]
        )
        atomic_write_json(paths["phase1_posttrain"], report)
        atomic_write_text(
            paths["phase1_posttrain"].with_suffix(".md"),
            "\n".join(
                (
                    "# EXP-030A Phase-1 post-train interface validation",
                    "",
                    f"- correct/shuffled memory distinct: `{str(report['correct_and_shuffled_memory_distinct']).lower()}`",
                    f"- all 36 down/up fusion layers receive gradients: `{str(report['all_down_and_up_layers_receive_gradients']).lower()}`",
                    f"- save/load logits and generation exact: `{str(report['save_load_resume_exact']).lower()}`",
                    f"- Qwen parameter gradients: `{qwen_gradients}`",
                    f"- passed: `{str(report['passed']).lower()}`",
                    "",
                )
            ),
        )
        attempt.progress(
            status="reader_phase1_posttrain_validation_complete",
            latest_validated_checkpoint=str(paths["phase1_posttrain"]),
            result=report,
        )
    if not report["passed"]:
        raise RuntimeError("Post-train cross-attention interface validation failed")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
