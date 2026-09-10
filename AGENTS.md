# RCMF Repository Instructions

This repository is jointly used by ChatGPT and Codex for implementation,
experimentation, and review. Read this file first, then read the relevant
`tasks/<dataset>/STATE.md`. Load historical material only when the current task
requires it.

## Authority Order

When records disagree, use this order:

1. Sealed primary artifacts and source code at the specified commit.
2. The canonical version manifest and `docs/PIPELINE.md`.
3. The relevant `tasks/<dataset>/STATE.md`.
4. `docs/HISTORY.md` and `docs/FAILURE_MODES.md`.
5. Conversation summaries or remembered context.

Independently verify the latest pushed GitHub state before acting on any
bootstrap document or chat summary. A prose chat is never the research source
of truth.

## Problem And Contribution

RCMF tests whether an editable ledger of complete memories can be compiled by
reversible per-memory addition into one fixed-dimensional whole-bank field,
then read through a fixed-size state-conditioned interface that causally
improves a frozen model.

The contribution under test is the reusable feed-forward writer, reversible
field algebra, fixed-size whole-bank state/read, and frozen-policy reader
framework. Cross-attention itself is not claimed as an RCMF contribution.
Benchmark-specific work belongs in an adapter, prompt renderer, environment
bridge, evaluator, and versioned dataset profile. The writer/field/reader
mathematics and orchestration contracts must remain reusable across datasets.

## Core Invariants

### Authoritative ledger

- Raw complete human-readable memories remain the authoritative ledger.
- Each memory records the complete goal, pre-action state, opaque complete
  action, post-action observation, source provenance, and lineage keys.
- Derived tensors never replace the ledger as the auditable source.

### Feed-forward write and edit

- After writer/reader training, one new memory is compiled independently by a
  feed-forward writer.
- Adding a memory cannot retrain Qwen, the selector, writer, reader, or field.
- Adding or removing one memory cannot scan or recompile unrelated memories.
- Every memory contribution is independently recorded and reversibly
  addable/removable within the declared numerical tolerance.

### Fixed-size whole-bank read

- The complete bank is represented by fixed-shape field state.
- Read output shape and core read complexity are independent of memory count.
- Runtime top-k, nearest-neighbor search, FAISS, selected-memory access,
  per-memory scoring, raw-memory prompt text, and learned/hard memory-use gates
  are prohibited in the production path.

### Frozen deployment

- Query prompts contain no raw memory text.
- Qwen remains frozen.
- Selector/addressing parameters are frozen after their separately approved
  training.
- Deployment-time memory addition is feed-forward compilation only.

## Portable Canonical V2

Portable canonical v2 is the engineering base for new dataset adaptations. Its
authoritative interface is
`rcmf.pipeline.portable_v2.adapter.ReproducibleBenchmarkAdapterV2`.
New adapters must register explicitly and pass capability preflight; missing
capabilities fail closed. There is no implicit AppWorld fallback.

The generic pipeline has twelve semantic phases:

1. environment and data provenance;
2. successful trajectory corpus;
3. memory transition ledger;
4. state and transition representations;
5. addressing/selector supervision;
6. paired causal outcomes;
7. policy teacher and training units;
8. writer/reader training;
9. per-epoch diagnostics;
10. terminal checkpoint validation;
11. deployment field;
12. official evaluation and reporting.

Dataset facts, counts, split names, action grammars, rewards, paths, prompt
profiles, and environment behavior come from adapter-produced strict manifests
or versioned dataset profiles. They are not generic-core constants.

### Prospective checkpoint policy

Portable v2 uses `checkpoint_policy = terminal_completed_epoch`:

- `training_epochs` is configured before training;
- deployment uses the final configured epoch only;
- that checkpoint must be complete, finite, content-hash valid, and bound to
  exact run/config/data identity;
- heldout and dev metrics may be reported but cannot select a checkpoint;
- no fallback to an earlier checkpoint is permitted.

This is a prospective policy revision. It does not retroactively change the
formal EXP-037A 14n result, whose selected checkpoint remains epoch 1, or the
post-hoc R19 epoch-2 diagnostic.

## Adapter Boundary

New dataset work must be confined normally to:

- `rcmf/benchmarks/<dataset>/`;
- `configs/datasets/<dataset>.yaml`;
- `assets/prompts/<dataset>/`;
- `tasks/<dataset>/`;
- dataset-specific tests and entrypoints.

Dataset-independent code belongs under `rcmf/pipeline/portable_v2/` and shared
writer/field/reader modules. Portable core cannot directly import AppWorld,
ALFWorld, WebShop, their environment classes, or their prompt modules. Dispatch
is through exact adapter registration and capability resolution, never a
benchmark-name conditional.

Every adapter must declare benchmark/environment/data/prompt versions,
capabilities, determinism, action semantics, reward semantics, stable task and
episode IDs, lineage/leakage keys, trajectory provenance, reset-and-replay
behavior, prompt rendering/token counting, causal comparison, official
evaluation, and audit redaction.

