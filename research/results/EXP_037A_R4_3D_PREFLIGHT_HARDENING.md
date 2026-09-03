# EXP-037A-R4 Future 3D Run Identity and Authorization Hardening

Date: 2026-09-03

## Decision

`READY_FOR_EXPLICIT_USER_APPROVAL`

This status means the fresh future-run package is internally consistent and
ready for review. It does not authorize launch. No scientific stage ran.

## Git and Scope

- Starting commit: `b42260ff6e1de47295085197555164c2656f2ec2`
- Implementation commit: `ed9b7c22fb7452a8ec6eb5faacbe10976829abf8`
- Branch: `research/v6-rcmf-exp037a-3d-preflight-hardening`
- Scientific changes from EXP-037A-R3: zero
- Optimizer, backward, representation rebuild, selector training, AppWorld
  generation, and H100 scientific active counts: zero

## Fresh Run Identity

- Frozen old UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`
- Fresh future UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001`
- Frozen old root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`
- Fresh future root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14f_20260903_001`
- Lambda static preflight verified that the fresh root did not exist and had
  no prior entries. The preflight did not create it.
- Main config: `configs/pipeline/rcmf_appworld_repro_14f.yaml`
- Main config SHA256:
  `226ba22d8dd3bfc464b9198a6ab634170ce9386ecd1973044e6e35357825abd4`

## Authorization Hardening

The new package records:

- `authorization_status = NOT_AUTHORIZED`
- `granted_by_user = false`
- `previous_200_hour_authorization_inherited = false`
- `full_pipeline_authorized = false`
- `d06_or_later_authorized = false`
- `one_demo_authorized = false`
- `automatic_three_demo_launch_after_preflight = false`
- proposed hard cap = 80 hours

The launcher now requires an explicit authorization file. A valid future
authorization must match the current run UUID, canonical run root, source
commit, contract SHA256, pipeline-config SHA256, and approved hard cap. It must
also explicitly authorize the full pipeline, D06 and later, and the conditional
one-demo arm, while recording that the old 200-hour approval was not inherited.
The scheduler independently revalidates the same bindings before executing a
stage. A stale or incomplete authorization fails closed.

The 80-hour cap is a proposal only. It does not imply approval.

## Scientific Invariance

Static resolution verified:

- selector parent split: seed 18018, 29 train parents, 8 heldout parents;
- causal panel: 256 initial, 499 maximum, 40 minimum per label;
- selector CV seed 25071;
- selector final seeds 25071, 25072, 25073;
- 3D prompt `full_demo`;
- 1D prompt `full_demo_first_only`;
- exact resolved-arm differences remain on the existing prompt-dependent
  allowlist only;
- post-D06 outcomes 366 train and 98 heldout remain a gate with
  `construction_input = false`.

No data, model, prompt, selector, writer, field, reader, loss, evaluator, or
checkpoint-selection rule changed.

## Runtime Proposal

- Expected wall time: 26.5 hours
- Conservative wall time: 56 hours
- Expected H100 active time: 21.8 hours
- Expected storage: 46 GiB
- Conservative storage: 90 GiB
- Proposed anomaly cap: 80 hours
- Explicit approval is required because the run may exceed 18 hours.
- Restart remains stage-atomic with append-only attempts and hash validation
  before resume skips.

## Tests

Final local focused suite: `33 passed`.

Final local full suite with process-start `PYTHONHASHSEED=25101`:
`861 passed, 2 skipped`.

Lambda focused suite: `33 passed`.

Lambda full suite with process-start `PYTHONHASHSEED=25101`:
`863 passed`.

Two test-development failures were corrected before final validation: one
test-only quoting typo and one Windows rendering difference for a POSIX Lambda
path. Neither reached or changed scientific code or artifacts.

## Artifacts

The machine-readable package is under
`research/results/exp037a_r4_3d_preflight_hardening/`. Its static stage DAG SHA256
is `515ea361a053a82eae8696b2fef2a5a3fddc06fa0a75e18274390714180ac72f`,
and its preflight summary SHA256 is
`20289f26eef821d701eae7f6a44d0fbdb70f4e0c41fd1d105b9f0c05c86b72b5`.

## Required Stop State

- Not authorized to launch.
- Old 200-hour authorization not inherited.
- No selector training or representation rebuild ran.
- No D06, D07, D08, D09, or 1D stage ran.
- H100 scientific active time is zero.
- No new long scientific run launched.
