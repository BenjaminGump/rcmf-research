# EXP-037A-R4 Structured Handoff

## Status

- Decision: `READY_FOR_EXPLICIT_USER_APPROVAL`
- Authorization: `NOT_AUTHORIZED`
- Starting commit: `b42260ff6e1de47295085197555164c2656f2ec2`
- Implementation commit: `ed9b7c22fb7452a8ec6eb5faacbe10976829abf8`
- Branch: `research/v6-rcmf-exp037a-3d-preflight-hardening`

## What Changed

- Added a unique `14f` run package and new NFS root identity.
- Added run-bound authorization validation for UUID, root, source, contract,
  config, cap, and explicit scope.
- Hardened the scheduler to revalidate strict authorization before stage start.
- Removed fixed 200-hour authorization assumptions from the launcher.
- Added a static-only preflight builder and focused regression tests.

No R3 scientific semantics changed.

## Frozen Future Identity

- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001`
- Config SHA256:
  `226ba22d8dd3bfc464b9198a6ab634170ce9386ecd1973044e6e35357825abd4`
- Lambda verified the root was absent before and after static preflight.

## Authorization

All authorization booleans are false. The prior 200-hour permission is not
inherited. The proposed cap is 80 hours and requires a new explicit user
approval. Preflight cannot persist approval or launch stages by itself.

## Invariants and Runtime

- Parent split: seed 18018, 29/8.
- Panel: 256/499/40.
- Selector: CV 25071, final 25071/25072/25073.
- Prompts: 3D `full_demo`, 1D `full_demo_first_only`.
- Post-D06 gate: 366/98, never panel input.
- Runtime: expected 26.5h, conservative 56h, H100 active 21.8h.
- Storage: expected 46 GiB, conservative 90 GiB.

## Validation

- Local focused: 33 passed.
- Local full: 861 passed, 2 skipped.
- Lambda focused: 33 passed.
- Lambda full: 863 passed.
- Scientific stage executions: 0.
- H100 scientific active time: 0.

## Review Entry Points

- Report: `research/results/EXP_037A_R4_3D_PREFLIGHT_HARDENING.md`
- Machine summary:
  `research/results/exp037a_r4_3d_preflight_hardening/preflight_summary.json`
- Authorization state:
  `research/results/exp037a_r4_3d_preflight_hardening/authorization_state.json`
- Artifact index:
  `research/results/exp037a_r4_3d_preflight_hardening/artifact_index.json`

No long run was launched. A later task must persist a fresh, run-bound explicit
authorization before the launcher can create runtime authorization.
