# EXP-036B Determinism Root Cause

## Scope

This audit reads the exact unredacted EXP-036A smoke rows from Lambda. It does
not rewrite EXP-036A and does not use task success to choose a repair.

Source root:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13a_20260831_002/formal/smoke_v2`

## VERIFIED

- `B0` versus `B0-REPEAT` first differs at step 18. The observation SHA256
  values are `43263e605ca3bf2328506060d744a6ddca2b98edb215480fb3898b0018fa2c2d`
  and `c6f815d82b9153e7aac68724d19990f6d36058bdce23ea1a30de58ac398c9c4c`.
- `FULL1D-S` versus `FULL1D-S-REPEAT` first differs at step 19. The observation
  SHA256 values are `c64fa7824a3ad5e8a04bfb40e4435d5607bcee7cfa11848d3694f0f4285b0bd6`
  and `74e6624d7a67c72f7c8cd6925bf15311a1404e272a664217d21322c308b79692`.
- Before each first difference, the complete prompts, generated token IDs, raw
  responses, and executed code are exact matches. The same fields also match at
  the divergence step itself.
- The world action, state fingerprints before and after the action, task
  completion state, and authoritative evaluator state match at each divergence.
- A quote-aware balanced-brace scan followed by `ast.literal_eval` finds two
  `set[str]` literals in each differing observation. Corresponding sets have
  identical type-tagged member hashes and cardinalities.
- Replacing all parsed set spans by indexed sentinels yields byte-identical
  non-set text. Every set's source rendering differs, and no non-set semantic
  difference was found.
- Raw values are intentionally omitted from this Git-safe report. Exact row
  hashes and set semantic hashes are recorded in `determinism_root_cause.json`.

## INFERENCE

Python process hash randomization is the likely source of the different set
iteration order. This is consistent with the observed representation-only
difference but is not yet treated as proven repair evidence.

## UNVERIFIED

Whether `PYTHONHASHSEED=25101` before interpreter startup is sufficient for
complete cross-process equality remains unverified until the preregistered B0
and FULL1D-S Stage 1 probe completes.

## Decision

The hard stop for a non-set semantic difference is not triggered. EXP-036B may
proceed to Stage 1 only. Set canonicalization remains disabled unless Stage 1
fails with the same strictly set-order-only divergence.
