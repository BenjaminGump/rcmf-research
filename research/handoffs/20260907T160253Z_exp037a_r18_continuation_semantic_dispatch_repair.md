# EXP-037A-R18 Structured Handoff

- Classification: `VERIFIED_MINOR_NONSCIENTIFIC_INFRASTRUCTURE_DEFECT`
- Starting records SHA: `709cd0b56e469f56075df3b60be134e5cad4331e`
- Frozen launch source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`
- Archive: `archive/exp037a-r18-launch-source-98f917d`
- Replacement UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Replacement root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Config/contract: `6e4be2be11e608436f5b5ebfdeee45d94a61f2831c841be72cd97e8050107e01` /
  `e479889fba498401e50ff7f309668629c3745d8ea5d91d84d8600c62986e61c7`
- Parent manifest/closure: `c4cc6501982ec2b8ff9bc7061ad5ed649476d69ad606ac3e39229f16c8768a62` /
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Artifact index/authorization request: `562c0fe02f70ec893b24c14dc14e36713a52b779771989cc2a520262bd30dfae` /
  `8c3d5b5c45c23a5a0c5246856a3e317a48f1cfdf9cbff5394d19a5598c2a77f2`
- Production diagnostic: C00/C01 `2/2` strict PASS; parent `922/922` unchanged;
  zero backward and optimizer operations.
- Tests: local `32 focused`, `1008 full + 3 skipped`; Lambda `32 focused`,
  `1011 full`.
- Scientific diff: zero. Only semantic continuation routing/provenance checks
  changed.
- Runtime: expected/conservative wall `9.5/20.0 h`; H100-active `9.3/19.5 h`;
  hard cap `32 h`.
- Next action: use R18 standing user approval to create and validate a fresh
  exact 14n authorization, launch from the frozen source, and monitor until F03
  or a scientific/ambiguous stop.
