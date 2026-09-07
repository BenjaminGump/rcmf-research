# EXP-037A-R17 14m C00 Terminal-Failure Handoff

## Outcome

`FORMAL_EXP037A_14M_CONTINUATION_TERMINAL_FAILURE`

The exact 14m authorization and runtime authorization validated, but the
first formal stage failed before its stage callable or any scientific work.

## Identity

- Source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Run: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Explicit authorization SHA256: `3b9c68e155c49cfd96c2b222dab401ceab0699936c6511d53471cf0f73c2f736`
- Runtime authorization SHA256: `f99e0ae3775dce7da1bd3b779afad7b24fbfe68f4ad13bfdc97c5e3f86509e42`
- Authorized scope: continuation C00/C01, O08-O19, F00-F03 only
- Hard cap: 32 hours

## Failure

- Stage: `C00_parent_run_evidence_import`
- Attempt: `C00_parent_run_evidence_import-1788793548718278731-r1`
- Exit: 65, fatal/non-recoverable
- Completed stages: 0
- Backward/optimizer: 0/0
- Missing path:
  `14m_root/preflight/shared/transitions.jsonl`

The 14m config's schema is
`rcmf_reproducible_pipeline_continuation_14m_v1`. The frozen runtime-layout
dispatcher only recognizes schemas ending in `continuation_14l_v1`, so it
fell through to the full-run compatibility-input path. This occurred inside
`initialize_runtime_layout()` before the C00 callable.

Classification:

`CONTINUATION_SCHEMA_VERSION_DISPATCH_MISMATCH`

## Preserved evidence

- Failure SHA256: `1d7a6d3578508d5848207ff37afed4936886278d3cd6aba2a3cce8b489b6b553`
- Completion SHA256: `8de5d91df123e626cb87708ad9d6b7e8b2df60ffacd7f74f7c24b305a1cecd05`
- Attempts SHA256: `827dc084ada5bdd8783640de3eae62e71d9cfc4240cb93f78527257b7a3d82c2`
- Orchestrator SHA256: `2feb28bd26e989d767fa4d86cd5f8afb4a1e4fa95953f466eadec8cbf1251ccc`
- Parent closure after failure: 922/922, SHA256
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`

No source/config/contract/parent artifact was modified. No retry, resume, or
monitor bridge was started. Formal tmux/process/lock are absent and H100 is
idle.

## Next safe action

Do not reuse this root or authorization. A new task must minimally repair the
continuation schema dispatch, test C00 through the actual production stage
runner, freeze a new source/root/config/contract, and obtain fresh explicit
authorization. Parent 14k evidence remains valid; one-demo and cross-arm
science remain `NOT_EVALUATED`.
