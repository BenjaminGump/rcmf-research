# EXP-037A-R14B Structured Handoff

Timestamp UTC: 2026-09-07T06:55:31Z

Decision: `READY_FOR_14L_CONTINUATION_AUTHORIZATION`

## Git

- Starting SHA: `d7c61ad2850adf03cde8652576eb024cddcd062c`
- Branch: `research/v6-rcmf-exp037a-o08-count-ownership-continuation`
- Implementation SHA: `ffd6a22fc938f2737faafc1df4e6da5e58a51518`
- Launch source: `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- Archive: `archive/exp037a-r14b-launch-source-95b355a`
- Records SHA: the records-only commit containing this handoff
- Formal execution must use the launch source, not the records commit.

## Parent Boundary

- Parent UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`
- Parent source: `004f866647cfabb38a141b88e6d83821df88c403`
- Parent root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`
- Parent config SHA256:
  `f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69`
- Parent contract SHA256:
  `eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6`
- Parent artifact count: 922
- Closure SHA256:
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Parent manifest SHA256:
  `617e2ccade182650a8087fbc81a5267683de03dd69b415f2f0d85b6c17e376ab`
- Valid boundary: after O07, before O08.
- D22 and O00-O07 strict validations pass. D22 is
  `THREE_DEMO_REPRODUCTION_PASS`; O06/O07 agree on 407 states.
- Parent O08 has no valid manifest. Its three partial files are prohibited and
  were not imported.

## Repair

- 3D policy: `exact_reproduction`, exact 366/98 retained.
- 1D policy: `sealed_upstream_outcomes`, no exact paired-state count.
- Sealed one-demo O06-derived counts: 324 train, 83 heldout, 407 total;
  labels 120 positive, 247 neutral, 40 harmful.
- Shared structural counts remain 29/8 tasks and 401/98/499 memories.
- No production 324/83/407/120/247 success constants exist.
- O00-O07, 3D, selector, panel, prompt, model, writer/reader, losses, epochs,
  and evaluation behavior changes: zero.

## Diagnostics

O08 root:
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r14b_o08_20260907_003`

- C00/C01/O08 strict PASS.
- O08 wall: 270.380s; 420 hashed outputs.
- O08 output-manifest SHA256:
  `22f26fa90cfefdda246cc30851f053fabe75a897f07cd3dda1baeb2a690c6613`
- Zero-cache finite, optimizer/backward count zero, no scientific checkpoint.
- Parent closure unchanged.

O09 root:
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r14b_o09_one_unit_20260907_001`

- Exactly one unit, one backward, one optimizer step.
- Loss `0.0025804713368415833`; all gradients and parameters finite.
- Writer/reader gradients nonzero; Qwen/selector frozen.
- Diagnostic checkpoint SHA256 before discard:
  `1b323072864cc0dd8840412334d759b9c70ce5136cca1a90adc72b5f06f4e459`.
- Updated diagnostic checkpoint and pointer deleted after hash recording.
- Parent closure unchanged.

The O08-O19 stale-contract audit passed: 24 classified occurrences, no
unresolved reachable defect, no production one-demo exact outcome constant.

## Tests

All tests used process-start `PYTHONHASHSEED=25101`.

- Local focused: 88 passed, 1 skipped.
- Lambda/CUDA focused: 89 passed.
- Local full: 987 passed, 3 skipped.
- Lambda/CUDA full: 990 passed.

## Continuation Package

- UUID:
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Config SHA256:
  `3a37b18611b23db7335a66bb8b51e80db116bb7b6df8ee7ac66f6b1b06d283e4`
- Contract SHA256:
  `3a30f1ffba28f992d6c1dfa5821abb1f64ba3b6cfdfedd64a340f48654012ed9`
- Stage-scope SHA256:
  `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`
- Artifact-index SHA256:
  `60db055b01f25f514d5aa48a06a907f1212d1062cbf6efc7289ab791c90c48b5`
- Authorization-request SHA256:
  `23c6fcebddac96db65a59f11ef6418651ddef73f0e57a91912fa6133d2eadacf`
- Scope: C00/C01, O08-O19, F00-F03 only.
- Authorization: `NOT_AUTHORIZED`; no runtime authorization, stages, attempts,
  or scientific outputs exist.

## Runtime

- Expected/conservative wall: 9.5h / 20.0h.
- Expected/conservative H100 active: 9.3h / 19.5h.
- Expected/conservative storage: 5 / 10 GiB.
- Proposed cap: 32h.
- Monetary cost: `MONETARY_COST_NOT_AVAILABLE`.
- Explicit user approval is required because conservative runtime exceeds 18h.

## Interpretation

VERIFIED: the defect is repaired, bounded production paths pass, the parent is
unchanged, and the package is provenance-safe and unauthorized.

INFERENCE: runtime scaling uses measured 14k downstream anchors adjusted by
516/576 training units and 83/98 heldout states; 57-task dev timings are not
scaled.

UNVERIFIED: O09-O19 scientific execution and final 3D-vs-1D comparison. A
positive or borderline continuation result requires later single-source fresh
confirmation before a paper-level claim.

## Stop Conditions

Do not launch without a new authorization bound to launch source, UUID, root,
config SHA, contract SHA, parent manifest SHA, stage-scope SHA, 32h cap, and
the continuation-only scope. Do not modify or resume 14k. Do not use parent O08
partials. Do not start any follow-on experiment automatically.
