# EXP-037A 14j Terminal Failure At O06

Date: 2026-09-05

Decision: `INFRASTRUCTURE_IMPLEMENTATION_FAILURE`

Root-cause classification: `DETERMINISTIC_LIVE_CONTEXT_PREFLIGHT_MISMATCH`

Final cross-arm scientific result: `NOT_EVALUATED`

## Executive Summary

The frozen 14j run completed 42 stages and terminated at the one-demo stage
`O06_paired_causal_outcomes`. The three-demo arm was complete: D06B exactly
reproduced the historical 366 train, 98 heldout, and 129/300/35 label contract;
D08B passed; both writer/reader epochs completed; and D22 returned
`THREE_DEMO_REPRODUCTION_PASS`. This is valid sealed three-demo evidence.

The one-demo arm completed O00-O05. O06 completed 178 paired states (356
condition outputs), then completed the bare condition for state 179. Before
generating that state's raw-memory condition, the live renderer found the
prompt over the 40,960-token context limit and failed closed. The stage has no
`output_manifest.json` or sealed `paired_outcomes.json`, so the 357 atomic
condition files are diagnostic/resume evidence only and are not a completed
one-demo panel.

No final 3D-versus-1D comparison exists. O07-O19 and F00-F03 were not run.
The failed root is immutable and must not be retried under the existing source
or authorization.

## Frozen Identity

- Launch source: `0e4015547da45802cc7b6ff3a9b92adce73077fc`
- Archive: `archive/exp037a-r10-launch-source-0e40155`
- Records reference: `5e57a92951812a23d052629eb7a7a5d30985cd78`
- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Config SHA256: `470f7183adb8804540c46e146e2db9e359f09f67b43eb0d3e0fab153bedf41a9`
- Contract SHA256: `2ff7810700abe99c53c298978e0c6f14d56f6cd0465d2c8065d3569e59abdafb`
- Preflight artifact-index SHA256: `8c0af8a6c1dbd4e1a95dfeba60a334924dcf4b453db6a633afbd46fadd98f46b`
- Global seed: `25101`

The formal attempt interval was `2026-09-04T13:30:33.460010Z` through
`2026-09-05T09:10:31.164604Z`, or 70,797.705 seconds (19.666 hours).

## Verified Three-Demo Result

D06B passed exact post-seal historical comparison:

- completed train states: `366/366`;
- completed heldout states: `98/98`;
- labels: `POSITIVE=129`, `NEUTRAL=300`, `HARMFUL=35`;
- over-context states: `27/27` with identical sets;
- replay-semantic failures: `8/8` with identical sets;
- no historical artifact was used for generation.

D22 then returned `THREE_DEMO_REPRODUCTION_PASS` and
`continue_to_one_demo=true`. Under the common one-demo dev deployment prompt,
fresh 3D outcomes were bare `12/57`, correct `17/57`, and shuffled `11/57`.
The historical references were `12/57`, `17/57`, and `12/57`. Thus the frozen
D22 contract accepted absolute `+5` tasks and matched-shuffle specificity `+6`
tasks despite the one-task shuffled-count difference. No threshold was changed.

## Exact O06 Failure

- Stage: `O06_paired_causal_outcomes`
- Attempt: `O06_paired_causal_outcomes-1788596760065108009-r1`
- Stage PID: `769150`
- Started: `2026-09-05T08:26:00.075142Z`
- Failed: `2026-09-05T09:10:30.740411Z`
- Completion record: `2026-09-05T09:10:31.157904Z`
- Stage wall time: about 2,671 seconds (44.5 minutes)
- Child exit: `1`; scheduler completion exit: `65`
- Classification: `fatal`; recoverable: `false`
- Validator: `passed=false`, reason `missing_output_manifest`
- Failed condition key: `f0106e0a9f6ea5889b2aeda8ad24828bf208296aa879fd3c50704f2b19e0e65a`
- Condition: `T1_selected_raw`
- State: `appworld:trace:229360a_3:step:27:line:382`
- Selected transition: `60716ec9-9ca6-5011-9efc-15b0db10b2f0`
- Selected class: `procedure:e5ff4dfaa6f8c0c2c28189f169e2fa89e622bf88e1563b78b7daa8792d416c1d`

