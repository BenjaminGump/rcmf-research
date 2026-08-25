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
from rcmf.training.cross_attention_memory_8b import (
    render_observation_excluded_transition,
)
from rcmf.training.cross_attention_memory_sampling_8b import (
    capped_memory_token_indices,
    slot_indices,
)
from rcmf.training.oracle_convergence_5fa import atomic_torch_save
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import (
    atomic_write_json,
    atomic_write_text,
    read_jsonl,
    sha256_file,
    sha256_text,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


CACHE_VERSION = "observation_excluded_layer_memory_slots_8b_v1"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    parser.add_argument("--tmux-session", default="exp030a_memory_cache")
    return parser.parse_args()


def _cache_path(root: Path, transition_id: str) -> Path:
    return root / "rows" / f"{sha256_text(transition_id)}.pt"


def _validate_cached(
    path: Path,
    *,
    transition_id: str,
    rendered_sha256: str,
    layer_count: int,
    slot_count: int,
    model_dim: int,
) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    checks = {
        "format": payload.get("format") == CACHE_VERSION,
        "transition": str(payload.get("transition_id")) == transition_id,
        "render": str(payload.get("rendered_memory_sha256")) == rendered_sha256,
        "shape": tuple(payload["slots"].shape)
        == (layer_count, slot_count, model_dim),
        "finite": bool(torch.isfinite(payload["slots"].to(torch.float32)).all()),
        "outcome_excluded": bool(payload.get("post_action_observation_excluded")),
    }
    if not all(checks.values()):
        raise ValueError(f"Existing memory slot row differs: {checks}")
    return payload


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings: Mapping[str, Any] = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires global seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    preflight_path = args.artifact_dir / "runtime_preflight.json"
    manifest_path = args.artifact_dir / "memory/observation_excluded_manifest.json"
    transitions_path = (
        Path(str(settings["parent_exp025b"]))
        / "clean_cache_rebuild/transition_preflight/transition_manifest.jsonl"
    )
    for path in (preflight_path, manifest_path, transitions_path):
        if not path.exists():
            raise FileNotFoundError(path)
    if not bool(_json(preflight_path)["automatic_launch_allowed"]):
        raise RuntimeError("Runtime preflight did not authorize EXP-030A")
    ledger = {str(row["transition_id"]): dict(row) for row in read_jsonl(transitions_path)}
    manifest = _json(manifest_path)
    ordered_ids = [str(row["transition_id"]) for row in manifest["rows"]]
    if len(ordered_ids) != 499 or len(set(ordered_ids)) != 499:
        raise ValueError("Memory manifest does not contain 499 unique transitions")

    backend = _build_backend(cfg)
    backend.model.eval()
    for parameter in backend.model.parameters():
        parameter.requires_grad_(False)
    model_dim = int(settings["reader"]["model_dim"])
    layer_count = int(settings["reader"]["qwen_layer_count"])
    token_cap = int(settings["memory"]["token_cap_before_sampling"])
    slot_count = int(settings["memory"]["slot_count"])
    if int(backend.model.config.hidden_size) != model_dim:
        raise ValueError("Loaded Qwen hidden size differs from EXP-030A")
    if int(backend.model.config.num_hidden_layers) != layer_count:
        raise ValueError("Loaded Qwen layer count differs from EXP-030A")

    root = args.artifact_dir / "memory/slot_cache"
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="memory_slot_cache",
        command=[str(value) for value in sys.argv],
        local_head=args.local_head,
        github_head=args.github_head,
        lambda_head=args.lambda_head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "preflight": sha256_file(preflight_path),
            "memory_manifest": sha256_file(manifest_path),
            "transitions": sha256_file(transitions_path),
        },
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=args.resume_checkpoint,
        scientific_parameter_changed=False,
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        for ordinal, transition_id in enumerate(ordered_ids, start=1):
            transition = ledger[transition_id]
            rendered = render_observation_excluded_transition(transition)
            rendered_sha256 = sha256_text(rendered)
            output = _cache_path(root, transition_id)
            if output.exists():
                payload = _validate_cached(
                    output,
                    transition_id=transition_id,
                    rendered_sha256=rendered_sha256,
                    layer_count=layer_count,
                    slot_count=slot_count,
                    model_dim=model_dim,
                )
                cache_source = "resumed_atomic_row"
            else:
                full_ids = [
                    int(value)
                    for value in backend.tokenizer(
                        rendered,
                        add_special_tokens=True,
                        truncation=False,
                    )["input_ids"]
                ]
                source_indices = capped_memory_token_indices(len(full_ids), token_cap)
                capped_ids = [full_ids[index] for index in source_indices]
                input_ids = torch.tensor(
                    [capped_ids], dtype=torch.long, device=backend.device
                )
                attention_mask = torch.ones_like(input_ids)
                with torch.no_grad():
                    result = backend.model.model(
                        input_ids=input_ids,
                        attention_mask=attention_mask,
                        output_hidden_states=True,
                        use_cache=False,
                        return_dict=True,
                    )
                hidden_states = result.hidden_states
                if len(hidden_states) != layer_count + 1:
                    raise ValueError("Qwen hidden-state count differs from layer contract")
                sampled = slot_indices(len(capped_ids), slot_count)
                slots = torch.stack(
                    [hidden[0, sampled] for hidden in hidden_states[1:]], dim=0
                ).detach().to(torch.bfloat16).cpu()
                payload = {
                    "format": CACHE_VERSION,
                    "global_seed": GLOBAL_SEED,
                    "transition_id": transition_id,
                    "parent_id": str(transition["parent_memory_id"]),
                    "parent_task_id": str(transition["parent_task_id"]),
                    "step_index": int(transition["step_index"]),
                    "rendered_memory_sha256": rendered_sha256,
                    "raw_ledger_transition_content_sha256": str(
                        transition["transition_content_sha256"]
                    ),
                    "source_token_count": len(full_ids),
                    "capped_token_count": len(capped_ids),
                    "source_token_indices": source_indices,
                    "sampled_capped_token_indices": sampled,
                    "token_cap": token_cap,
                    "slot_count": slot_count,
                    "layer_count": layer_count,
                    "model_dim": model_dim,
                    "post_action_observation_excluded": True,
                    "student_prompt_contains_raw_memory": False,
                    "slots": slots,
                }
                payload["provenance_sha256"] = canonical_sha256(
                    {key: value for key, value in payload.items() if key != "slots"}
                )
                atomic_torch_save(payload, output)
                cache_source = "new_frozen_qwen_encoding"
            rows.append(
                {
                    "transition_id": transition_id,
                    "cache_path": str(output),
                    "cache_sha256": sha256_file(output),
                    "source_token_count": int(payload["source_token_count"]),
                    "capped_token_count": int(payload["capped_token_count"]),
                    "provenance_sha256": str(payload["provenance_sha256"]),
                    "cache_source": cache_source,
                }
            )
            if ordinal % 10 == 0 or ordinal == len(ordered_ids):
                attempt.progress(
                    status="memory_slot_cache",
                    completed_memories=ordinal,
                    total_memories=len(ordered_ids),
                    latest_validated_checkpoint=str(output),
                )
        index = {
            "format": "observation_excluded_memory_slot_cache_index_8b_v1",
            "global_seed": GLOBAL_SEED,
            "memory_count": len(rows),
            "ordered_transition_ids": ordered_ids,
            "rows": rows,
            "student_prompt_contains_raw_memory": False,
            "qwen_trainable_parameter_count": sum(
                parameter.requires_grad for parameter in backend.model.parameters()
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        index["manifest_sha256"] = canonical_sha256(index)
        index_path = root / "index.json"
        atomic_write_json(index_path, index)
        summary = {
            "format": "observation_excluded_memory_slot_cache_summary_8b_v1",
            "memory_count": len(rows),
            "layer_count": layer_count,
            "slot_count": slot_count,
            "model_dim": model_dim,
            "cache_bytes": sum(Path(row["cache_path"]).stat().st_size for row in rows),
            "elapsed_seconds": time.perf_counter() - started,
            "index_sha256": sha256_file(index_path),
            "passed": len(rows) == 499,
        }
        summary_path = root / "summary.json"
        atomic_write_json(summary_path, summary)
        atomic_write_text(
            root / "report.md",
            "\n".join(
                (
                    "# EXP-030A observation-excluded memory-slot cache",
                    "",
                    f"- memories: `{len(rows)}`",
                    f"- shape per memory: `{layer_count}x{slot_count}x{model_dim}`",
                    f"- cache bytes: `{summary['cache_bytes']}`",
                    f"- elapsed seconds: `{summary['elapsed_seconds']:.3f}`",
                    "- post-action observation included: `false`",
                    "- student prompt contains raw memory: `false`",
                    "- Qwen trainable parameters: `0`",
                    "",
                )
            ),
        )
        attempt.progress(
            status="memory_slot_cache_complete",
            latest_validated_checkpoint=str(summary_path),
            result=summary,
        )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
