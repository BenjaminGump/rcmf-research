# EXP-037A-R14B O08 Count-Ownership Repair and 14l Continuation Freeze

Date: 2026-09-07

Decision: `READY_FOR_14L_CONTINUATION_AUTHORIZATION`

Scientific result: `NOT_EVALUATED_CONTINUATION`

## Scope

R14B repairs the stale three-demo scoreable-count requirement that terminated
the sealed 14k run at O08. It validates the repaired O08 production path and
exactly one O09 training unit, audits O08-O19 for the same defect class, and
freezes a new provenance-safe continuation at the parent O07/O08 boundary.
It does not resume 14k or authorize/launch the continuation.

## Git And Identities

- Starting branch: `research/v6-rcmf-exp037a-14k-o08-failure-records`
- Starting SHA: `d7c61ad2850adf03cde8652576eb024cddcd062c`
- Repair branch: `research/v6-rcmf-exp037a-o08-count-ownership-continuation`
- Implementation SHA before final charter: `ffd6a22fc938f2737faafc1df4e6da5e58a51518`
- Frozen continuation launch source:
  `95b355ab9f7419d86fb6045bcdc27ea8e27604bc`
- Frozen archive: `archive/exp037a-r14b-launch-source-95b355a`
- Parent source: `004f866647cfabb38a141b88e6d83821df88c403`

## Verified Repair

The previous shared preparation path treated 366 train and 98 heldout paired
states as universal structure. Those numbers are exact outcomes of the 3D
positive control, not preregistered one-demo outcomes.

The repaired typed policy is:

- 3D: `exact_reproduction`, still requiring exactly 366/98 and leaving D06B
  unchanged.
- 1D: `sealed_upstream_outcomes`, deriving train/heldout populations from
  strict-valid O06/O07 artifacts and enforcing the fixed 29/8 task split,
  complete bare/raw pairs, unique IDs, typed missingness, panel completion,
  O06/O07 identity, and 401/98/499 memory structure.

No production success criterion contains the observed 324/83, 407, or
120/247/40 values. Synthetic tests exercise multiple valid dynamic
populations. Unknown or missing policies fail closed.

## Parent Closure

- Parent UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`
- Parent root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`
- Parent config SHA256:
  `f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69`
- Parent contract SHA256:
  `eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6`
- Parent artifacts: 922
- Parent closure SHA256:
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`
- Parent manifest file SHA256:
  `617e2ccade182650a8087fbc81a5267683de03dd69b415f2f0d85b6c17e376ab`

The boundary validator independently revalidated D22 and O00-O07 against the
parent source/run/config/contract and dependency hashes. D22 is exact
`THREE_DEMO_REPRODUCTION_PASS`; O06 and O07 are strict-valid and agree on all
407 state IDs. Parent authorization identities validate only as historical
full-run authorization and explicitly do not authorize 14l.

The failed parent O08 had no valid output manifest. Its partial
`memory_provenance.jsonl`, `rcmf_source_cache.pt`, and
`key_payload_shuffle_manifest.json` were recorded as prohibited and were not
used. Parent closure was identical before and after both diagnostics.

## O08 Diagnostic

Root:
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r14b_o08_20260907_003`

The actual scheduler, stage runner, preparation script, preflight, smoke,
zero-cache path, canonical output-manifest writer, and strict validator all
completed. C00, C01, and O08 passed. The O08 stage took 270.380 seconds and
its scientific substep manifest reports 267.185 seconds.

- Paired states: 407
- Model train / heldout validation: 324 / 83
- Labels positive / neutral / harmful: 120 / 247 / 40
- Static over-context / replay-semantic missing: 11 / 10
- Training units per epoch: 516
- Fixed task split: 29 / 8
- Memory counts: 401 / 98 / 499
- Optimizer steps: 0
- Scientific checkpoints: 0
- O08 output count: 420
- O08 output-manifest SHA256:
  `22f26fa90cfefdda246cc30851f053fabe75a897f07cd3dda1baeb2a690c6613`

Key output hashes include the full-bank manifest `eb016db6...`, source cache
`611b3af2...`, training units `b70072df...`, state-query shuffle
`34a4b361...`, and zero-cache summary `8732c68a...`. The complete path, size,
and SHA256 list is in `o08_output_manifest.json`.

Two earlier diagnostic attempts failed closed before scientific optimization:
`_001` exposed continuation runtime-layout initialization assuming full-run
shared artifacts; `_002` exposed an R14B smoke lookup using a noncanonical
count key. Both were repaired with focused regressions. Only `_003` is the
passing O08 production-path diagnostic.

## O09 One-Unit Smoke