The child traceback ends at
`scripts/run_procedural_causal_audit_7b.py:285`:

```text
RuntimeError: Live prompt is over context for
f0106e0a9f6ea5889b2aeda8ad24828bf208296aa879fd3c50704f2b19e0e65a
```

The call path is
`run_appworld_train_causal_gate_7hr.py:_run_paired` ->
`run_procedural_causal_audit_7b.py:_run_condition`.

## First Causal Divergence

VERIFIED:

- O05 preflight used `_appworld_messages_from_example()`, which reconstructs
  observations from stored `example.state_text`.
- O06 used `build_live_appworld_messages()` with observations returned by the
  fresh AppWorld replay bridge.
- O05 marked the state scoreable with static base/raw token counts
  `18,957/38,075`; its raw prompt SHA was
  `c19ceb6f729bc64cbebe264a4a45e9a4512da927a7bc7084927c20d63812d8df`.
- O06 completed the same state's live bare condition with `23,809` prompt
  tokens and prompt SHA
  `8746a3737ee9249a6209ee2922b1f85e48f68120df0b66a8c229271195a7ecdb`.
  The live bare prompt was 4,852 tokens longer than the preflight base.
- The O06 runtime computed the raw live prompt, found no positive remaining
  context, and raised before generation. No raw-condition result file exists.

INFERENCE:

- The static memory increment was `19,118` tokens. Adding it to the observed
  live base gives a projected raw prompt of `42,927`, or `1,967` tokens over
  the limit. This arithmetic explains the observed branch, but the exact live
  raw token count was not persisted before the exception and was not
  regenerated in this records-only task.
- The underlying mechanism is that actual replay observations are longer than
  their stored historical-state render for this state. The verified contract
  defect is the use of different message sources for scoreability preflight
  and formal live generation.

UNVERIFIED:

- Whether additional O06 states would also cross the live context limit.
- Whether treating live-over-context as a missing panel row would preserve the
  intended causal-panel semantics. That is a scientific-contract decision,
  not an infrastructure retry.

## Partial O06 Evidence

- Last fully paired state count: `178`.
- Last paired label counts: `HARMFUL=13`, `NEUTRAL=103`, `POSITIVE=62`.
- Atomic condition outputs: `357` files, 7,658,591 bytes.
- Replay-missing records: `3` files, 4,920 bytes.
- The 357th file is the bare condition for the failing state; the raw condition
  has no output file.
- There is no sealed O06 result, valid output manifest, completed one-demo
  causal panel, policy teacher, writer/reader training, or one-demo evaluation.

Raw outputs contain credentials/JWT-bearing observations and remain
Lambda-only. Git records contain only hashes, counts, identities, and typed
descriptions.

## Attempts And Runtime State

- Attempt event rows: `86`.
- Unique attempts: `43`.
- Complete: `42`; failed/interrupted: `1`; open: `0`.
- Formal parent/orchestrator: exited.
- Formal tmux `exp037a_14j_formal`: absent.
- Read-only status bridge: present at last inspection.
- H100: `0%` utilization, `0/81,559 MiB`; no formal GPU process.
- D06B: PASS; D08B: PASS; D22: PASS; F03: not reached.

The monitor independently published terminal state
`TERMINAL_INFRASTRUCTURE_FAILURE` at commit
`5534c248c612bb7cfc968d97115b15abcdc745cc` on
`monitor/exp037a-14j-live`.

## Safe Next Action

Do not retry or resume 14j. The failure is deterministic and the frozen
completion marks it non-recoverable. A safe next task is a bounded,
user-reviewed source repair that makes O05 scoreability use the exact live
replay message semantics (or explicitly revises panel admission semantics),
adds mismatch regression coverage, creates a new source/config/contract/run
identity, and obtains fresh authorization. No such repair was implemented.

## Publication State

No executable source or scientific configuration changed in this records-only
publication. The exact executed code is already pushed and archived at
`archive/exp037a-r10-launch-source-0e40155`. This publication adds only
Git-safe research records; the raw formal root remains unchanged.
