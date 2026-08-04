# RCMF Repository Map

This map is for ChatGPT and Codex handoff. It favors operational truth over
historical design intent.

## Root-Level Legacy References

- `agent.py`, `main.py`, `model.py`, `prompt.py`, `test_qwen3_8b.py`: original
  local Qwen/AppWorld reference code kept for comparison and debugging.
- `RCMF_Codex_Implementation_Plan.md`: original implementation plan. Treat it
  as historical guidance, not current implementation truth.

## Core Package

- `rcmf/config.py`: dataclass config parsing and validation.
- `rcmf/factory.py`: builds backends, trainers, memory components, and
  benchmark adapters from config.
- `rcmf/model/backends/hf_qwen.py`: Hugging Face Qwen backend, target-only
  loss, frozen-backbone representation extraction, and generation hook.
- `rcmf/model/backends/mock.py`: small deterministic backend for tests.
- `rcmf/memory/compiler.py`: compiles support memory representations into
  memory bank tensors.
- `rcmf/memory/ledger.py`: JSONL append-only memory ledger and safetensors
  snapshots.
- `rcmf/memory/state.py`: memory addressing and read path.
- `rcmf/injection/prefix.py`: prepend prefix and additive-prefix injectors.
- `rcmf/training/datasets.py`: decision-example loading, prompt construction,
  EOS target suffixing, target-only labels, and no-truncation batching.
- `rcmf/training/trainer.py`: RCMF training step, supervised loss, optional
  semantic-retrieval auxiliary loss, and optimizer groups.
- `rcmf/benchmarks/appworld/agent.py`: AppWorld ReAct agent wrapper for bare
  Qwen and RCMF memory-enabled models.
- `rcmf/benchmarks/appworld/traces.py`: official trace conversion into
  per-step decision examples and memory records.

## Configs

- `configs/baseline/appworld_qwen_full_prompt_context40.yaml`: corrected bare
  Qwen AppWorld baseline contract.
- `configs/benchmark/appworld_rcmf_full_prompt.yaml`: full-prompt RCMF AppWorld
  config with additive-prefix injection.
- `configs/benchmark/appworld_rcmf_full_prompt_low_injector.yaml`: lower
  injector disturbance config.
- `configs/benchmark/appworld_rcmf_full_prompt_semantic_retrieval.yaml`: current
  best first-10 RCMF config with semantic-retrieval auxiliary loss.

## Scripts

- `scripts/check_training_query_lengths.py`: mandatory preflight before
  training on any new AppWorld prepared dataset.
- `scripts/filter_prepared_dataset.py`: audited dataset filtering by
  task/episode. Use only after user approval.
- `scripts/train.py`: main supervised RCMF training entrypoint.
- `scripts/compile_memory.py`: builds memory snapshots from records and
  optional cached Qwen representations.
- `scripts/evaluate.py`: AppWorld evaluation entrypoint.
- `scripts/summarize_eval_results.py`: aggregate AppWorld result summaries.
- `scripts/inspect_eval_trace.py`: trace-level debugging of model input,
  output, and AppWorld observation.
- `scripts/inspect_memory_injection_stats.py`: memory/read/injection norm
  diagnostics.
- `scripts/lambda_train_eval_filtered_rcmf.sh`: Lambda full-size filtered
  train/eval runner.
- `scripts/lambda_eval_checkpoint_test10.sh`: Lambda checkpoint first-10
  evaluation runner.

## Research Workflow

- `research/CHATGPT_ENTRYPOINT.md`: what ChatGPT should read first.
- `research/CURRENT_STATE.md`: current verified project state.
- `research/EVALUATION_CONTRACT.md`: locked baseline and fair-comparison rules.
- `research/DECISIONS.md`: implementation deviations and workarounds.
- `research/FAILURE_ANALYSIS.md`: known negative results and failure modes.
- `research/NEXT_EXPERIMENTS.md`: proposed next experiments for Codex.
- `research/experiments.jsonl`: machine-readable experiment ledger.
- `research/results/`: concise GitHub-safe result summaries.
- `research/handoffs/`: structured Codex-to-ChatGPT handoffs.
- `tools/research_ops/`: local utilities for audit, run manifests, finalization,
  snapshots, backfill, and validation.
