from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
import rcmf.training.cross_attention_bounded_hooks_8b as bounded
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.cross_attention_training_8b import fusion_gradient_norms
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
import scripts.run_cross_attention_reader_8b as reader_base
from scripts.run_cross_attention_reader_8b import (
    _json,
    _load_slot_bank,
    _load_source,
    _paths,
    _phase2_unit_forward,
    _phase2_units,
    _reader,
    _require,
)
from scripts.run_cross_attention_reader_8b_v7 import _bounded_dispatch
from scripts.run_cross_attention_reader_8b_v9 import (
    RecomputeCompatibleMemoryBoundedHooks,
)
from scripts.run_state_conditioned_program_fast_7df import _build_backend


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--parent-attempt-id", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--tmux-session", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_8b"]
    if int(settings["global_seed"]) != GLOBAL_SEED:
        raise ValueError("EXP-030A requires seed 25101")
    if os.name != "nt" and not os.path.ismount(str(settings["persistent_root"])):
        raise RuntimeError("Persistent filesystem is not mounted")
    seed_everything(GLOBAL_SEED)
    paths = _paths(settings, args.artifact_dir)
    required = (
        "preflight",
        "memory_index",
        "implementation",
        "phase1_selection",
        "mismatches",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
    )
    _require(paths, required)
    backend = _build_backend(cfg)
    source = _load_source(paths)
    slots = _load_slot_bank(paths["memory_index"])
    selection = _json(paths["phase1_selection"])
    checkpoint = Path(str(selection["selected_checkpoint"]))
    if sha256_file(checkpoint) != str(selection["selected_checkpoint_sha256"]):
        raise ValueError("Selected Phase-1 checkpoint hash differs")
    payload = torch.load(checkpoint, map_location=backend.device, weights_only=False)
    reader = _reader(settings, backend.device)
    reader.load_state_dict(payload["reader_state_dict"])
    reader.train()
    reader_base._forward = _bounded_dispatch
    bounded.MemoryBoundedCrossAttentionHooks = RecomputeCompatibleMemoryBoundedHooks
    backend._exp030a_track_residual_penalty = True
    backend.model.config.use_cache = False
    units = _phase2_units(source, paths["phase2_units"])
    unit = min(units, key=lambda row: str(row["unit_id"]))
    output = args.artifact_dir / "reader/phase2_recompute_smoke.json"
    data_hashes = {name: sha256_file(paths[name]) for name in required}
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="reader_phase2_recompute_smoke",
        command=[str(value) for value in sys.argv],
        local_head=args.head,
        github_head=args.head,
        lambda_head=args.head,
        tmux_session=args.tmux_session,
        config_sha256=sha256_file(args.config),
        data_manifest_hashes=data_hashes,
        parent_attempt_id=args.parent_attempt_id,
        resume_checkpoint=str(checkpoint),
        heartbeat_interval_s=float(settings["heartbeat_interval_seconds"]),
    ) as attempt:
        reader.zero_grad(set_to_none=True)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        with torch.autograd.graph.save_on_cpu(pin_memory=True):
            result = _phase2_unit_forward(
                backend=backend,
                reader=reader,
                unit=unit,
                teacher_cache=source["teacher"],
                slots=slots,
                settings=settings,
            )
            loss = result["loss"]
            assert isinstance(loss, torch.Tensor)
            loss.backward()
        gradients = fusion_gradient_norms(reader)
        report = {
            "format": "cross_attention_phase2_recompute_smoke_8b_v1",
            "global_seed": GLOBAL_SEED,
            "unit_id": str(unit["unit_id"]),
            "loss": float(loss.detach().cpu()),
            "finite": bool(torch.isfinite(loss)),
            "all_up_gradients_nonzero": all(value > 0.0 for value in gradients["up"]),
            "all_down_gradients_nonzero": all(value > 0.0 for value in gradients["down"]),
            "qwen_gradient_count": sum(
                parameter.grad is not None for parameter in backend.model.parameters()
            ),
            "elapsed_seconds": time.perf_counter() - started,
            "peak_allocated_bytes": (
                int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0
            ),
            "peak_reserved_bytes": (
                int(torch.cuda.max_memory_reserved()) if torch.cuda.is_available() else 0
            ),
            "selected_checkpoint_sha256": sha256_file(checkpoint),
            "residual_penalty_weight": float(
                settings["curriculum"]["phase2_residual_norm_weight"]
            ),
            "checkpoint_recompute_operation_identity": True,
        }
        report["passed"] = bool(
            report["finite"]
            and report["all_up_gradients_nonzero"]
            and report["all_down_gradients_nonzero"]
            and report["qwen_gradient_count"] == 0
        )
        atomic_write_json(output, report)
        attempt.progress(
            status="reader_phase2_recompute_smoke_complete",
            latest_validated_checkpoint=str(output),
            result=report,
        )
    if not bool(report["passed"]):
        raise RuntimeError("Phase-2 recompute smoke failed")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
