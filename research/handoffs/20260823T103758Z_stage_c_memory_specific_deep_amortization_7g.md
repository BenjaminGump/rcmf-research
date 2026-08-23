# EXP-027B Structured Handoff

## Identity

- Run: `memory_specific_deep_amortization_7g_20260823_001`
- Branch: `research/v4-memory-specific-deep-amortization`
- Start: `593fe203ab9db05889ae576da59b35124f2014ab`
- Source: `6f5f5bd22411f8f12a25111935f074a98a98d3b0`
- Preflight compatibility fix:
  `bcf768298dccc410fc7fb411bf434bbd20e814a8`
- Seed: `25101`
- Decision: `memory_specific_deep_amortization_failed`

## Verified Result

- Exact-harness bare first37: `8/37`.
- Frozen-selector raw transition first37: `5/37`.
- A/B/C/D/E structural pairs: `607/135/112/112/135`.
- A train/validation: 479/128; policy-scoreable train: 478.
- Raw-policy teachers: 968 total, 252 reused and 716 new.
- A-only selected checkpoint: u4,
  SHA `8af2d1068ee0059c79edf740920f9d506b8a880459ff53656b449f767e5dffda`.
- Teacher-forced B/E correct raw-policy KL: `0.10858/0.10536`.
- Teacher-forced B/E transition specificity: `0.11741/0.10859`.
- Teacher-forced B/E state specificity: `0.11765/0.10883`.
- One-step conditions: 180/180 complete, zero exceptions, P0=C0 for 45/45.
- Primary P1 signature/successor: `0.40625/0.53125`.
- Primary C0 signature/successor: `0.31250/0.43750`.
- Primary P2 signature/successor: `0.40625/0.53125`.
- Primary P3 signature/successor: `0.40625/0.53125`.
- P1 execution: `0.87500`, versus C0 `0.93750`.
- Raw-gain retention: signature `0.25000`, successor `0.27273`.
- Positive tasks: `4/9`.
- Classification: `CLEAR_FAILURE`.

## Interpretation

VERIFIED: direct Qwen-gradient mismatch training produces positive policy-
space specificity on A validation and B/C/D/E, but correct and shuffled
programs remain behaviorally indistinguishable on the two primary one-step
metrics.

INFERENCE: the current generic observation-excluded PairMLP/program route does
not provide a submission-ready memory-specific compiler.

UNVERIFIED: an AppWorld-structured compiler using deployment-available
procedural features may still work.

## Required Next Action

Stop generic PairMLP/program work. Do not start rank sweeps, factorized models,
a memory-reader adapter, full-bank integration, `p(s,m_transition)`, Stage C2,
end-to-end RCMF, full AppWorld evaluation, or V4 tagging.

The only compiler rescue eligible for a separately reviewed milestone is an
AppWorld-structured compiler using deployment-available procedural features.
Given the deadline and the matched first37 raw-memory result, review submission
scope and expected paper value before authorizing that rescue.

## Recovery

- All 9 attempts are closed in `attempts.jsonl`.
- One preflight attempt stopped on a missing legacy runtime-estimate key before
  generation. Commit `bcf7682` added a tested fallback; no science changed.
- Resume identity is preserved through the final evaluation and one-step
  summaries.
- Qwen, selector, prompt, carrier, representations, and raw teacher remained
  frozen.

## Artifacts

Root:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/memory_specific_deep_amortization_7g_20260823_001`

Read first:

1. `compiler/pairmlp/one_step/analysis.json`
2. `compiler/pairmlp/final_evaluation_summary.json`
3. `compiler/pairmlp/training_summary.json`
4. `phase_a_matched_bare_first37/summary.json`
5. `attempts.jsonl`

GitHub-safe detailed report:

`research/results/stage_c_memory_specific_deep_amortization_7g_20260823_001.md`

## Machine State

- NFS: mounted.
- EXP-027B processes: none.
- EXP-027B tmux sessions: none.
- H100: 0 MiB used, 0% utilization.
- Safe to terminate: yes, after final Git synchronization.
