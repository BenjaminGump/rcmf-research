# EXP-037A-R17 14m C00 Terminal Failure

## Status

`FORMAL_EXP037A_14M_CONTINUATION_TERMINAL_FAILURE`

The exact R17 authorization validated and the frozen 14m process launched,
but it failed closed at `C00_parent_run_evidence_import` before any scientific
stage or parent import completed. No retry or source change was performed.

## Frozen identity

- Launch source: `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f`
- Archive: `archive/exp037a-r16-launch-source-a8cd3b6`
- Records reference: `58f890695b2f9c8e693350102c02d4fbaf48e0ed`
- Run UUID: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002`
- Config SHA256: `c8b5af07f72cd64472fa47fabd3f339d640f80ddb30cbd2fd0c8db41bee6f9f0`
- Contract SHA256: `e00df7b19d525bf8a317cbd9332f08bdacf4a4e1aa3ef47243d839f6402849be`
- Stage-scope SHA256: `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`
- Parent-manifest SHA256: `95e10d58f4f30df71cf050cffa62040de47b98c2a36dde3febcf08b5dd7e1d60`
- Parent closure SHA256: `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`

## Authorization and launch

The explicit authorization was created as a distinct file and passed the
frozen `validate_explicit_authorization()` both before and after atomic write.

- Explicit authorization SHA256: `3b9c68e155c49cfd96c2b222dab401ceab0699936c6511d53471cf0f73c2f736`
- Runtime authorization SHA256: `f99e0ae3775dce7da1bd3b779afad7b24fbfe68f4ad13bfdc97c5e3f86509e42`
- Runtime authorization validation: all checks passed
- Authorization version: `exp037a_run_bound_authorization_14m_v1`
- Scope: `continue_from_sealed_14k_o07_boundary_through_o19_and_final_reporting`
- Hard cap: 32 hours
- Run start: `2026-09-07T15:05:48.696366+00:00`
- Computed deadline: `2026-09-08T23:05:48.696366+00:00`
- Parent/full-pipeline authorization inherited: false

## Exact failure

- Stage: `C00_parent_run_evidence_import`
- Attempt: `C00_parent_run_evidence_import-1788793548718278731-r1`
- Stage wall time: 2.685234 seconds
- Exit code: 65
- Classification: fatal/non-recoverable
- Completed stages: 0
- Scientific stages started: 0
- Backward passes: 0
- Optimizer steps: 0

The stage runner failed during its unconditional runtime-layout initialization,
before the C00 stage callable ran:

1. `scripts/run_rcmf_reproducible_stage_14b.py:153` called
   `initialize_runtime_layout()`.
2. `reproducible_stages_14b.py:450` recognized continuation layout only when
   `schema_version.endswith("continuation_14l_v1")`.
3. The frozen 14m config uses
   `rcmf_reproducible_pipeline_continuation_14m_v1`, so the condition was false.
4. Execution fell through to the full-run `_compatibility_inputs()` path at
   line 473.
5. `_copy_exact()` raised `FileNotFoundError` for:

`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_002/preflight/shared/transitions.jsonl`

That file is not part of the continuation preflight by design. The failure is
therefore classified:

`CONTINUATION_SCHEMA_VERSION_DISPATCH_MISMATCH`

This is an executable integration failure, not a scientific result and not a
parent-closure failure. The R16 O13 repair was not reached or exercised by the
formal attempt.

## Evidence

- `failure.json`: `1d7a6d3578508d5848207ff37afed4936886278d3cd6aba2a3cce8b489b6b553`
- `completion.json`: `8de5d91df123e626cb87708ad9d6b7e8b2df60ffacd7f74f7c24b305a1cecd05`
- `process.json`: `bfa39828391222684b1653d3ebf7f2cfa82230ac1334fec1b5a03da15bcd854d`
- `attempts.jsonl`: `827dc084ada5bdd8783640de3eae62e71d9cfc4240cb93f78527257b7a3d82c2`
- `scheduler_state.json`: `2feb28bd26e989d767fa4d86cd5f8afb4a1e4fa95953f466eadec8cbf1251ccc`
- `orchestrator_result.json`: `2feb28bd26e989d767fa4d86cd5f8afb4a1e4fa95953f466eadec8cbf1251ccc`
- `heartbeat.json`: `b6d1592e7c482e5ea4a9575270860e8b0dc66c223b46be9d173b9618b38eef1c`
- Stage stdout/stderr are both empty-file SHA256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
  the structured traceback is in `failure.json`.

All evidence remains under the sealed 14m root. The scheduler recorded one
opened and one failed/closed attempt, with no completed attempt.

## Parent and terminal state

After the failure, the real parent-artifact validator rehashed all 922 files.
The closure remained exact at
`f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
D22, required O00-O07 stages, O06/O07 population validation, and parent O08
exclusion remained valid. The 14k parent was not modified.

The formal tmux and orchestrator are absent, no scheduler lock remains, and
H100 telemetry is `0% / 0 MiB of 81559 MiB`. The dedicated 14m status bridge
was not installed because the prerequisite healthy launch was never reached.

## Test-gap explanation and safe next action

R16 validated the repaired O13 consumer and generated the 14m preflight, but
its bounded tests did not invoke the real production stage runner at C00 using
the new 14m schema version. The continuation-layout dispatch remained tied to
the literal 14l schema suffix, so static preflight and parent-boundary checks
could pass while the first real stage failed before dispatch.

The failed 14m authorization and root must not be reused. The safe next action
is a separately reviewed executable repair that makes continuation-layout
selection semantic/version-compatible, adds a real C00 production-runner
regression, freezes a new source and run identity, and obtains new explicit
authorization. No such repair was made in R17.

## Scientific interpretation

- Parent 14k evidence: unchanged and sealed.
- One-demo continuation result: `NOT_EVALUATED`.
- Cross-arm result: `NOT_EVALUATED`.
- Scientific configuration changes in R17: 0.
- Scientific optimizer/backward count in R17: 0/0.

`FORMAL_EXP037A_14M_CONTINUATION_TERMINAL_FAILURE`
