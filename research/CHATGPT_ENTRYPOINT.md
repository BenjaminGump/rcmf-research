# ChatGPT Entry Point

Read in this order:

1. `research/CURRENT_STATE.md`
2. `research/ARCHITECTURE.md`
3. `REPO_MAP.md`
4. `research/EVALUATION_CONTRACT.md`
5. `research/FAILURE_ANALYSIS.md`
6. `research/NEXT_EXPERIMENTS.md`
7. The latest file in `research/handoffs/`
8. Relevant source files and configs from `REPO_MAP.md`

Do not assume:

- pipeline correctness beyond verified items;
- the final checkpoint is the best checkpoint;
- aggregate accuracy captures all important changes;
- an old design document equals the current implementation;
- Codex chat history is a durable source of truth.

Current collaboration contract:

- ChatGPT proposes mechanisms, diagnoses, and discriminating experiments.
- Codex implements, runs Lambda experiments, records artifacts, and pushes
  source and GitHub-safe summaries.
- Lambda stores large artifacts: checkpoints, datasets, full traces, caches, and
  logs.
- GitHub stores code, configs, concise results, manifests, decisions, and
  handoffs.
