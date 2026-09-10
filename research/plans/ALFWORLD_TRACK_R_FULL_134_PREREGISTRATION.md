# ALFWorld Track R Full 134-Task Preregistration

Status: `PREREGISTERED_BEFORE_ANY_VALID_UNSEEN_MODEL_OUTCOME`

Date: 2026-09-10

## Hypothesis and mechanism

Hypothesis: a fixed-size RCMF whole-bank field compiled from replay-validated
official TRAIN transitions can improve the frozen Qwen3-8B action policy on
ALFWorld `valid_unseen` relative to the same frozen bare Qwen policy.

Mechanism: every admitted TRAIN transition is compiled independently into a
reversible additive contribution. The resulting field has fixed shape. The
current goal and observation query that field; a fixed-size reader output is
added to four tokens in the last user-message span. No memory is retrieved,
scored, or placed in the prompt. Qwen remains frozen.

## Frozen benchmark endpoint

- Track: `alfworld_upstream_react_valid_unseen_reference_v1`.
- Role: `UPSTREAM_PROTOCOL_REFERENCE`.
- Population: all 134 official `valid_unseen` tasks.
- Task-set SHA-256:
  `2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534`.
- Evaluation-order SHA-256:
  `c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8`.
- Frozen Harness execution-lock identity:
  `055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
- Lock file SHA-256:
  `bc2a2ee381ad86e686eff02b4f7ba647c91ab207d5bfb939689b19af86464b5f`.
- Harness source: `4fa9274eaf55d066eb85bf828f76cea4f40c3dbb`.
- Prompt profile: `react_task_type_two_demo_v1`.
- Prompt SHA-256:
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a`.
- Model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Chat-template SHA-256:
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`.
- Generation identity:
  `6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9`.
- Generation: greedy, thinking disabled, `max_new_tokens=512`, first decoded
  line as action, no custom stopping criteria, and at most 49 environment
  actions.
- Effective context: 40,960 tokens with silent truncation forbidden.
- Scheduling: stable task-order groups of six with exact left-padding masks
  under the sealed FlashAttention-2 runtime. Active rows compact stably.
- Evaluator: official ALFWorld terminal `won=true` binary success.

The bare and RCMF arms use this exact endpoint. No task is filtered,
substituted, or removed. Track R remains distinct from Track S.

## Frozen TRAIN source and training configuration

- Main run UUID: `f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
- Admitted official-expert TRAIN trajectories: 3,545.
- Excluded typed expert timeouts: 8.
- Admitted transitions/memories: 21,259.
- Corpus SHA-256:
  `35aa8f20e0cb8571d23da9c61f2efc0e93ee7a84bd8385742fd537c78eac795d`.
- Ledger SHA-256:
  `2fe6efb1ad7b76695460202515684c6f83c15235b4430717c47f4eb86f8f213d`.
- Config SHA-256:
  `452db186566fc0844421c736e5dedb0ec9f08d63d3a5e16485c6b2c1b3bd10ea`.
- Training seed: 25,101.
- Hashed text feature dimension: 256.
- Key dimension: 256.
- Program dimension: 128.
- Writer hidden dimension: 512.
- Model dimension: 4,096.
- Injection: four additive token deltas at `last_user_k`, initial scale 0.05.
- Embedding target scale: 0.10.
- Writer epochs: one.
- Reader epochs: one.
- Training batch size: 256.
- Optimizer: AdamW; writer LR 0.001, reader LR 0.0005, weight decay
  0.0001, gradient clip norm 1.0.
- Checkpoint policy: `terminal_completed_epoch`; no metric selection or
  fallback.
- Final training uses every admitted transition and `engineering_limit=0`.

The writer is trained from complete TRAIN transition text, state alignment,
and action targets. The fixed field is compiled after writer training. The
query encoder, fixed-field reader, and injector are then trained against
frozen action-token embeddings. Qwen weights receive no gradient.

## Prospective run identities

- FA2 TRAIN sanity: `cf34300b-44dd-4601-a31f-dc3a21f51b5f`.
- Small TRAIN-only training smoke: `3ffde0e7-0d08-4db6-9ce3-8b1ad8d9c791`.
- Small TRAIN-only RCMF environment smoke:
  `0e79ad18-f055-45df-b4a1-3b697d70b7c4`.
- Full training: `c5f81480-1e70-43f1-911b-ab258e424ccd`.
- Full bare evaluation: `9b9fcadb-a03c-49b1-9a94-1a5cb2c1068a`.
- Full RCMF evaluation: `b9e9f74d-196a-4797-953c-94211ce2907b`.

Generation is greedy under a frozen identity, so duplicate evaluation seeds
would not constitute meaningful replication. The seed is still recorded for
runtime reconstruction.

## Primary endpoint and analysis

The primary endpoint is official binary success over all 134 tasks. Report
bare and RCMF successes, accuracies, paired absolute difference, gained and
lost tasks, both-correct and both-wrong counts, family-wise results, all typed
failures, per-task audit rows, a 100,000-replicate paired task bootstrap 95%
interval with seed 25,101, and an exact two-sided McNemar test.

There is no success threshold. The experiment completes whether RCMF wins,
ties, or loses. The conclusion will be positive evidence, negative evidence,
or inconclusive effect based on the paired estimate, uncertainty, and
mechanism diagnostics—not a post-hoc cutoff.

## Outcome firewall and invalidation rule

No `valid_seen` or `valid_unseen` outcome may change the corpus, task list,
prompt, model, tokenizer, generation, architecture, hyperparameters,
checkpoint, threshold, or parser. Once the first formal `valid_unseen` output
is visible, no scientific setting may be tuned.

A genuine implementation defect that invalidates benchmark execution will be
recorded as an invalid attempt. Every scientifically affected arm will be
rerun under one corrected, prospectively frozen descendant identity. Poor
task performance, repeated actions, or a zero score is not an implementation
defect.

## Compute gate

The formal arms may launch only after the completed six-task FlashAttention-2
TRAIN sanity supplies an exclusive-H100 wall-clock and peak-memory
measurement. Extrapolated expected and conservative duration for each single
scientific run must be confidently at most 18 hours. Otherwise execution
stops for explicit run-bound approval; the task count will not be reduced or
artificially split to evade the gate.

## Current evidence boundary

Before this preregistration, no formal `valid_unseen` model outcome was run or
inspected. A prior six-task TRAIN sanity completed the model/environment loop
and has no scientific standing. A later six-task FA2 attempt was interrupted
with zero completed rows because it overlapped a separately owned WebShop GPU
run; it is explicitly ineligible as sanity or scientific evidence.

