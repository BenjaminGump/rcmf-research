# EXP-025B Root-Login JWT Replay Report

## Contract

The prospective `appworld_observation_semantic_normalization_7b_v1` keeps the
existing named `access_token` rule and adds only root path `$` for an
AST-verified single AppWorld login call. It ignores only numeric `exp` and the
signature bytes changed by `exp`; algorithm, type, stable claims, and semantic
claim hash remain represented.

## Validation

- Corrected sentinel twice: `13/13`, `102/102` priors each run.
- Fixed root-JWT sentinel twice: `3/3`, `20/20` priors each run.
- Full 45-state replay twice: `45/45`, `372/372` priors each run.
- Exceptions: `0`; non-temporal mismatches: `0`.
- Locked v2 control: `42/45` histories and `369/372` priors.
- Focused adversarial and integration suite: passed.

The replay gate records `identity_reconciled_replay_validated`; historical
replay artifacts and normalizers remain unchanged.
