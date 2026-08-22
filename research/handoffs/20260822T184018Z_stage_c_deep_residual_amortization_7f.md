# Structured Handoff: EXP-027A

## Identity

- Run: `deep_residual_amortization_7f_20260822_001`
- Branch: `research/v4-deep-residual-amortization`
- Starting SHA: `f4ec45b2d16ccebc17b481d629a0a3a445d38aa6`
- Source SHA: `8467756c4ebb433b4bcceef5bce65e0a25e82c5b`
- Seed: `25101`
- Decision: `deep_residual_amortization_failed`

## VERIFIED

- The first-37 automatic selector plus raw-memory run is `5/37`, versus the
  immutable bare baseline `10/37`; it is `CLEARLY_WEAK`.
- The PairMLP selected u8 by A-validation Huber. B/C/D/E Spearman is
  `0.572432/0.515658/0.545204/0.583406`; Huber reduction versus zero is
  `44.34%/42.26%/43.01%/44.08%`.
- The formal one-step run completed `180/180` conditions with zero exceptions,
  same-world count `180`, and same-namespace count `180`.
- On 32 primary states P1 signature/successor/execution is
  `0.40625/0.59375/0.84375`. C0 is `0.31250/0.43750/0.93750`; F3 is
  `0.68750/0.78125/1.00000`.
- P1 is worse than transition shuffle on both primary behavioral metrics and
  equal to state shuffle on successor. It is positive on `4/9` tasks.
- Raw-gain retention is `25.00%` for action signature and `45.45%` for
  semantic successor. Classification is `CLEAR_FAILURE`.
- Phase D did not run. No factorized program, full bank, Stage C2, or V4 tag
  was created.

## INFERENCE

- The fixed carrier is sufficient, but current observation-excluded
  state/transition representations plus direct objective do not amortize the
  pair-specific behavioral mechanism.
- One-step selected-raw-memory gains do not compound into first-37 task
  success under the current automatic retrieval loop.

## UNVERIFIED

- Adapter-level procedural features in the compiler.
- A fixed trained memory-reader adapter.
- End-to-end compiled RCMF under a different amortization mechanism.

## Recovery Record

- Append-only attempt ledger: 12 attempts, all closed; 3 nonzero infrastructure
  attempts were preserved.
- One pre-ledger import-only invocation failed before attempt initialization.
  Commit `8467756c` fixed the representation-loader import; 17 focused tests
  passed locally and on Lambda, and the resumed preflight/formal/analyze chain
  completed without duplicate conditions.
- Selected checkpoint SHA256:
  `84633fa6460b52ac6723e0c7eb6b7673b7f0dfaa08de17688fa605de7b32a1ce`.
- Final evaluation SHA256:
  `9c6d26dfd8b03d086a1cde6dc00a0b1812b9e40d95854039005a161c2064fe2e`.
- One-step analysis SHA256:
  `5d0c7472782adaa5bdfa496739e7f3020ab7255e59a3b679cecf1a393d7c6afd`.

## Required Review

Freeze the compiled-program route for the submission. Review paper scope and
the negative amortization result. If one further component experiment is
explicitly authorized, choose one of: an AppWorld-enhanced structured compiler
or a fixed trained memory-reader adapter. Do not resume Phase D, add another
carrier, run a rank sweep, or start full-bank integration automatically.

## Artifact Root

`/lambda/nfs/rcmf-persist/project/runs/stage_c/deep_residual_amortization_7f_20260822_001`

Primary files:

- `phase_a_first37_v3/summary.json`
- `compiler/pairmlp/training_summary.json`
- `compiler/pairmlp/final_evaluation_summary.json`
- `compiler/pairmlp/one_step/generation_summary.json`
- `compiler/pairmlp/one_step/analysis.json`
- `attempts.jsonl`
