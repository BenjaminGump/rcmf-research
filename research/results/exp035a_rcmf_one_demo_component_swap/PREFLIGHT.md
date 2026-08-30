# EXP-035A Frozen Preflight

## Verified

- Run UUID: `rcmf_one_demo_component_swap_12a_20260831_001`.
- Frozen task order: `76f2c72_1`, `76f2c72_2`, `7d7fbf6_1`,
  `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`,
  `e7a10f8_3`.
- Task-list SHA256:
  `2f6cd62aeb004156b3d7e418aa27d98586a0e83afa82c894b345f509a9d718e8`.
- The field ledger contains exactly 401 model-training memories and zero
  heldout-parent memories.
- The old and fresh historical 401-memory shuffle mappings match exactly.
- The common shuffle is a 401-row bijection with zero fixed points.
- Native OO and FF fields reconstruct their historical 401-memory correct and
  shuffled anchors exactly.
- Native 499-memory reconstruction, selector factorization, and
  add/remove/restore checks are all within the preregistered `1e-5` tolerance.
- Every OO/OF/FO/FF correct and shuffled field is finite and nonzero.
- One-demo prompt identity and retained-demo identity match the frozen hashes.
- No optimizer step, generation, AppWorld execution, dev task, first37 task, or
  test task occurred during preflight.

The identity/field preflight SHA256 is
`4025188c8ef10bbbfc406161193383c99c5a6f9886ec9b2cd760fd44b6aabc10`.

## No-Generation Diagnostics

All 98 immutable heldout state rows were evaluated with one Qwen prompt forward
and final-prompt-token reader adapter diagnostics at layers 7, 14, 21, and 28.
No token was generated and no AppWorld condition was executed.

- Old/fresh selector score Spearman: mean `0.873211`, median `0.908684`.
- Top-1 memory overlap: mean `0.561224`.
- Top-4 overlap: mean `3.091837 / 4`.
- Top-8 overlap: mean `6.612245 / 8`.
- Old/fresh per-memory payload cosine: mean `0.636804`.
- Old/fresh selector flattened-field cosine is approximately `-0.02` under
  either writer and either binding.
- All query, slot, residual, and attention values are finite.

These diagnostics are descriptive only. They did not remove a cell, alter a
scale, select a checkpoint, or modify the frozen trajectory manifest.

The diagnostic summary SHA256 is
`3d9f8f4bc1bf846fb6390f32ad0bd32f21f2086461b9f2d6bee5800b77330af9`.

## Attempts

- `exp035a-preflight-001`: failed before field construction because the
  implementation transcribed the old-selector SHA as 65 characters.
- `exp035a-preflight-002`: passed after restoring the exact immutable SHA.
- `exp035a-diagnostics-001`: failed on the first row because the diagnostic
  omitted EXP-031A's BF16 autocast context.
- `exp035a-diagnostics-002`: passed with the historical inference autocast
  semantics; runtime was 36.96 seconds.

No failed attempt produced a behavioral result.
