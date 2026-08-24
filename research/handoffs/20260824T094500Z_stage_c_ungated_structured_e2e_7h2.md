# Structured Handoff: EXP-028B

## Scope

Milestone 7H2 audited the frozen EXP-028A gate distribution and ran the
ungated correct/shuffled structured compiler on the locked first37 AppWorld
tasks. No model, selector, gate, threshold, carrier, prompt, task harness, or
dataset was trained or modified.

## Verified

- Branch: `research/v4-ungated-structured-e2e-audit`.
- Starting SHA: `6343f433ba96442d4a6e421ded072cfd85406fb8`.
- Run UUID: `ungated_structured_compiler_e2e_7h2_20260824_001`.
- Seed: `25101` only.
- Frozen gate/compiler/selector hashes match EXP-028A.
- Gate inference contract matches exactly; saved live probabilities reproduce
  to maximum error `1.1920929e-7`.
- Heldout-train activation is `11/98` at threshold `0.60`; first37 activation
  is `0/871` available feature rows, with two explicit parent-run missing rows.
- Distribution diagnosis is `broad_feature_state_distribution_shift`:
  classifier AUC `0.997624`, `64/186` features have absolute SMD at least
  `0.5`, and maximum absolute SMD is `4.838915`.
- U0 matched bare: `8/37`.
- U1 correct ungated compiler: `0/37`, 1,718 steps, zero execution exceptions,
  four locked context-overflow terminal failures.
- U2 transition-shuffled compiler: `2/37`, successes `0d01c76_2` and
  `325d6ec_1`, 1,156 steps, zero execution exceptions.
- U1-U0 is `-8`; U1-U2 is `-2`.
- The U2 map covers all 499 transitions, has no fixed transition IDs, and
  changes signature class for every row.
- The official 168-task `test_normal` pool was fully exposed by the historical
  full bare baseline. No untouched task remains, so a fresh 37-task manifest
  cannot be constructed honestly.
- No new V4 tag was created or moved.

## Inference

- The zero first37 gate activation is associated with a broad deployment-state
  feature shift, not an inference-code mismatch or UNK-vocabulary shift.
- Gate recalibration alone is not a viable rescue: when forced on, the correct
  compiler underperforms both bare and shuffled compilation.
- The small curated one-step specificity signal does not survive live ReAct
  trajectories.

## Unverified

- Whether a fixed trained memory-reader adapter can retain raw-memory behavior.
- Any statistically reliable test-normal task-success difference across seeds.
- Any generalization claim on a truly untouched AppWorld 0.1.0 test-normal
  subset; no such subset remains in the local benchmark pool.

## Attempts

1. `exp028b-prepare-001`: passed gate-distribution, exposure, and runtime
   preflight.
2. `exp028b-u1-001`: stopped after four atomic task rows when a live state
   reached 65,267 tokens under the locked 40,960 no-truncation contract.
3. `exp028b-u1-002`: resumed the four rows, recorded context overflow as a
   task-level terminal failure without changing the harness, and completed U1.
4. `exp028b-u2-001`: completed U2 and final analysis.

## Decision

Reached `structured_compiler_live_specificity_failed`.

Freeze structured-compiler work for the submission. Do not lower/retrain the
gate, build the full bank, start another compiler automatically, train Qwen,
start Stage C2, or create/move a V4 tag.

## Next 48 Hours

1. Lock paper claims around the replay-validated clean corpus, signature-
   balanced selector, oracle raw-transition one-step benefit, validated free
   carrier, and negative live amortization result.
2. Make an explicit scope decision between one bounded fixed memory-reader
   adapter study and no further architecture work before submission.
3. Treat all existing AppWorld `test_normal` results as development/exposed;
   do not label any subset as fresh final test data.
4. Move immediately to tables, failure analysis, limitations, and reproducible
   artifact links.

## Artifacts

- Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/ungated_structured_compiler_e2e_7h2_20260824_001`
- Gate audit:
  `research/results/exp028b_ungated_structured_e2e/gate_distribution_audit.json`
- Final analysis:
  `research/results/exp028b_ungated_structured_e2e/final_analysis.json`
- Attempts:
  `research/results/exp028b_ungated_structured_e2e/attempts.jsonl`
- Fresh-test exposure audit:
  `research/evaluation_manifests/fresh_test37_post_exp028b.json`
- Human-readable report:
  `research/results/EXP_028B_UNGATED_STRUCTURED_E2E.md`

## Termination

The EXP-028B tmux session ended normally after U2 and analysis. No Qwen,
AppWorld, or experiment process remains; GPU memory is clear. Historical idle
tmux sessions are unrelated and were not modified. The Lambda instance is safe
to terminate after final Git synchronization.
