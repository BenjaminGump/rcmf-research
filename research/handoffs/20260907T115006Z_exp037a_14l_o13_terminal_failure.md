# EXP-037A 14l O13 Terminal Failure Handoff

- Recorded UTC: `2026-09-07T11:50:06Z`
- Source: `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- Run: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Decision: `VERIFIED_CONTINUATION_STATE_CACHE_PATH_OWNERSHIP_MISMATCH`
- Scientific result: complete one-demo and cross-arm results `NOT_EVALUATED`

The frozen O13 helper read a continuation-local state cache instead of the
explicit `stage_c_9a.prompt_dependent_inputs.state_cache` parent input. The
configured parent artifact and full parent closure validate exactly; O09-O12
remain strict-valid. O13 executed zero trajectory tasks and produced no valid
scientific output.

Do not resume 14l. If the bounded repair validates, construct a fresh package
from sealed 14k O07 and rerun O08-F03 under one replacement source. Do not use
14l checkpoints or completions as formal replacement inputs.

See `research/results/EXP_037A_14L_O13_TERMINAL_FAILURE.md` and its machine-
readable summary/index.
