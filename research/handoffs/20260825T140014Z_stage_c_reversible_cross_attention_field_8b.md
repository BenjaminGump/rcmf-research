# Structured Handoff: EXP-030A

## Scope

Milestone 8B implemented and trained one published-style dedicated memory
cross-attention reader, then applied its preregistered heldout-train policy
gate. Qwen, the selector, prompt, clean corpus, and single seed remained
frozen. The gate failed before heldout live generation and before reversible
whole-bank field construction.

## Verified

- Branch: `research/v4-cross-attention-field`.
- Starting SHA: `02b71fa490e2d0485453cee0739598b990ba4174`.
- Source/finalizer SHA: `4534c2673f2d0878b76f4ba0650b0b5d030fff25`.
- Archive: `archive/v4-fixed-memory-reader-failed` at the starting SHA.
- Run UUID: `reversible_cross_attention_field_8b_20260825_001`.
- Seed: `25101` only.
- Memory cache: 499 records, 36 layers, 16 slots, hidden size 4096,
  2,356,661,732 bytes.
- Reader: rank 16, 4,718,592 trainable parameters; original Qwen trainable
  and gradient counts are both zero.
- Zero/no-memory equivalence, decode access, separate memory KV, gradient,
  memory-specific logit, and exact save/load/resume checks pass.
- Phase 1: 401 train samples, 98 heldout samples, three epochs; epoch 1 selected
  at heldout CE `0.597649`.
- Phase 2: 366 train states, 576 units/epoch, four epochs, 2,304 backwards.
- Positive heldout policy gate at the best epoch: X0/X1/X2/X3 KL is
  `0.583907/0.839403/0.976347/1.770020`.
- All four checkpoints fail `X1 < X0`; eligible checkpoint count is zero.
- Heldout live conditions: 0. Whole-bank field conditions: 0. First37: 0.
- No test-normal outcome was used. No V4 tag was created or moved.
- Attempts: 17 closed, 10 successful, seven failed. Ledger SHA256:
  `769222cdd6c32e35ee1189fbc8fc6e346dab2516b91ca4479be74b3ac751d42c`.
- Active H100-process time: `5.544890 h`; wall span: `11.069678 h`.

## Inference

- The selected-single-memory cross-attention interface is technically valid
  and distinguishes correct from shuffled memories, but the bounded reader
  moves positive-state policy farther from the raw-memory teacher than the
  no-memory condition.
- Since the required policy gate is a prerequisite to checkpoint selection,
  no live result could authorize this reader or the reversible field.

## Unverified

- Heldout live single-memory behavior, reversible-field one-step behavior,
  and first37 task success were not run and must not be inferred.
- Broader training, another reader, or multiple seeds might differ, but these
  are outside the deadline scope and not authorized.

## Decision

Reached `published_cross_attention_reader_failed_on_appworld`.

Stop before the reversible field. Do not build a full bank, run another
reader/carrier/compiler, train Qwen, start Stage C2, run end-to-end RCMF, or
create/move a V4 tag.

## Attempts And Recovery

The seven failed attempts are preserved append-only. They cover two early
activation-checkpoint hook mismatches, one initial full-audit OOM, one bounded
hook compatibility error after Phase-1 work, one posttrain audit OOM, one
Phase-2 checkpoint recompute mismatch, and one immutable policy-row schema
adapter error. All corrections were implementation-only and regression-tested;
no accepted scientific row or frozen parameter was changed.

## Next 48 Hours

1. Freeze EXP-030A code, checkpoints, policy summaries, and failure branch.
2. Stop neural compiled-memory architecture work for the submission.
3. Lock claims and tables around clean provenance/replay, automatic selector
   quality, oracle raw-transition behavior, free-carrier capacity, and bounded
   negative amortization results.
4. Finish limitations, reproducibility, artifact links, and paper writing.

## Artifacts

- Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/reversible_cross_attention_field_8b_20260825_001`
- Human report: `research/results/EXP_030A_REVERSIBLE_CROSS_ATTENTION_FIELD.md`
- Machine summary:
  `research/results/exp030a_reversible_cross_attention_field/summary.json`
- Related-work note: `research/EXP_030A_RELATED_WORK_AND_NOVELTY.md`
- Policy decision: `reader/phase2/policy_gate_decision.json`

## Termination

All EXP-030A attempts are closed. No EXP-030A, Qwen, or AppWorld process
remains. The H100 is idle. The Lambda instance is safe to terminate after the
final records commit is synchronized.
