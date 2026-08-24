# EXP-028A Structured Handoff

## Resume Identity

- Run: `appworld_structured_gate_compiler_7hr_20260823_001`
- Branch: `research/v4-appworld-structured-rescue`
- Starting SHA: `78f8e2e674709775b8a2a9b92297d255ec0e73c3`
- Final source SHA: `52357e234ebf516bb507cb9cffbb2f6cedcb5f3e`
- Archive: `archive/v4-generic-memory-specific-amortization-failed` at the starting SHA
- Seed: `25101`
- Attempt ledger: `research/results/exp028a_appworld_structured_gate_compiler/attempts.jsonl`
- Lambda run: `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_structured_gate_compiler_7hr_20260823_001`

## Decision

Reached `appworld_structured_compiler_competitive`.

The train-side causal gate passed, the structured compiler was
`PARTIAL_POSITIVE` on the locked one-step audit, and compiled first37 was
`8/37`. The first37 gate activated `0/873` times, so that task result is bare
Qwen behavior and does not validate compiled memory.

## Verified Facts

- Paired causal data: `464` states and `928` T0/T1 conditions; labels
  `129 POSITIVE / 300 NEUTRAL / 35 HARMFUL`.
- Explicit missing accounting: `27` over-context and `8` strict-replay rows;
  all `499` train states are accounted for.
- Structured leakage audit: `186` deployment-available features and zero violations.
- Gate: epoch `200`, temperature `2.0`, threshold `0.60`, activation `11/98`,
  harmful activations `0`, successor `0.408163 -> 0.459184`, signature
  `0.397959 -> 0.469388`, execution `0.928571 -> 0.938776`.
- Gated raw first37: `8/37`, gate ON `0/872` turns.
- Structured compiler: u4 selected using heldout clean-train tasks only;
  checkpoint SHA256 `95bc2869df1084eb1166cadeb0edfad584814d5fe0c049b3d7beab59b2c4cab3`.
- Locked audit: `180/180` generated/executed. On 32 primary states, S1
  signature/successor is `0.40625/0.53125`, versus C0 `0.31250/0.43750`,
  S2 `0.37500/0.50000`, and S3 `0.37500/0.50000`.
- S1 is positive on only `2/9` tasks; classification is `PARTIAL_POSITIVE`.
- Compiled first37: `8/37`, gate ON `0/873`, interpretation `COMPETITIVE`.
- Attempt ledger: `22` attempts, `13` successful, `9` failed, all closed.
- Runtime: `13.8011` wall hours and approximately `10.02` GPU-attributed process hours.
- Focused final regression suite: `51 passed` locally; the phased source suite
  also passed on Lambda before the completed scientific run.

## Inferences

- Compact structured features can identify a useful train-side subset and can
  produce a small one-step memory-specific compiler effect.
- The locked gate does not operationally transfer to first37 test-normal
  turns: zero activation means neither gated end-to-end run tests memory use.
- Full-bank construction is premature before a deployment-feature-only audit
  explains the activation-domain mismatch.

## Unverified Claims

- End-to-end compiled-memory improvement.
- Full-bank constant-size field behavior.
- Robustness across seeds or beyond AppWorld-specific structured features.

## Attempt Deviations

- Eight strict semantic-v3 replay rows remain missing; normalization was not changed.
- Residual hooks were fixed to survive checkpoint recomputation through backward.
- Live residual projection now uses same-run bare block-input norms and a
  versioned `0.99` numerical margin while preserving the `<=1.0` budget.
- All stopped attempts remain in the ledger and contributed no accepted output.

## Next 48 Hours

1. Do not build the full bank or another compiler architecture.
2. Audit gate score, calibration, intent, selector-margin, and context-feature
   distributions on train-validation versus first37 using deployment features
   only and without task outcomes.
3. Decide the submission claim: train-side gate plus partial one-step compiler,
   or a bounded negative end-to-end result.
4. Keep Stage C2, p(s,m_transition), Qwen training, full-bank integration, and
   V4 tagging blocked pending that review.

## Artifacts

- Final report: `research/results/stage_c_appworld_structured_gate_compiler_7hr_20260823_001.md`
- Git-safe evidence: `research/results/exp028a_appworld_structured_gate_compiler/`
- Full durable artifacts: `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_structured_gate_compiler_7hr_20260823_001`
