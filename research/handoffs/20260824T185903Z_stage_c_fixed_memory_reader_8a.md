# Structured Handoff: EXP-029A

## Scope

Milestone 8A collected on-policy states from clean train tasks and trained one
observation-excluded PairMLP plus fixed memory-reader adapter at frozen Qwen
layers `[7,14,21,28]`. It did not train Qwen or the selector, build a full
bank, run test-normal tasks, start Stage C2, or create/move a V4 tag.

## Verified

- Branch: `research/v4-fixed-memory-reader`.
- Starting SHA: `be93219b61ff30b50006f0801e93c14c288595d4`.
- Run UUID: `fixed_memory_reader_adapter_8a_20260824_001`.
- Seed: `25101` only.
- On-policy collection: `222` states over all 37 clean train tasks, split
  `174/48` over fixed `29/8` model-train/heldout-train tasks.
- Paired outcomes: `444/444`; labels `5/210/7`
  POSITIVE/NEUTRAL/HARMFUL; test-normal outcomes used `0`.
- Training-only augmentation added 21 immutable EXP-028A positives, reaching
  24 model-training positives while leaving validation purely on-policy.
- The reader has `2,162,688` parameters and is fixed-size with respect to
  memory count. Zero equivalence, locality, decode exclusion, gradients,
  frozen-Qwen, no-raw-memory-prompt, and mixed-precision/checkpoint tests pass.
- Training used `243` units and `972` backwards. Positive and bare groups had
  equal total weight `121.5`; maximum training ratio at u4 was `0.004515`.
- All `576/576` heldout live R1/R2/R3/R0 conditions completed.
- At u1, u2, and u4, every control has action signature `0.1875` and semantic
  successor `0.145833`. R1 execution is `0.9375/0.916667/0.9375` versus R0
  `0.9375` throughout.
- Positive R1-R0 task count is `0/8` at every checkpoint. All checkpoints are
  `CLEAR_FAILURE`; no checkpoint was selected.
- Conditional first37 was not authorized and did not run.
- Accounted active attempt time was `4.3516 h`; wall span was `7.5943 h`.
- No V4 tag was created or moved.

## Inference

- The fixed reader carrier is implemented correctly, and optimization reduces
  teacher-policy loss, but the learned correct pair does not alter heldout
  deterministic behavior relative to zero or either shuffle.
- This bounded one-seed result does not prove every reader architecture
  impossible, but it exhausts the last preregistered neural compiled-memory
  rescue on the submission critical path.

## Unverified

- Whether a different reader architecture, broader training regime, or
  multiple seeds could produce live memory-specific behavior.
- Any reader/compiler first37 or fresh-test task-success effect; no such run
  was authorized after the heldout failure.
- A deployable full-bank compiled-memory system.

## Attempts

1. `exp029a-preflight-001`: passed immutable/runtime preflight.
2. `exp029a-collect-001`: froze 222 on-policy states.
3. `exp029a-paired-001`: completed 444 paired conditions.
4. `exp029a-implementation-001`: stopped on an incorrect DecisionExample
   field access before scientific training.
5. `exp029a-implementation-002`: stopped on the FP32/BF16 reader boundary.
6. `exp029a-implementation-003`: stopped because hooks were removed before
   activation-checkpoint recomputation.
7. `exp029a-implementation-004`: passed all reader invariants.
8. `exp029a-train-001`: completed u1/u2/u4.
9. `exp029a-validate-001`: stopped before its first accepted row because the
   state ID was duplicated in the bridge condition key.
10. `exp029a-validate-002`: resumed unchanged checkpoints and completed all
    576 validation conditions.
11. `exp029a-select-001`: recorded no eligible checkpoint and the final
    decision.

## Decision

Reached `fixed_memory_reader_failed`.

Stop neural compiled-memory architecture work for the ICLR submission. Do not
run another reader/adapter, full-bank integration, a factorized field, Qwen
training, Stage C2, end-to-end RCMF, or V4 tagging.

## Next 48 Hours

1. Freeze all EXP-029A code, checkpoints, states, outcomes, and failure branch.
2. Lock the paper contribution around clean provenance, semantic replay,
   signature-balanced automatic selection, raw-transition one-step causality,
   and free deep-carrier capacity.
3. Present the generic, structured, and fixed-reader amortization sequence as
   controlled negative evidence and narrow deployment claims accordingly.
4. Move to tables, limitations, reproducibility links, and submission writing;
   do not spend the deadline window on another neural-memory architecture.

## Artifacts

- Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/fixed_memory_reader_adapter_8a_20260824_001`
- Human-readable report:
  `research/results/EXP_029A_FIXED_MEMORY_READER.md`
- GitHub-safe machine summary:
  `research/results/exp029a_fixed_memory_reader/summary.json`
- Append-only Lambda ledger SHA256:
  `889c0f422c7b3df3139b2d178c5a1eb6f9c82382904f59d448be9fbd84cc6396`

## Termination

All EXP-029A attempts are closed. No Qwen, AppWorld, or EXP-029A process
remains; the H100 reports zero allocated memory and zero utilization.
Historical idle tmux sessions are unrelated and were not modified. The Lambda
instance is safe to terminate after final Git synchronization.