# EXP-037A-R12A First-Divergence Forensic Handoff

Date UTC: 2026-09-05T13:18:18Z

## Decision

- Final decision: `READY_FOR_SCOREABILITY_REPAIR_DESIGN`
- Phase-A classification: `THREE_DEMO_STATIC_FILTERED_STATE`
- Root cause: `PROMPT_PROFILE_CONFIG_SOURCE_MISMATCH`
- Secondary: `TOKEN_COUNT_CONTRACT_MISMATCH`, non-causal at target
- Production repair: not implemented

## Git And Run Identity

- Starting records SHA: `53b534263e6bd8de3f1602fdfd5d99cfa9f88954`
- Audit evidence commit: `9571d5a303bcd7f5bd45f43583d4e4252356ee16`
- Frozen source: `0e4015547da45802cc7b6ff3a9b92adce73077fc`
- Formal UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Formal root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Diagnostic ID: `rcmf_exp037a_r12a_3d_1d_first_divergence_20260905_001`
- Diagnostic root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r12a_3d_1d_first_divergence_20260905_001`

## Exact Finding

O05 uses the resolved 1D prompt `full_demo_first_only`. O06 ignores that arm
setting: the stage dispatcher passes the shared 7b replay config, and the
runtime reads its hard-coded `full_demo` profile. D05/D06 are aligned because
both legitimately use `full_demo`; O05/O06 are not.

For `appworld:trace:229360a_3:step:27:line:382`, both arms select the same class
and same sole legal candidate transition `60716ec9-9ca6-5011-9efc-15b0db10b2f0`.
D05's full-demo raw prompt is 42,924 tokens and is statically filtered. O05's
one-demo raw prompt is 38,075 and is admitted. Runtime-equivalent live counts
are 42,927 under `full_demo` and 38,078 under `full_demo_first_only`.

The target is in both initial panels. D06 skipped it; O06 wrote only its bare
condition before fatal raw preflight. Quota evolution did not cause the arm
difference.

## Global And Phase-B Evidence

- Static state universes: exact `499/499`.
- Scoreability: both scoreable 472, 1D-only 16, 3D-only 0, both unscoreable 11.
- Selected classes: 414 same, 85 different.
- Selected transitions: 405 same including null, 94 different.
- Targeted census: 276 states; 259 replay-ready, 17 replay-unavailable.
- Correctly profiled static/live scoreability mismatch: `0/259` in either arm.
- Wrong full-demo O06 runtime over-context: six states.
- Same-class live-feasible alternatives: zero.
- Census time: 3,472.1 seconds; Qwen generations and target actions: zero.

The full 499 census was not needed. The 17 replay-unavailable rows retain typed
error hashes and no fabricated feasibility result.

## Scientific Status

The sealed three-demo evidence remains valid: D06B and D22 passed, and dev
bare/correct/shuffle remains `12/17/11`. Complete 1D and cross-arm science are
not evaluated. Partial O06 outputs remain diagnostic-only.

## Preservation

- Formal root inventory before/after: 22,434 files, identical SHA256
  `6eb6b3b1afa25b9837c49eea1033d09b909a35e6a509c0954fc8f1741a788e87`.
- No production source/config change.
- No formal retry, resume, authorization, or new run.
- No Qwen generation, target action, optimizer, or H100 science.
- Raw observations/stderr remain Lambda-only.

## Recommended Next Task

Use Direction A: repair prompt-profile propagation so the paired runtime reads
the arm-resolved config, and unify the preflight/runtime chat-template call to
explicitly disable thinking. Preserve scoreability semantics, selector,
memories, panel, context limit, and D06B/D22. Add regression coverage, freeze a
new run identity, and request fresh authorization.

Do not implement typed runtime missingness or member substitution from this
evidence.

## Git-Safe Records

- Report: `research/analysis/EXP_037A_R12A_3D_1D_FIRST_DIVERGENCE_AUDIT.md`
- Summary: `research/analysis/exp037a_r12a_3d_1d_first_divergence/summary.json`
- Artifact index: `research/analysis/exp037a_r12a_3d_1d_first_divergence/artifact_index.json`
- Input index: `research/analysis/exp037a_r12a_3d_1d_first_divergence/input_artifact_index.json`
- Census rows: `research/analysis/exp037a_r12a_3d_1d_first_divergence/targeted_census_rows.jsonl`