Allowed trajectory provenance classes are distinct and cannot be silently
merged: `OFFICIAL_EXPERT`, `OFFICIAL_HUMAN`, `OFFICIAL_HUMAN_SAMPLE`,
`OFFICIAL_MODEL_OR_IL`, `ORACLE_GENERATED_FROM_TRAIN_METADATA`, and
`AGENT_GENERATED`. `UNKNOWN_PROHIBITED` fails closed.

## Scientific And Evaluation Contract

- Use only successful, replay-validated training trajectories for the memory
  and training corpus.
- Evaluation splits cannot influence corpus construction, selector training,
  writer/reader training, checkpoint choice, prompt choice, thresholds, or
  task filtering.
- Split names and counts are adapter facts; no dataset must mimic AppWorld's
  `29/8`, `57`, `401/98/499`, or paired-state outcome counts.
- Paired causal missingness and replay failure are typed outcomes, never
  silently neutral labels.
- Prompt assets are content-addressed and pinned to upstream repository,
  commit, file/blob hash, extraction method, and license.
- Every scientific generation or complete trajectory needs reconstructible
  task/step audit records. Preserve actual emitted text; never request hidden
  chain-of-thought.
- Official metrics and reward semantics are adapter-owned. Partial reward must
  not be silently treated as full success.

Portable v2 may be described as
`ENGINEERING_VERIFIED_PORTABLE_CANONICAL_BASE` only after its conformance gates
pass. It is not `SCIENTIFICALLY_VALIDATED_ACROSS_DATASETS` until dataset-specific
scientific experiments are separately approved and completed.

## Prohibited Shortcuts

Never silently:

- simplify, truncate, subsample, replace, or downsample a research mechanism;
- use historical outcomes as construction inputs for a fresh run;
- use an evaluation outcome to choose a prompt, checkpoint, memory, task, or
  threshold;
- search arbitrary old run directories when an owned artifact is missing;
- reinterpret a failed replay as a valid trajectory;
- copy AppWorld-specific stages into an ALFWorld or WebShop pipeline;
- mutate a sealed parent artifact or hard-linked supposedly immutable input;
- mark a stage complete because a file merely exists;
- weaken source/run/config/contract/dependency/output hash validation;
- create or inherit authorization for a new long run without exact user
  approval.

## Execution, Resume, And Authorization

- Every run has a unique UUID, root, source commit, config hash, contract hash,
  and authorization scope.
- Scientific outputs are written only under that run root.
- A scheduler may skip a stage only after strict identity, dependency, and
  output-hash validation.
- Stage completion is atomic; attempts are append-only; interrupted attempts
  close explicitly.
- A checkpoint pointer and checkpoint content must both validate before
  deserialization. Same-stage intermediate resume and next-stage terminal
  checkpoint use are distinct contracts.
- Historical or parent artifacts may enter a continuation only through an
  explicit, hash-bound ownership manifest. No copied completion can impersonate
  a new-source stage.
- Missing ownership or ambiguous provenance fails early.
- Long GPU work requires a measured expected/conservative runtime, storage and
  restart plan, a proposed anomaly cap, and exact run-bound user authorization.
- Any single run plausibly exceeding 18 wall-clock hours requires explicit user
  approval. Never reduce scientific workload to evade approval.

## Isolation And Parallel Work

- Use one Git worktree and branch per dataset.
- Use one NFS run namespace per dataset and UUID.
- Use one environment/process namespace and one task `STATE.md` per dataset.
- Do not share mutable caches or output directories.
- WebShop server ports require explicit coordination.
- Do not modify portable core while another formal run uses its frozen source.
- Never reset, clean, stash, delete, overwrite, or rewrite unexplained evidence.

Suggested branches are `adapt/alfworld-v1` and `adapt/webshop-v1`; suggested
roots are `runs/alfworld/<uuid>/` and `runs/webshop/<uuid>/`.

## Repository Work Protocol

Before ending substantial work:

1. Preserve existing code, Git history, runs, checkpoints, logs, and Lambda
   artifacts.
2. Commit intended source/config/workflow/documentation changes.
3. Update `research/CURRENT_STATE.md` when active method, evidence, or
   infrastructure status changes.
4. Append every completed, failed, aborted, or intentionally stopped activity
   to `research/experiments.jsonl`.
5. Create a structured handoff under `research/handoffs/`.
6. Record exact source, config, seed, commands, metrics, and Lambda paths.
7. Separate VERIFIED, INFERENCE, and UNVERIFIED claims.
8. Record implementation deviations in `research/DECISIONS.md`.
9. Secret-scan Git-safe outputs; never commit credentials, datasets, model
   caches, checkpoints, large logs, or raw protected observations.
10. Push final commits after confirming repository visibility and
    authentication.

