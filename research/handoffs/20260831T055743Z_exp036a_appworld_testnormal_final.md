# EXP-036A Structured Handoff

## Identity

- Repository: `BenjaminGump/rcmf-research`
- Active branch: `research/v5-rcmf-appworld-testnormal-final`
- Starting SHA: `4aa0fd89b2b63d5a9bcbf1e6b18395e0a14e847b`
- Archive branch: `archive/exp035a-component-swap-4aa0fd89`
- Annotated tag: `exp035a-component-swap-verified-4aa0fd89`
- Run UUID: `rcmf_appworld_testnormal_final_13a_20260831_002`
- Evaluation seed: `25101`
- Scientific source SHA: `61af5066d9642984384bd2e2cfda73a0a1612daf`
- Final Git SHA: the commit containing this handoff

## Outcome

Decision: `STOPPED_BEFORE_FORMAL` at
`complete_path_smoke_determinism`.

Formal Test-Normal: `NOT_RUN` (`0/840` trajectories).

Efficiency/scaling: `NOT_RUN`.

Numerical reversibility: `NOT_RUN`.

The stop is an infrastructure/reproducibility gate, not a positive, negative,
or inconclusive performance result.

## Frozen Inputs

- AppWorld: legacy `0.1.0`
- root: `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`
- prompt profile: `full_demo_first_only`
- one-demo messages SHA:
  `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- retained demo SHA:
  `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- ordered Test-Normal task count: 168
- ordered task-list SHA:
  `990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83`
- BEST selector:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- BEST writer/reader:
  `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- BEST field:
  `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- FULL1D selector:
  `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`
- FULL1D writer/reader:
  `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`
- FULL1D field:
  `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`
- shuffle:
  `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`

## Preflight

- Exact package hashes: passed.
- Test-Normal count/list identity: passed.
- ground-truth model-input leak count: 0.
- memory-parent overlap: 0.
- retained-demo overlap: false.
- no retrieval/top-k/runtime memory scan/raw-memory prompt: passed.
- formal manifest frozen before outcomes: passed.
- focused/full tests passed locally and on Lambda.

## Smoke

Tasks: `3d9a636_1`, `3d9a636_2`.

Completed: 15 trajectories, 624 steps.

- B0 repeat: failed; first observation divergence at step 18.
- BEST-C repeat: passed.
- BEST-S repeat: passed.
- FULL1D-C repeat: passed.
- FULL1D-S repeat: failed; first observation divergence at step 19.

At each failure, the first divergent observation differed only in Python set
`repr` ordering while the emitted response and executed code still matched.
Later prompts and token IDs then diverged. The contract required exact
equality, so no formal launch was permitted.

Task-row wall time: 2,189.576 seconds (0.6082 hours). Attempt span:
2,239.285 seconds (0.6220 hours).

## Attempts

Clean `_002` run: 3 unique attempts, 1 failed, 0 open.

- `exp036a-prepare-001`: complete
- `exp036a-smoke-001`: failed, determinism gate
- `exp036a-stop-export-001`: complete

Superseded `_001`: 4 unique attempts, 2 failed, 0 open. It preserved a
selector-SHA transcription correction and a wrongly ordered efficiency pilot;
it produced no task trajectory and no scientific result.

## Tests

- Pre-smoke local focused: 14 passed.
- Pre-smoke local full: 763 passed, 1 skipped.
- Pre-smoke Lambda focused: 14 passed.
- Pre-smoke Lambda full: 764 passed.
- Final local focused: 15 passed.
- Final local full: 764 passed, 1 skipped.
- Final Lambda focused: 15 passed.
- Final Lambda full: 765 passed.

## Artifacts

Raw root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13a_20260831_002`

Raw root at finalization: 296 MiB, 1,367 files.

Git-safe bundle archive:
`/lambda/nfs/rcmf-persist/quarantine/exp036a_git_safe_stop_export_v3.tar.gz`

Bundle SHA256:
`f3d4d4203efc7dba2737b795870aaafab5cd06adcaec2830c83268fe425e979c`

Audit index:
`research/audits/rcmf_appworld_testnormal_final_13a_20260831_002/index.json`

Audit index file SHA256:
`2a5baa88f2fe7579ed3ac5676724a1920c7f5833f27443151ee0575f496148ed`

Result summary:
`research/results/exp036a_appworld_testnormal_final/summary.json`

Secret scan: passed; 0 registered secret/JWT leaks in Git-safe output.

## Deviations

1. A 65-character expected selector SHA was corrected to the verified
   64-character immutable SHA. The false original diagnosis was itself
   append-only corrected.
2. A superseded `_001` efficiency pilot ran before formal execution, contrary
   to the requested phase order. It failed without output. `_002` enforces
   formal-before-efficiency and is the scientific run.
3. Historical-cache numerical drift is audit-only after exact raw text,
   tokenization, and provenance identity; no cache or model was rewritten.

## Evidence Labels

VERIFIED: identity checks, leakage checks, tests, 15 smoke trajectories,
set-order divergences, mandatory stop, zero formal rows, and artifact hashes.

INFERENCE: process-level Python hash/set iteration state likely caused the
different set rendering.

UNVERIFIED: formal performance, scaling, TTFT, serving-state measurements,
numerical reversibility, and whether a future newly preregistered deterministic
harness would pass.

## Operational State

At handoff collection, H100 utilization was 0%, GPU memory use 0 MiB, and no
EXP-036A Python process remained. The `exp036a_smoke_002` tmux session was an
idle shell. Historical tmux sessions and the existing Jupyter service remain.

The instance is safe to terminate with respect to EXP-036A artifacts and
compute. It was not terminated automatically.

## Stop

Do not rerun with a new seed, normalize observations, change
`PYTHONHASHSEED`, launch formal Test-Normal, run efficiency/reversibility, or
start another experiment without a new reviewed protocol.

