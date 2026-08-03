from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from rcmf.benchmarks.appworld.adapter import AppWorldAdapter
from rcmf.benchmarks.appworld.agent import RCMFAppWorldAgent
from rcmf.config import load_config
from rcmf.eval.runner import EvaluationRunner
from rcmf.factory import build_backend, build_trainer
from rcmf.memory.state import MemoryState


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RCMF or no-memory policy.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--benchmark", default="appworld")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--memory-snapshot", default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--task-id", action="append", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--memory-scale", type=float, default=1.0)
    parser.add_argument("--no-memory", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.max_steps is not None:
        cfg.benchmark.max_steps = args.max_steps
    if args.benchmark != "appworld":
        raise NotImplementedError("Only AppWorld is wired for executable evaluation in this MVP")
    backend = build_backend(cfg, load_model=True)
    memory_state = None
    state_encoder = None
    injector = None
    if args.memory_snapshot and not args.no_memory:
        memory_state = MemoryState.load(args.memory_snapshot)
    if args.checkpoint and not args.no_memory:
        trainer = build_trainer(cfg, backend)
        trainer.load_checkpoint(args.checkpoint, map_location=backend.device)
        trainer.to(backend.device).eval()
        state_encoder = trainer.state_encoder
        injector = trainer.injector
    adapter = AppWorldAdapter(cfg)
    if args.task_id:
        task_ids = list(args.task_id)
    else:
        task_ids = adapter.load_splits(cfg)[args.split]
        if args.start_index:
            task_ids = task_ids[args.start_index :]
        if args.limit is not None:
            task_ids = task_ids[: args.limit]
    policy = RCMFAppWorldAgent(
        cfg,
        backend=backend,
        memory_state=memory_state,
        state_encoder=state_encoder,
        injector=injector,
        experiment_name=args.experiment_name,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        memory_scale=args.memory_scale,
    )
    output_dir = Path(args.output_dir or cfg.experiment.output_dir) / "evaluate" / args.split
    summary = EvaluationRunner(output_dir).run(adapter, policy, task_ids)
    print(summary)


if __name__ == "__main__":
    main()
