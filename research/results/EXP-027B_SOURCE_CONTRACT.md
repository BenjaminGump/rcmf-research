# EXP-027B Source Contract

- Milestone: `EXP-027B` / matched-harness bare correction and memory-specific deep policy amortization
- Run UUID: `memory_specific_deep_amortization_7g_20260823_001`
- Global seed: `25101`
- Starting commit: `593fe203ab9db05889ae576da59b35124f2014ab`
- Archive branch: `archive/v4-deep-residual-amortization-failed`
- Working branch: `research/v4-memory-specific-deep-amortization`

The EXP-027A run and artifacts remain immutable. The matched-bare condition uses
the same AppWorld 0.1.0 full-agent bridge and differs from raw memory only by
disabling the selector and memory insertion.

The compiler keeps the EXP-027A PairMLP architecture, deep-residual carrier,
representations, prompt, selector, pair universe, split, and ratio budget. Its
correct-pair objective is raw-policy KL plus teacher-token and ground-truth CE.
Every update also applies Qwen-gradient supervision to exactly one deterministic
mismatch: transition mismatch on odd rounds and state mismatch on even rounds.

Checkpoint selection is A-validation only. The fixed score is:

```text
(zero_raw_KL - correct_raw_KL)
  - 0.5 * (transition_mismatch_to_bare_KL + state_mismatch_to_bare_KL)
```

Both state and transition specificity gaps must be positive, metrics finite,
and every selected-layer residual ratio at most 1.0. B/C/D/E are evaluated only
after the checkpoint is frozen.

One raw-policy pair lacks the locked 512-token generation headroom. It remains
an explicit over-context missing row with no truncation, zeroing, neutral label,
or imputation.
