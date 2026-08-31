# EXP-036B Structured Handoff

## Outcome

- Status: STOPPED_BEFORE_FORMAL
- Stop gate: runtime_preflight_42_hour_cap
- Run UUID: rcmf_appworld_testnormal_final_13b_20260831_001
- Active branch: research/v5-rcmf-appworld-testnormal-deterministic
- Starting SHA: 69a218a212f05709ca4c278f1ae14a89b44031a4
- Root-cause commit: 9aaa292a786333c33e7f5f0c836d9ce41c1c7c75
- Initial determinism implementation: a71368d66f54c88525c3f6e8e5e161cae09cf902
- Frozen mode commit: 5eecc08139d4572913f91578944131fb8b05c47f
- Fresh-process smoke implementation: 98833e7c40d1d693b146a3192937250cb953c479
- Audit exporter: 5013d0a965010070c9f79adfa0291b98778d0498

## Immutable References

- Archive: archive/exp036a-determinism-stop-69a218a2
- Annotated tag: exp036a-determinism-stop-verified-69a218a2
- Test-Normal: 168 tasks, SHA
  990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83
- Final determinism mode: hash_seed_only
- Mode logical SHA:
  d43d46e46ce8beef7c2b5946c1657b84e94d4a0783b4180f45186beedfc9eb5c
- Canonicalizer: disabled
- Evaluation seed and PYTHONHASHSEED: 25101

## Completed Work

1. Reverified all frozen BEST/FULL1D package, field, prompt, shuffle, evaluator,
   and task identities.
2. Proved the EXP-036A difference was equal-set representation order only.
3. Passed B0 and FULL1D-S fresh-process Stage-1 probes.
4. Passed full local and Lambda test suites.
5. Ran 15 complete smoke trajectories in 15 fresh Python processes.
6. Passed all five exact repeat comparisons.
7. Froze the 168-task, five-condition formal manifest before formal work.
8. Stopped with 0/840 formal rows because conservative total runtime was
   50.3493h, above the approved 42h.
9. Exported 575 Git-safe smoke step rows and passed the secret scanner.
10. Final test suites passed locally (778 passed, 2 skipped) and on Lambda
    (780 passed).

## Attempts

- Attempts: 22
- Failed: 1 (exp036b-smoke-finalize-001)
- Open: 0
- The failed finalizer is the expected enforcement of the runtime gate; all
  smoke rows and comparisons were already atomically complete.

## Runtime

- Expected formal: 29.7593h
- Conservative formal: 45.0993h
- Expected complete total: 32.8593h
- Conservative complete total: 50.3493h
- Approved total: 42h
- Probe plus smoke H100-active attempt time: 0.7817h
- Ledger wall span: 1.3898h

## Not Run

- Formal B0/BEST-C/BEST-S/FULL1D-C/FULL1D-S: 0/840
- Performance contrasts and uncertainty: NOT_RUN
- Efficiency, scaling, compilation, TTFT, serving state: NOT_RUN
- Numerical reversibility: NOT_RUN
- Follow-on experiments: NOT_RUN

## Artifacts

- Raw Lambda:
  /lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13b_20260831_001
- Machine summary:
  research/results/exp036b_appworld_testnormal_final/summary.json
- Runtime preflight:
  research/results/exp036b_appworld_testnormal_final/runtime_preflight.json
- Report:
  research/results/EXP_036B_APPWORLD_TESTNORMAL_FINAL.md
- Audit index:
  research/audits/rcmf_appworld_testnormal_final_13b_20260831_001/index.json
- Audit logical SHA:
  a53c597eeb0d445e6bc901d74bb3c4f1fd0602202990b8b78276d33c53ef58f7
- Audit file SHA:
  d4d4fe7b793e8559a3b9d66c683123c889014e9ee411a8b72d4f94c17ffa8eb3
- Secret scan: passed.

## Claims

**VERIFIED:** Hash-seed-only rendering is exact on the preregistered probe and
final complete-path smoke; model-visible observations remain raw and evaluator
state is unchanged.

**VERIFIED:** No optimizer, backward pass, model update, field change, prompt
change, formal Test-Normal row, efficiency run, or reversibility run occurred.

**INFERENCE:** A renewed authorization above the current conservative estimate
would allow the already frozen formal manifest to run without another
scientific change.

**UNVERIFIED:** Every formal Test-Normal and deployment-efficiency result.

No Lambda termination was performed. Review the runtime authorization before
any resume.
