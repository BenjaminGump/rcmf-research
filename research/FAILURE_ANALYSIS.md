# Failure Analysis

## Data Issue: `2a163ab_3`

The official AppWorld successful train trajectory `2a163ab_3` contains repeated
large social-feed dumps in the raw trace. When converted into per-step
trajectory training examples, it produced 66 examples over Qwen3-8B's effective
40,960-token context limit and a worst sample over two million tokens.

Resolution:

- The raw official file was not modified.
- The prepared training dataset excludes task `2a163ab_3`.
- The filtered prepared dataset has 638 decision examples and 46 memory records.
- The post-filter maximum sample length is 35,615 tokens.

## Training OOM: Full-Vocab Loss

Earlier training passed labels directly into `AutoModelForCausalLM`, causing
full vocabulary logits to be allocated for prompt positions even though prompt
labels were `-100`. Long contexts made this exceed H100 80GB memory.

Resolution:

- `HFQwenBackend.forward_train()` computes loss only for shifted target
  positions where labels are not `-100`.

## Training OOM: Frozen Backbone Activations

Even with target-only logits, gradients must pass through the frozen Qwen
forward to the memory/prefix delta. Long-context activations can exceed memory.

Resolution:

- Enable gradient checkpointing for Qwen during training forwards.
- This avoids truncation by trading compute for memory.

## Generation Failure: Prepend Prefix

Prepending virtual prefix embeddings changed Qwen's effective prompt length and
positions. Even a zero-memory control produced repetitive non-code outputs and
AppWorld returned `No code available to execute`.

Resolution:

- AppWorld full-prompt configs now use `additive_token` instead of prepending
  virtual tokens.
- The deprecated `additive_prefix` alias maps to the same first-k additive-token
  behavior for old checkpoints/configs.
- Memory scale 0.0 reproduces the bare first-10 baseline, supporting pipeline
  equivalence.

## Current Research Failure: First-10 Gain Does Not Generalize

The semantic-retrieval final checkpoint reaches `4/10` on the fixed first-10
slice, but its partial full evaluation was stopped at `7/37 = 18.9%`.

Paired first-37 comparison against the locked bare-Qwen run:

- Baseline on the same 37 tasks: `10/37`.
- RCMF semantic-retrieval candidate: `7/37`.
- Retained baseline successes: `5`.
- Lost baseline successes: `5`.
- Gained over baseline: `2`.
- Result file:
  `research/results/rcmf_semretr_vs_qwen_first37_paired_20260804.md`.

Working hypothesis:

- The learned memory read is still too global or state-insensitive.
- The first-10 improvement may come from a useful perturbation for nearby tasks
  rather than a robust memory mechanism.

Needed diagnosis:

- Trace-level comparison for retained, gained, and lost tasks.
- Memory read/address diagnostics on failed broader-slice tasks.
- Checkpoint and memory-scale sweeps before new architecture changes.

2026-08-04 diagnostic evidence:

- Diagnostic artifact:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/next_iteration_20260804/memory_injection_diagnostics_semretr_legacy_statecache.json`.
- State rows inspected: `638`.
- State representation pairwise cosine mean: `0.882234`.
- Address top1 max load fraction: `0.448276` over `4` unique top1 slots.
- Memory read `memory_z` pairwise cosine mean: `0.999994`.
- Memory read `memory_z` mean direction norm: `0.999997`.

Interpretation:

- The legacy semantic-retrieval checkpoint's memory read is effectively
  state-insensitive after reading from the compiled bank. This supports
  prioritizing representation/teacher/address fixes before another full run.