Root:
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r14b_o09_one_unit_20260907_001`

- Completed unit:
  `e1:appworld:trace:cf6abd2_1:step:10:line:510::correct`
- Full process wall: 18.826 seconds
- Training-unit time: 1.811 seconds
- Backward count this attempt: 1
- Optimizer steps this attempt: 1
- Loss: `0.0025804713368415833`
- Finite loss/gradients/updated parameters: yes
- Nonzero writer and reader gradients: yes
- Qwen frozen: yes
- Selector frozen: yes
- Optimizer state nonempty: yes

The diagnostic checkpoint SHA256 was
`1b323072864cc0dd8840412334d759b9c70ce5136cca1a90adc72b5f06f4e459`.
It and its latest-checkpoint pointer were deleted after hash recording. The
remaining summary is diagnostic-only and cannot initialize formal science.

## Downstream Audit

The O08-O19 audit classified 24 occurrences:

- `DYNAMIC_ARM_OUTPUT`: 4
- `THREE_DEMO_REPRODUCTION_ONLY`: 15
- `TRUE_SHARED_INVARIANT`: 3
- `UNREACHABLE_HISTORY`: 2
- unresolved reachable defects: 0

No production one-demo exact outcome constant remains. The structural 98
heldout-parent memories remain enforced and are distinct from the 3D-only 98
completed heldout paired states.

## Tests

All commands used process-start `PYTHONHASHSEED=25101`.

- Local focused: 88 passed, 1 skipped in 71.15s.
- Lambda/CUDA focused: 89 passed in 28.12s.
- Local full: 987 passed, 3 skipped in 93.25s.
- Lambda/CUDA full: 990 passed in 37.19s.

The local suite emitted only a pytest cache-write warning; a writable explicit
base temp directory was used and no assertion failed. CUDA checkpoint/resume
coverage passed on Lambda.

## Frozen 14l Package

- UUID:
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_001`
- Pipeline config SHA256:
  `3a37b18611b23db7335a66bb8b51e80db116bb7b6df8ee7ac66f6b1b06d283e4`
- Contract SHA256:
  `3a30f1ffba28f992d6c1dfa5821abb1f64ba3b6cfdfedd64a340f48654012ed9`
- Stage-scope SHA256:
  `3581cf1caded5a5aeba300b538117a4771afd64eb33b5c423c033fa1ac1397d7`
- Parent-manifest SHA256:
  `617e2ccade182650a8087fbc81a5267683de03dd69b415f2f0d85b6c17e376ab`
- Preflight artifact-index SHA256:
  `60db055b01f25f514d5aa48a06a907f1212d1062cbf6efc7289ab791c90c48b5`
- Authorization-request SHA256:
  `23c6fcebddac96db65a59f11ef6418651ddef73f0e57a91912fa6133d2eadacf`
- Authorization: `NOT_AUTHORIZED`

The stage graph is exactly C00/C01, O08-O19, and F00-F03. It contains no S,
D, or O00-O07 stage. The root has preflight only: no runtime authorization,
formal stages, formal attempts, or scientific outputs.

## Runtime And Restart

- Expected wall: 9.5h
- Conservative wall: 20.0h
- Expected H100 active: 9.3h
- Conservative H100 active: 19.5h
- Expected / conservative storage: 5 / 10 GiB
- Proposed anomaly cap: 32h
- Monetary cost: `MONETARY_COST_NOT_AVAILABLE`

The cap follows `max(2 * 9.5, 1.25 * 20) = 25h`, rounded upward to 32h.
Because conservative runtime exceeds 18h, explicit user authorization is
required. C00/C01 are cheap and repeatable, O08 always rebuilds under the new
source, O09/O10 use strict atomic checkpoint resume, and completed continuation
stages may skip only after new-run identity/dependency/output hash validation.

## Interpretation

### VERIFIED

- The stale count-ownership defect is repaired without encoding 324/83.
- Parent O00-O07 and 3D scientific behavior changes are zero.
- O08 and one O09 unit pass through production code.
- Parent 14k remains byte-identical under the 922-file closure.
- The continuation package is complete, preflight-only, and unauthorized.

### INFERENCE

- Runtime estimates scale parent 3D training and heldout timings by the sealed
  516/576 training-unit and 83/98 heldout-state ratios. The 57-task correct and
  shuffle dev measurements are retained without scaling.

### UNVERIFIED

- Complete O09-O19 behavior and the final 3D-vs-1D scientific comparison are
  not evaluated. A positive or borderline continuation result will require a
  later single-source fresh full confirmation before a paper-level claim.

## Deviations

- Two bounded O08 diagnostics failed closed and motivated non-scientific
  continuation-layout/key fixes before `_003` passed.
- The Windows `apply_patch` helper could not update existing files; guarded
  exact PowerShell replacements were used, followed by diff checks and full
  local/Lambda suites.
- No reliable Lambda hourly rate was available, so monetary cost was not
  guessed.

No long continuation run was launched.
