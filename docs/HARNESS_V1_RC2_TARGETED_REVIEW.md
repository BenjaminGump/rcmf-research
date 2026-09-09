# Harness V1 RC2 Targeted Independent Review

Status: `STOP_TYPED_FAILURE_GATE_INCOMPLETE`

Verified UTC: `2026-09-09T06:28:44Z`

## Authority

- RCMF review base/previous records:
  `17f0818e9ae5939e44d4ec26b64b21cd7a9b1c2a`.
- RCMF previous review source:
  `ca93ed71a747c5c1ba0cac3d2659636ca936f092`.
- Harness RC2 source:
  `d82028ae1684858d10899ce2ef5c2b50bf2e7b6b`.
- Harness RC2 records:
  `9b2175d2c8d7617f677c24913577b5f24dce8f3e`.
- Harness RC2 archive:
  `archive/harness-v1-rc2-source-d82028a`.

The RC2 records commit is the direct child of the source commit and changes
only `docs/` and `research/`. The archive resolves exactly to the source. The
RC1 archive remains fixed at
`ec953717d233f3958d8c6b3180b0b4a785312ad8`, and RC2 retains RC1 ancestry.
Both fresh RC2 checkouts were clean and `git fsck --full` passed.

## Targeted Results

### F01: task population closure

Direct calls to `validate_finalized_result()` rejected every malformed fixture:

- duplicate result task ID;
- omitted task;
- extra task;
- reordered task under `EXACT`;
- task-result hash mismatch;
- task-result status mismatch;
- duplicate IDs in the authorized task manifest;
- an actual task row absent from `per_task_results`;
- a `per_task_results` entry with no actual task row.

The explicit `ORDER_INDEPENDENT` mode accepted a reordered result only when the
exact authorized task set remained present. Producer-set `complete=true` did
not bypass any of these checks.

### F02: run/result identity closure

Finalization rejected independently mutated result `run_uuid`, `track`,
`harness_source_sha`, `benchmark_lock_sha256`, `method_lock_sha256`, and
`task_manifest_sha256`. It also rejected a task row from another run and a task
row bound to another task manifest. Validation used the supplied run manifest
and canonical content hashes, not path or filename identity.

### F03: typed failures

Otherwise complete fixtures containing one `METHOD_FAILURE`,
`ENVIRONMENT_FAILURE`, `INVALID_OUTPUT`, or `TIMEOUT` failed finalization and
were comparison-ineligible with, respectively:

- `EXECUTION_METHOD_FAILURE`;
- `EXECUTION_ENVIRONMENT_FAILURE`;
- `EXECUTION_INVALID_OUTPUT`;
- `EXECUTION_TIMEOUT`.

All failure rows retained null reward and success. Separate completed fixtures
with raw reward zero and with `binary_success=false` remained valid benchmark
outcomes.

## Blocking Evidence-Flow Bypass

A raw result mapping is correctly rejected by aggregation with
`UNVALIDATED_RESULT_EVIDENCE`. However, RC2 publicly exports the freely
constructible `ValidatedResultEvidence` dataclass. The aggregator treats any
instance of that class as semantically finalized and does not recheck its task
closure.

The independent reproducer directly constructed this exported class around a
raw manifest containing a duplicate two-row task population, set empty
comparison reasons, and passed two such instances to
`evaluate_comparison_eligibility()`. The result was:

```text
comparison_eligible = true
comparison_reasons = []
```

No finalization call occurred. This is a concrete in-process API path, not a
filename or serialization trick. It contradicts the RC2 handoff statement that
only semantic finalization can emit comparison-eligible evidence.

Before final Harness V1 freeze, Thread B should make evidence construction
factory-only/guarded or give the aggregator a verifiable closure proof, remove
the public unguarded construction path, and add a negative regression that
attempts this exact bypass. No lifecycle redesign is required.

## Regression And Neutrality

- Fresh Harness RC2 full suite: `62 passed`, zero failures.
- Independent closure reproducer: `25 passed`, `1 failed`; the only failure was
  the public-constructor bypass above.
- `protocol.py`, `lifecycle.py` (absent in both compared trees), and
  `tests/test_protocol_lifecycles.py` are byte-unchanged from RC1 source to RC2.
- No RCMF or method-name branch appears in RC2 validation/aggregation.
- ReAct, TTR, RCMF, ReMe, MemGen, and delta-Mem lifecycle semantics remain
  unchanged.

## RCMF Compatibility

The existing RCMF focused suite passed `38/38`, covering H1 same-run versus
sealed-upstream closure, H2 executor registry proof, H3 dataset semantic
identity, and the RCMF plugin. A separate execution through the actual RC2
`HarnessLifecycleRunner` accepted the RCMF plugin, preserved the harness-owned
base prompt, bound the method-owned checkpoint/field, performed two episode
resets, and reported no runtime retrieval or raw-memory prompt use.

No RCMF source change was required. The full RCMF suite was not rerun because
the focused compatibility suite was clean and this branch changes records only.

## Decision

F01, F02, and F03 are directly repaired, method neutrality is preserved, and
RCMF compatibility passes. Final freeze remains blocked because an exported
typed-evidence constructor can bypass the finalizer and make a malformed task
population comparison-eligible.

`STOP_TYPED_FAILURE_GATE_INCOMPLETE`

NO RCMF OR BASELINE TRAINING WAS RUN

NO STANDARDIZED BENCHMARK RESULT WAS PRODUCED

NO FINAL HARNESS V1 WAS FROZEN

FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED
