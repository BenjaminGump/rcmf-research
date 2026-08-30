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

## EXP-031B Benefit-Preserving Calibration Charter v1

EXP-031B freezes the complete verified EXP-031A system and tests only bounded,
predeclared calibration and read-algebra diagnostics. Its purpose is to retain
the complete whole-bank field's demonstrated positive behaviors while reducing
harmful interventions. It is not an architecture search and does not redefine
RCMF success.

- Qwen, tokenizer, writer, reader, selector, checkpoint, all 499 memories,
  splits, prompt, renderer, evaluator, generation settings, and seed `25101`
  remain frozen.
- The default 9b route must reproduce the 9a correct-field system exactly.
- Production candidates retain feed-forward independent memory compilation,
  reversible constant-time add/remove, fixed-size whole-bank state, and a read
  whose shape and complexity are independent of memory count.
- Runtime retrieval, top-k, FAISS, selected-memory access, per-memory scoring,
  raw-memory prompt text, hard or learned memory-use gates, retraining, and
  optimizer updates are prohibited.
- Offline per-memory contribution analysis is audit-only and cannot influence
  runtime generation or post-hoc candidate selection.
- Candidate formulas and values are locked before first37 outcomes. At most
  four candidates may reach heldout live evaluation and at most two complete
  first37 correct/shuffled pairs may run sequentially.
- Benefit preservation is mandatory: an eligible candidate must retain at
  least five of six original gains with all three gain families represented,
  plus both original retained successes.
- Scientific decisions are `PROCEED`, `STOP ROUTE`, or `INCONCLUSIVE`; a route
  is not forced to win.
- Before each scientific GPU stage, record exact identities, measured pilot
  runtime, expected and conservative runtime, H100 cost, and restart plan. Any
  single batch that may exceed 18 wall-clock hours requires explicit approval.

Sources of truth:

- `configs/benchmark/stage_c_rcmf_benefit_preserving_calibration_9b.yaml`
- `rcmf/training/rcmf_benefit_preserving_calibration_9b.py`
- `scripts/prepare_rcmf_benefit_preserving_calibration_9b.py`
- `scripts/run_rcmf_benefit_preserving_calibration_9b.py`

## EXP-031C Q90 Full-Trajectory Charter v1

- Q90 is the only scientific candidate allowed in EXP-031C. Its formula,
  calibration identity, and exact `tau = 4.606291029188367` are immutable.
- Complete AppWorld agent trajectories replace one-step proxies as the primary
  decision evidence: heldout-train trajectories run first, followed by the
  exposed first37 development comparison only when the heldout gate permits it.
- Retraining, optimizer updates, new calibration, candidate search, retrieval,
  runtime per-memory scoring, gates, and raw-memory query text are prohibited.
- The first37 tasks remain exposed development data and cannot support a final
  statistical generalization claim.
- Detailed reconstructible per-step audit logs remain mandatory for every
  heldout and first37 task/condition; a result without a committed Git-safe
  audit index is not independently verified.

## EXP-032A On-Policy Trajectory Union Distillation Charter v1

- Deployment preserves the EXP-031A architecture: feed-forward reversible
  memory writes, one fixed-size whole-bank field, and a read whose shape and
  complexity are independent of memory count.
- Runtime retrieval, top-k, per-memory scoring, raw-memory prompt text, and
  hard or learned memory-use gates are prohibited.
- All rollout comparison, trajectory selection, and distillation are offline;
  no test-time online training or optimizer update is permitted.
- The exposed first37 outcomes cannot modify the model, checkpoint selection,
  losses, data, or thresholds. Model selection uses only the 29 training tasks
  and the immutable eight heldout train tasks.
- EXP-032A uses exactly one training seed, `25101`.
- Detailed reconstructible per-step Git-safe logs are mandatory for every
  training rollout, heldout trajectory, and conditional first37 condition.

## EXP-033A Frozen EXP-031A One-Demo Dev Evaluation Charter v1

- EXP-033A is evaluation-only. The EXP-031A Qwen, tokenizer, selector,
  writers, readers, epoch-2 checkpoint, 499-memory field, field algebra, and
  key-payload shuffle are immutable; no optimizer step, retraining, parameter
  update, calibration, or checkpoint selection is permitted.
- The complete official AppWorld 0.1.0 `dev` split is the only scientific
  evaluation split. `train` is allowed only for engineering smoke, while
  `test_normal`, `test_challenge`, first37, and three-demo dev conditions are
  prohibited.
- The one-demo prompt is frozen before dev generation and retains exactly the
  original system/instruction content plus the original first complete demo.
  Dev outcomes cannot modify the prompt, model, field, checkpoint, condition
  manifest, or generation settings.
- Only matched conditions D0 bare/zero, D1 correct 499-memory whole-bank
  field, and D2 immutable key-payload-shuffled 499-memory field are allowed.
- Runtime retrieval, top-k, per-memory scoring, raw-memory prompt text, gates,
  and task-specific rules remain prohibited. Production write and whole-bank
  read complexity invariants remain unchanged.
- Detailed reconstructible per-step logs and a committed Git-safe audit index
  are mandatory. No training or follow-on experiment starts automatically.

## EXP-034A One-Demo-Consistent EXP-031A Retraining Charter v1

- EXP-034A preserves the EXP-031A problem definition, contribution, production
  memory complexity contract, architecture, fixed 29/8 task split, and single
  training seed `25101`.
- The only scientific change is replacing the original three complete demos
  with the original first complete demo on every prompt-dependent training,
  supervision, heldout-validation, and deployment-evaluation path.
- Prompt-independent raw-ledger, memory, transition-representation, split,
  provenance, parent-weighting, and shuffle-rule artifacts are reused only
  after exact hash and semantic validation. Every prompt-dependent state,
  query, selector choice, paired outcome, label, policy teacher, training unit,
  and heldout row is rebuilt under `full_demo_first_only`.
- Qwen and the selector remain frozen. Writers and readers are initialized and
  trained with the exact two-epoch EXP-031A recipe; architecture, dimensions,
  losses, learning rates, weight decay, checkpoint selection, field algebra,
  and runtime mechanisms cannot change.
- Official AppWorld dev is evaluation-only and cannot influence checkpoint
  selection, training, manifests, or configuration. `first37`, `test_normal`,
  and `test_challenge` are prohibited.
- Runtime retrieval, top-k, per-memory scoring, raw-memory deployment prompt
  text, gates, and bank-size-dependent production reads remain prohibited.
- No additional optimization, prompt variant, retraining round, or follow-on
  experiment starts automatically after EXP-034A.

## EXP-034B Fresh One-Demo Selector Retraining Charter v1

- The RCMF problem definition and production memory complexity contract are
  unchanged. The official 499-memory ledger, complete-transition semantics,
  transition representation semantics, parent-normalized weighting, fixed-size
  field algebra, and deployment read contract remain unchanged.
- The prompt remains the exact EXP-033A/EXP-034A `full_demo_first_only`
  profile, and Qwen remains frozen. The selector architecture and locked
  historical training method are unchanged; only selector parameters are
  freshly initialized and trained from scratch on one-demo state
  representations.
- No selector architecture, objective, hyperparameter, candidate, seed, or
  calibration search is permitted. The fixed deployed three-member method is
  reproduced deterministically from global seed `25101` and member index.
- Every downstream artifact that depends on selector outputs must be rebuilt.
  Writers and readers are then initialized and trained from scratch using the
  unchanged EXP-031A/EXP-034A two-epoch recipe while Qwen and the new selector
  remain frozen.
- Official AppWorld dev is evaluation-only. D0 may be reused only after exact
  identity verification. Dev outcomes cannot modify the selector, downstream
  supervision, writer/reader checkpoint, field, or configuration.
- First37, `test_normal`, `test_challenge`, retrieval, top-k, per-memory runtime
  scoring, gates, raw-memory deployment prompt text, and automatic follow-on
  optimization are prohibited.

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