For Python/AppWorld work, prefer the existing `appworld_env` Conda environment
locally. On Lambda, the project root is `/lambda/nfs/rcmf-persist/project` and
the verified Python is `/home/ubuntu/venvs/rcmf-py311/bin/python`. Run tests
with process-start `PYTHONHASHSEED=25101`.

## Documentation Routing

- Portable architecture and phase contracts: `docs/PIPELINE.md`
- Adapter methods and capabilities: `docs/ADAPTER_CONTRACT.md`
- Dataset onboarding sequence: `docs/DATASET_ONBOARDING.md`
- Prompt provenance: `docs/PROMPT_SOURCES.md`
- Current scientific claims: `docs/SCIENTIFIC_STATUS.md`
- Compact milestone index: `docs/HISTORY.md`
- Thematic prevention guide: `docs/FAILURE_MODES.md`
- Dataset readiness: `docs/datasets/`
- Active dataset state and next step: `tasks/<dataset>/STATE.md`
- Standalone dataset handoff: `tasks/<dataset>/ADAPTATION_BRIEF.md`
- ChatGPT bootstrap/cache routing: `docs/CHATGPT_ENTRYPOINT.md`
- Exact legacy charters through EXP-037A R19:
  `docs/charters/LEGACY_PROJECT_CHARTERS_THROUGH_EXP037A_R19.md`

Do not indiscriminately load all history. Start with `AGENTS.md`,
`docs/PIPELINE.md`, `docs/SCIENTIFIC_STATUS.md`, and the active task state;
follow historical links only when a present decision requires them.

## Current Status

The sealed AppWorld formal 14n result and R19 post-hoc diagnostic remain
unchanged. The formal one-demo epoch-1 result is negative; the forced epoch-2
diagnostic is mixed/inconclusive. The unusually strong matched-shuffle behavior
is deferred until after the 2026-09-25 submission.

Portable canonical v2 is an engineering base. ALFWorld and WebShop have pinned
prompt sources and adaptation plans but no scientific RCMF result. Their task
states are the only current entry points for that future work.

## WebShop End-to-End Adaptation Charter v2

This charter supersedes the narrow WebShop readiness scope for the active
`adapt/webshop-v1` work. Historical WebShop charters and records remain intact.

The primary formal WebShop evaluation is AgentBench-FC `webshop-std`, indices
`[0,200)`, exactly 200 tasks. The original canonical Test-500 is not required.
The 200 tasks may not be extended, removed, reordered, replaced, or subsampled
in response to outcomes. Their rewards cannot influence prompt selection,
memory construction, selector design, checkpoint choice, thresholds, or any
other method decision.

The adaptation may change the benchmark adapter, environment wrapper,
container/runtime construction, selector supervision, representation plumbing,
training pipeline, Portable-V2 interfaces, generic RCMF core abstractions, and
writer/reader implementation when scientifically justified, benchmark-neutral
where generic, tested, and recorded.

Every final implementation must still preserve all fundamental RCMF
invariants:

- the authoritative ledger contains complete auditable goal, pre-action state,
  opaque action, post-action observation, provenance, and lineage records;
- each memory is independently compiled by a feed-forward writer after
  training, without scanning or recompiling unrelated memories;
- independently recorded additive contributions support add, remove, and
  restore without retraining;
- the deployed whole-bank state has fixed shape independent of memory count;
- production core read is memory-count independent and performs no top-k,
  nearest-neighbor search, FAISS, per-memory scoring, bank iteration, or raw
  selected-memory retrieval;
- deployment prompts contain no raw memory text;
- after freeze, Qwen, addressing, writer, and reader remain frozen, and new
  memories use feed-forward compilation only.

All environment-dependent work, generation, training, and scientific
evaluation runs on Lambda Ubuntu. Infrastructure and dependency rot may be
repaired reproducibly without claiming byte-identical historical execution.
Any single scientific run or experiment batch plausibly exceeding 18
wall-clock hours requires a committed, pushed preflight packet and explicit
run-bound user approval before launch.

## Portable Canonical V2.1 Hardening Charter

V2.1 preserves the V2.0 writer, reversible field, fixed-size read, reader,
scientific data semantics, and evaluation ownership. It hardens execution only:

- records require the exact schema and complete task/trajectory/transition/state closure;
- capabilities are phase-scoped and must pass bounded executable probes;
- config binds a hashed dataset profile, adapter, executor, prompt asset,
  run-root template, and every safety field without fallback;
- every real phase manifest binds source, run, config, dataset, adapter,
  dependencies, inputs, and outputs;
- checkpoint unit counts come from a strict upstream unit manifest;
- release gates identify machine, test, manual, or not-evaluated evidence.

The bounded AppWorld three-demo pilot is engineering integration evidence only:
at most 20 epoch-1 training units and one deterministically selected dev task,
with no accuracy gate. It cannot replace formal 14n/R19 evidence. No ALFWorld
or WebShop scientific run starts under this charter.
