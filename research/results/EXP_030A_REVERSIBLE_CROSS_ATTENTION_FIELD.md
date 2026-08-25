# EXP-030A Reversible Cross-Attention Field

## Decision

EXP-030A reached:

`published_cross_attention_reader_failed_on_appworld`

The published-style selected-single-memory cross-attention reader failed its
mandatory heldout-train policy gate. No checkpoint made correct selected
memory closer to the raw-memory teacher than the zero-memory condition.
Heldout live generation, reversible field construction, whole-bank audit, and
first37 therefore did not run.

## Verified Scope

- Run UUID: `reversible_cross_attention_field_8b_20260825_001`.
- Seed: `25101` only.
- Branch: `research/v4-cross-attention-field`.
- Starting/archive SHA: `02b71fa490e2d0485453cee0739598b990ba4174`.
- Qwen3-8B and the EXP-025C-R selector remained frozen.
- The student query prompt contained no raw memory text.
- No test-normal outcome entered training, checkpoint evaluation, or the
  decision.
- The borrowed reader used 16 external slots at each of all 36 Qwen layers,
  rank-16 zero-initialized residual fusion, and 4,718,592 trainable parameters.

## Interface Validation

The 499-memory slot cache contains tensors shaped `[36,16,4096]` and occupies
2,356,661,732 bytes. Its index SHA256 is
`9c7f7c2b3760ef7be024a09731c3a09bd3324c91109afda40b14cbdec2e0fc1c`.

Four representative prompts at 7,319, 10,236, 35,505, and 35,608 tokens pass
zero-logit, NLL, and deterministic-generation equivalence. Generated tokens
query the external memory; slots do not enter the self-attention KV cache.
Qwen has zero trainable parameters and zero gradients. After Phase 1, all 36
fusion down/up pairs receive gradients, correct and shuffled memories produce
distinct logits, and save/load/resume is exact.

## Curriculum

Phase 1 trained on 401 model-train source transitions and evaluated on 98
states from eight disjoint heldout train tasks. Heldout utilization CE across
epochs 1-3 was `0.597649`, `1.142686`, and `0.816993`; epoch 1 was selected.
Its checkpoint SHA256 is
`9980c67a7ba81c054c41554ac3fc5b58e228f1de286b78d32ab926c86c40f71f`.

Phase 2 used 366 model-train causal states and 576 units per epoch for four
epochs, or 2,304 formal backwards. Positive/correct units targeted raw-memory
policy; neutral, harmful, transition-mismatch, and state-mismatch units
preserved bare policy.

## Policy Gate

The mandatory comparison used the 24 POSITIVE states among 98 heldout-train
states. No checkpoint passed.

| Epoch | X0 zero | X1 correct | X2 transition shuffle | X3 state shuffle | Gate |
|---:|---:|---:|---:|---:|:---:|
| 1 | 0.583907 | 0.839403 | 0.976347 | 1.770020 | fail |
| 2 | 0.583907 | 8.131224 | 9.088912 | 8.372354 | fail |
| 3 | 0.583907 | 0.938593 | 1.002282 | 1.830099 | fail |
| 4 | 0.583907 | 1.353806 | 1.256119 | 2.421437 | fail |

Epoch 1 is the best diagnostic checkpoint. It is memory-specific relative to
both shuffle controls, but its correct-memory KL is 43.76% worse than zero
memory. The preregistered gate requires all three strict inequalities, so
live behavior cannot make any checkpoint eligible.

## Runtime And Recovery

The append-only ledger contains 17 closed attempts: 10 successful and seven
failed implementation attempts. The failures preserve two activation-
checkpoint identity errors, two bounded-hook/OOM issues, one compatibility
error, one Phase-2 recompute mismatch, and one immutable policy-row schema
error. Focused tests preceded each unchanged restart; accepted scientific
parameters did not change.

Active H100-process time was `5.544890 h`; wall span was `11.069678 h`.
The artifact root occupies 2,756,202,486 bytes. The preflight had projected
`13.033797 H100 h` through Phase C, but the failed policy gate stopped the
remaining GPU work.

## Interpretation

**VERIFIED:** The external-memory pathway is implemented correctly and can
distinguish correct from shuffled memories after bounded training, but it
does not beat no memory on the required positive-state raw-policy target.

**INFERENCE:** Under the preregistered one-seed curriculum, the borrowed
cross-attention reader is not a valid foundation for the proposed reversible
whole-bank field on AppWorld.

**UNVERIFIED:** A different curriculum, reader architecture, more training,
or multiple seeds might behave differently. None is authorized for the
submission milestone. No claim is made about whole-bank field behavior
because the field gate was never reached.

## Next Action

Freeze EXP-030A and stop compiled-memory architecture work for the submission.
Use the remaining deadline window for claim locking, tables, limitations,
reproducibility, and the paper narrative around the validated clean corpus,
semantic replay, selector, raw-transition one-step effect, free carrier
capacity, and bounded amortization failures.

## Artifacts

- Lambda root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/reversible_cross_attention_field_8b_20260825_001`
- Policy decision: `reader/phase2/policy_gate_decision.json`
- Policy summary: `reader/phase2/policy_evaluation_summary.json`
- Phase-2 training: `reader/phase2/training_summary.json`
- Phase-1 selection: `reader/phase1/checkpoint_selection.json`
- Implementation validation: `reader/implementation_validation.json`
- Attempts SHA256: `769222cdd6c32e35ee1189fbc8fc6e346dab2516b91ca4479be74b3ac751d42c`
- GitHub-safe machine summary: `research/results/exp030a_reversible_cross_attention_field/summary.json`

