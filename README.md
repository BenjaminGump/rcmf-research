# RCMF

This repository implements a single-bank Reversible Compiled Memory Field
(RCMF) around the existing AppWorld agent and Qwen3-8B example code.

For the current ChatGPT + Codex + GitHub + Lambda research loop, start with
[research/CHATGPT_ENTRYPOINT.md](research/CHATGPT_ENTRYPOINT.md).

The new implementation lives under `rcmf/`. The original root-level
`agent.py`, `main.py`, `model.py`, `prompt.py`, and `test_qwen3_8b.py` are
kept as legacy references.

## Milestone Coverage

- M0: config system, model backend interfaces, Qwen backend, mock backend,
  minimal AppWorld ReAct agent shell.
- M1: FP32 memory algebra, add/remove/replace, safetensors snapshots, JSONL
  append-only ledger, rebuild from active records.
- M2: latent prefix, logit-bias and no-memory injectors.
- M3 starter: AppWorld adapter with official task-id loading and ground-truth
  field probing.
- M4 starter: differentiable training step, episodic sampler, losses,
  checkpoint save/load.
- M5 starter: unified evaluate/baseline/ablation command surfaces.

The training scripts are written for Linux servers with multi-GPU launchers
such as `torchrun`, Accelerate, FSDP, or DeepSpeed. Windows-specific APIs are
not used in the new package.

## Start Here On Lambda Cloud

If you are starting from a blank Lambda Cloud instance, follow the Chinese
quickstart first:

[docs/LAMBDA_CLOUD_QUICKSTART.md](docs/LAMBDA_CLOUD_QUICKSTART.md)

It covers Conda, PyTorch/CUDA, Rust, AppWorld, Hugging Face cache, environment
checks, RCMF smoke tests, and AppWorld smoke data preparation.

For the current AppWorld supervised training run, use the official downloaded
trajectory workflow instead of single-trace examples or compiled-solution
replay:

[docs/AppWorld_官方Trajectory训练说明.md](docs/AppWorld_官方Trajectory训练说明.md)

## Quick Smoke

```bash
pytest -q
python scripts/compile_memory.py --config configs/base.yaml --records tests/fixtures/memory_records.jsonl --output runs/smoke/memory.safetensors --compiler hashing
python scripts/run_ablations.py --config configs/base.yaml --dry-run
```

## Main Commands

```bash
python scripts/prepare_appworld.py --config configs/benchmark/appworld.yaml
python scripts/train.py --config configs/base.yaml --data runs/appworld/prepared
python scripts/compile_memory.py --config configs/base.yaml --records runs/appworld/prepared/memory_records.jsonl --checkpoint runs/train/checkpoint.pt
python scripts/evaluate.py --config configs/base.yaml --benchmark appworld --memory-snapshot runs/memory.safetensors
python scripts/run_baselines.py --config configs/base.yaml --benchmark appworld --methods no_memory,bm25,full_context,fast_weight
python scripts/run_ablations.py --config configs/base.yaml
```

RCMF compiles experience into behavior control vectors. It is not a lossless
archive QA system; raw trajectories remain in the ledger for audit and rebuild.

## Server Guide

For the older generic Linux server guide, see [docs/RUN_ON_SERVER.md](docs/RUN_ON_SERVER.md).
