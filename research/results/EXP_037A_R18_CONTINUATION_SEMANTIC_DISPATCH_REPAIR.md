# EXP-037A-R18 Continuation Semantic Dispatch Repair

## Decision

`VERIFIED_MINOR_NONSCIENTIFIC_INFRASTRUCTURE_DEFECT`

The 14m C00 failure was independently confirmed and repaired without changing
scientific semantics. The fresh 14n preflight passed and remains unauthorized
until the distinct R18 standing-approval authorization is persisted.

## Failure and repair

- Failed source/run: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f` /
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`.
- Exact failure: `initialize_runtime_layout()` recognized only the literal
  `continuation_14l_v1` suffix and requested absent full-run
  `preflight/shared/transitions.jsonl` before C00 dispatch.
- The same version-specific check also guarded continuation count-policy
  validation in `build_arm_runtime_config()`.
- Repair: one semantic classifier validates agreement between the schema and
  explicit continuation contract, required parent fields, the fixed O07/O08
  boundary, exclusion of parent O08 partials, and parent immutability.
- Full-run configs still select full-run initialization. Valid 14l, 14m, 14n,
  and future versioned continuation configs select continuation initialization.

## Validation

- Focused local: `32 passed`.
- Full local: `1008 passed, 3 skipped`.
- Focused Lambda/CUDA: `32 passed`.
- Full Lambda/CUDA: `1011 passed`.
- Production diagnostic root:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r18_c00_c01_20260907_001`.
- C00 manifest SHA256:
  `4d3df420f12deee996496e2e58bf1be1d9aff3fa324a2b70d200b0b1ffb396aa`.
- C01 manifest SHA256:
  `d8968dd74f068b64f9cf264d617c4e21f91bf3aa7cc68dd11070779586f5d93b`.
- Both stages exited zero and passed the real strict validator. No full-run
  compatibility tree or shared-transition prerequisite was created. Backward
  and optimizer counts were zero.

## Frozen 14n package

- Launch source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`.
- Archive: `archive/exp037a-r18-launch-source-98f917d`.
- UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Config SHA256: `6e4be2be11e608436f5b5ebfdeee45d94a61f2831c841be72cd97e8050107e01`.
- Contract SHA256: `e479889fba498401e50ff7f309668629c3745d8ea5d91d84d8600c62986e61c7`.
- Stage-scope SHA256: `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`.
- Parent-manifest SHA256: `c4cc6501982ec2b8ff9bc7061ad5ed649476d69ad606ac3e39229f16c8768a62`.
- Parent closure: `922` files,
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
- Artifact-index SHA256: `562c0fe02f70ec893b24c14dc14e36713a52b779771989cc2a520262bd30dfae`.
- Authorization-request SHA256:
  `8c3d5b5c45c23a5a0c5246856a3e317a48f1cfdf9cbff5394d19a5598c2a77f2`.
- Scope: C00/C01, O08-O19, F00-F03 only.
- Preflight authorization status: `NOT_AUTHORIZED`.

## Science and runtime

All frozen scientific sections and the 1D arm config are unchanged from 14m;
scientific configuration changes are zero. Expected/conservative wall time is
`9.5/20.0 h`, expected/conservative H100-active time is `9.3/19.5 h`, expected/
conservative additional storage is `5/10 GiB`, and the approved per-attempt
anomaly ceiling is `32 h`. Monetary cost is unavailable.

The failed 14m root and authorization remain immutable and are not reused.
