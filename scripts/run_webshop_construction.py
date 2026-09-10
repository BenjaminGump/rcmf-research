from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rcmf.benchmarks.webshop.adapter import (
    WebShopPortableAdapterV2,
    load_task_catalog,
)
from rcmf.benchmarks.webshop.generation import (
    HFQwenToolGenerator,
    run_construction_block,
)
from rcmf.benchmarks.webshop.runtime_client import WebShopHTTPRuntime
from rcmf.utils.serialization import sha256_file


def _object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--runtime-identity", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--generation-config", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model-snapshot", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--index-start", type=int, required=True)
    parser.add_argument("--index-end", type=int, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--session-namespace", required=True)
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--admissible", action="store_true")
    args = parser.parse_args()
    if args.index_start < 1500 or args.index_end > 12000:
        raise ValueError("construction range must remain inside train [1500,12000)")
    if args.index_end <= args.index_start:
        raise ValueError("construction range must be non-empty")
    catalog = load_task_catalog(args.task_catalog)
    tasks = [
        task
        for task in catalog["train"]
        if args.index_start <= int(task.metadata["index"]) < args.index_end
    ]
    if len(tasks) != args.index_end - args.index_start:
        raise RuntimeError("task catalog does not contain the exact requested train range")
    runtime_identity = _object(args.runtime_identity)
    source_manifest = _object(args.source_manifest)
    source_identity = source_manifest.get("source_identity")
    if not isinstance(source_identity, Mapping) or not source_identity:
        raise ValueError("source manifest must contain a non-empty source_identity")
    config = _object(args.generation_config)
    if config.get("format") != "rcmf_agentbench_fc_webshop_construction_config_v1":
        raise ValueError("unexpected WebShop construction config format")
    config_sha256 = sha256_file(args.generation_config)
    current_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if source_identity.get("generation_config_sha256") != config_sha256:
        raise ValueError("source identity does not bind the generation config")
    if source_identity.get("source_commit") != current_commit:
        raise ValueError("source identity does not bind the checked-out source commit")
    expected_class = (
        "AGENT_GENERATED_TRAIN_CONSTRUCTION" if args.admissible else "TIMING_PILOT_NOT_ADMISSIBLE"
    )
    if source_identity.get("classification") != expected_class:
        raise ValueError("source identity classification differs from execution mode")
    if args.admissible:
        population = config["construction_population"]
        population_start = int(source_identity.get("population_start", -1))
        population_end = int(source_identity.get("population_end", -1))
        if (
            population_start != int(population["index_start"])
            or population_end != int(population["index_end"])
            or args.index_start < population_start
            or args.index_end > population_end
        ):
            raise ValueError("source identity does not bind the construction population")
        block_size = int(population["block_size"])
        if (args.index_start - population_start) % block_size != 0 or (
            args.index_end - args.index_start
        ) % block_size != 0:
            raise ValueError("construction task range is not aligned to frozen blocks")
    elif (
        int(source_identity.get("index_start", -1)) != args.index_start
        or int(source_identity.get("index_end", -1)) != args.index_end
    ):
        raise ValueError("pilot source identity does not bind the requested task range")
    generator = HFQwenToolGenerator(args.model_snapshot, dtype=args.dtype)
    adapter = WebShopPortableAdapterV2(
        task_records=catalog,
        trajectory_records={"train": ()},
        trajectory_source_identity=source_identity,
        runtime_identity=runtime_identity,
        token_counter=None,
        runtime_factory=lambda task: WebShopHTTPRuntime(
            int(task.metadata["index"]),
            endpoint=args.endpoint,
            session_namespace=args.session_namespace,
        ),
    )
    summary = run_construction_block(
        adapter=adapter,
        tasks=tasks,
        generator=generator,
        source_identity=source_identity,
        environment_identity=runtime_identity,
        output_root=args.output_root,
        max_new_tokens=args.max_new_tokens,
        admissible=args.admissible,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
