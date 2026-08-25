from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import statistics
import time

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.cross_attention_field_8b import GLOBAL_SEED
from rcmf.training.cross_attention_offload_8b import offloaded_checkpoint_reader_forward
from rcmf.training.datasets import _appworld_messages_from_example
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_cross_attention_reader_8b import (
    _example_policy_row,
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
        default=Path("configs/benchmark/stage_c_cross_attention_field_8b_verified.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def _representatives(source: Mapping[str, object], backend) -> list[tuple]:
    values = []
    for key, example in source["decisions"].items():
        messages = _appworld_messages_from_example(example, "full_demo")
        tokens = int(
            backend.tokenize_messages(messages, add_generation_prompt=True)
            .attention_mask.sum()
            .item()
        )
        values.append((tokens, key, example))
    values.sort(key=lambda row: (row[0], row[1]))
    return [values[0], values[len(values) // 2], values[-1]]


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
        "mismatches",
        "task_split",
        "transitions",
        "decisions",
        "outcomes",
        "teacher_cache",
        "implementation",
    )
    _require(paths, required)
    backend = _build_backend(cfg)
    source = _load_source(paths)
    slots = _load_slot_bank(paths["memory_index"])
    rows = []
    for ordinal, (prompt_tokens, key, example) in enumerate(
        _representatives(source, backend)
    ):
        reader = _reader(settings, backend.device)
        reader.train()
        reader.zero_grad(set_to_none=True)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
        policy = _example_policy_row(backend, example, f"offload-smoke::{ordinal}")
        started = time.perf_counter()
        loss, _, _ = offloaded_checkpoint_reader_forward(
            backend=backend,
            reader=reader,
            rows=[policy],
            slots=slots[source["transitions_by_step"][key]],
            training=True,
        )
        loss.backward()
        elapsed = time.perf_counter() - started
        rows.append(
            {
                "ordinal": ordinal,
                "prompt_tokens": prompt_tokens,
                "elapsed_seconds": elapsed,
                "loss": float(loss.detach().cpu()),
                "peak_allocated_bytes": (
                    int(torch.cuda.max_memory_allocated())
                    if torch.cuda.is_available()
                    else 0
                ),
                "peak_reserved_bytes": (
                    int(torch.cuda.max_memory_reserved())
                    if torch.cuda.is_available()
                    else 0
                ),
                "finite": bool(torch.isfinite(loss)),
            }
        )
    mean_seconds = statistics.fmean(row["elapsed_seconds"] for row in rows)
    projected_backward_hours = mean_seconds * int(
        _json(paths["preflight"])["execution_counts"]["backwards"]
    ) / 3600.0
    report = {
        "format": "cross_attention_reader_offload_backward_smoke_8b_v1",
        "global_seed": GLOBAL_SEED,
        "config_sha256": sha256_file(args.config),
        "rows": rows,
        "mean_backward_seconds": mean_seconds,
        "projected_backward_h100_hours": projected_backward_hours,
        "runtime_review_threshold_h100_hours": float(
            settings["runtime"]["review_threshold_h100_hours"]
        ),
        "no_truncation": True,
        "layer_count": 36,
        "slot_count": 16,
        "passed": all(row["finite"] for row in rows),
    }
    atomic_write_json(args.artifact_dir / "reader/offload_backward_smoke.json", report)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()

