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
