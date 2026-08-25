# Codex Rules for This Repository

This repository is jointly used by ChatGPT for research analysis and Codex for
implementation and experimentation.

Before ending any substantial task, Codex must:

1. Preserve existing code, Git history, runs, checkpoints, logs, and Lambda
   artifacts.
2. Commit intended source, config, workflow, or documentation changes.
3. Update `research/CURRENT_STATE.md` when the active method, result, baseline,
   or infrastructure status changes.
4. Append every completed, failed, aborted, or intentionally stopped experiment
   to `research/experiments.jsonl`.
5. Create a structured handoff in `research/handoffs/`.
6. Record exact commit, config, seed, command, metrics, and Lambda artifact
   paths.
7. Record per-task success-set changes against the locked baseline whenever an
   AppWorld evaluation is available.
8. Separate VERIFIED facts, INFERENCES, and UNVERIFIED claims.
9. Record implementation deviations and workarounds in
   `research/DECISIONS.md`.
10. Never silently simplify, truncate, downsample, or replace a research
    mechanism. When context filtering is needed, report offending examples and
    ask the user before filtering.
11. Push the completed commit to the configured GitHub remote once GitHub
    authentication and repository visibility are confirmed.
12. Never commit secrets, model caches, datasets, checkpoints, large logs, or
    Lambda-only artifacts.

A prose-only chat summary is not a valid handoff. Codex chat history is not a
source of truth.

## Production Memory Method Contract

1. Production memory write must not scan the existing memory bank.
2. Production whole-bank read complexity must not depend on memory count.
3. Benchmark-specific logic must be confined to prompt/rendering and a compact adapter or optional auxiliary supervision.
4. Raw human-readable memories remain the authoritative ledger.
5. Deployment-time addition of a new memory must be feed-forward compilation, not Qwen backward optimization.

## RCMF Joint Full-Bank Charter v1 (EXP-031A)

### Problem Definition

RCMF tests whether an editable ledger of complete memories can be compiled by
reversible per-memory addition into one fixed-size field whose
state-conditioned read causally improves a frozen model. Every scientific
forward must traverse the complete path: raw ledger, feed-forward memory
writer, reversible whole-bank field, fixed-size state-conditioned read,
standard reader, and frozen-model generation.

### Intended Contribution And Generality

The contribution under test is the reusable writer-field-read algebra, not the
cross-attention mechanism. One new memory must be feed-forward compiled and
added without scanning or retraining on the existing bank. Deployment
whole-bank read complexity and output shape must not depend on memory count.
Benchmark-specific logic is confined to a compact adapter or renderer; the
writer-field-reader algebra must remain reusable across benchmarks.

### Non-Goals

This milestone does not train Qwen or a selector, introduce retrieval, optimize
multiple seeds, run architecture sweeps, or claim the reader mechanism as an
RCMF contribution. It does not use the EXP-030A selected-single-memory proxy as
scientific evidence or as a prerequisite gate.

### Method Invariants And Prohibited Shortcuts

- Raw complete transitions remain the authoritative memory ledger.
- A memory contains the source goal, complete pre-action state, complete
  action, and complete post-action observation.
- Query prompts contain no raw memory text.
- Scientific conditioning uses only a fixed-size read from the complete legal
  bank; selected-single-memory access, top-k, FAISS, nearest-neighbor lookup,
  per-memory runtime scoring, and hard memory-use gates are prohibited.
- Production add/remove/read operations cannot enumerate unrelated records.
- Memory sections cannot be token-subsampled or arbitrarily truncated. Every
  semantic chunk must be encoded and aggregated.
- Qwen and the frozen addressing selector remain unchanged.
- EXP-031A uses exactly `GLOBAL_SEED = 25101`; no seed sweep, ensemble, or
  repeat-seed confirmation is permitted before the deadline.

### Evaluation And Compute Contract

Training uses the fixed 29/8 clean task split and full task-legal fields from
the first scientific forward. Both locked epochs are evaluated; checkpoint
selection uses only the eight heldout train tasks. The exposed first37 run is a
development diagnostic, not final statistical evidence. Before formal GPU
work, a measured complete-path full-bank smoke and best/expected/conservative
runtime estimate are required. Automatic launch is allowed only when expected
and conservative H100 wall time are both plausibly at most 18 hours. No bank,
state, control, epoch, slot, or audit reduction may be used to evade review.

### Sources Of Truth

- Approved design: `configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml`
- Reusable algebra: `rcmf/training/rcmf_joint_full_bank_9a.py`
- Preparation and validation: `scripts/prepare_rcmf_joint_full_bank_9a.py`
- Scientific runner: `scripts/run_rcmf_joint_full_bank_9a.py`
- Git-safe audit index: `research/audits/rcmf_joint_full_bank_9a_20260826_001/index.json`
- Lambda artifact root recorded by the run manifest and structured handoff.

### Detailed Generation And Interaction Audit Contract

For every one-step or full-agent generation run, save an auditable per-task,
per-step trace before declaring the run complete. Each step must include, or
losslessly reconstruct, all of the following:

- exact model message array, including every role and content field;
- prompt profile, renderer version, and content-addressed static prompt/demo
  asset references;
- current task message and complete trajectory-so-far;
- rendered-message SHA256, prompt token count, and context/truncation decision;
- model, checkpoint, and tokenizer identity;
- seed, temperature, top-p, and maximum generated tokens;
- exact emitted model response, extracted code, automatically repaired code,
  and exact executed code;
- execution exception, complete environment observation, and task-completed
  status;
- memory-field and query hashes, exact field-slot artifact reference, offline
  top-memory-contribution diagnostics, and add/remove/field provenance.

Never request or synthesize hidden chain-of-thought. Preserve only the model's
actual emitted response.

On Lambda, keep atomic unredacted raw logs and exact tensors. On GitHub, commit
Git-safe reconstructible traces: content-address static prompt assets; retain
the task message, trajectory, actual outputs, and observations; redact only
credentials, JWTs, and secrets with typed placeholders while retaining raw
SHA256 values. Commit compact lossless field tensors when practical; otherwise
record their exact Lambda path, SHA256, dtype, shape, and decoder/export tool.
Materialize the full prompt and field diagnostics for every first-divergence
and terminal step.

Every run must create `research/audits/<run_uuid>/index.json` plus per-task
audit files. A behavioral result without a committed audit index is not
independently verified and cannot be reported as confirmed.

## EXP-025D-Direct Single-Seed Deadline Policy

For EXP-025D-Direct, use exactly `GLOBAL_SEED = 25101` for deterministic
manifests, parameter initialization, data ordering, training, and diagnostic
bootstrap. Do not run multiple training seeds, repeated optimizer seeds, seed
sweeps, or ensemble training unless the user explicitly changes this policy.

## Environment Notes

- For AppWorld, agentic workflows, or Lambda experiment tooling, prefer the
  `appworld_env` Conda environment locally when it exists.
- On Lambda, the active project path is
  `/lambda/nfs/rcmf-persist/project`.
- On Lambda, the currently verified virtual environment is
  `/home/ubuntu/venvs/rcmf-py311`.
- Long-running Lambda training and evaluation should write explicit logs under
  `/lambda/nfs/rcmf-persist/runs/logs/` and should be monitored before source
  changes are synchronized.
