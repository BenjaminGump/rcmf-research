# EXP-037A-R10 Whole-Pipeline Logic Audit

Date: 2026-09-04

Decision: `READY_FOR_REAUTHORIZATION`

Scientific result: `NOT_EVALUATED`

## Scope

R10 audited the actual reproducible pipeline from S00 through F03, repaired
five verified execution/provenance defects, and froze a fresh unauthorized
14j package. It did not launch a formal run, create runtime authorization,
run S05 or any scientific stage, or change the RCMF scientific method.

The accepted starting records state was
`aee326eccc43256352a5ff773b8d1f49a8d124c4`. The tested and frozen launch
source is `0e4015547da45802cc7b6ff3a9b92adce73077fc`, archived at
`archive/exp037a-r10-launch-source-0e40155`.

## Verified Defects And Repairs

| ID | Reachable defect | Smallest repair | Scientific change |
|---|---|---|---:|
| R10-D1 | Successful stage manifests sealed only `stage_result.json`; a changed checkpoint, field, or summary could remain resume-eligible. | Every formal stage now resolves and SHA-seals its actual resume-critical output set. | 0 |
| R10-D2 | `latest_checkpoint.json` recorded a checkpoint SHA but the training path deserialized the path before verifying SHA, canonical root, or epoch-boundary kind. | Validate pointer path/root/SHA/completed-unit/epoch/boundary before `torch.load`. | 0 |
| R10-D3 | Strict `load_state_dict` did not prove restored writer/reader values matched the checkpoint's stored module hashes. | Recompute and compare both module hashes immediately after restore. | 0 |
| R10-D4 | D06B and D22 manual prior-stage checks did not independently require UUID/root/config/contract identity. | Gate-level prior checks now use full strict formal identity. | 0 |
| R10-D5 | 401/499 field validators omitted parts of count, binding, shape, checkpoint, finite-value, and add-only provenance validation. | Fail closed on exact 401/499 identities, 401+98 composition, A/B and shuffled A/B shape, checkpoint hash, payload multiset, and no-retraining evidence. | 0 |

The stage completion validator was not weakened. The R9 CPU/CUDA RNG repair,
optimizer state handling, writer/reader recipe, and checkpoint cadence remain
unchanged.

## Verified Safe

- The graph contains 60 formal stages: 11 shared, 25 3D, 20 conditional 1D,
  and 4 final-reporting stages. All use the same production stage runner and
  strict manifest writer/validator contract.
- D22 non-PASS is a valid scientific branch: O00-O19 stay skipped and final
  3D-only reporting remains eligible. A failed executable stage instead
  terminates with scheduler/orchestrator evidence and cannot fabricate a
  scientific conclusion.
- O09-O19 and D09-D20 dispatch through the same implementation. The resolved
  arm diff remains prompt-dependent only: 3D `full_demo`, 1D
  `full_demo_first_only`.
- The no-deployable-checkpoint path is explicit through selection, 401/499
  field handling, dev summaries, D21/D22, and 3D-only final reporting.
- All 29 formal `torch.load` sites were classified: 25 `SAFE_AS_IS`, two
  training loads safe after R9 canonicalization, and two live/evaluation
  module loads `SAFE_BUT_WASTEFUL`. None is unclassified.
- Hard-link and exact-copy consumers inspected are read-only; no downstream
  in-place mutation of a linked immutable source was found.
- Authorization, hard deadline, UUID/root/source/config/contract checks,
  dependency completion hashes, retry exit policy, and stale-output rejection
  remain fail closed.
- The production artifact resolver found every declared file for all 23
  `passed=true` 14h stages and content-addressed each one. The D10
  `passed=false` completion is correctly classified as attempted/failed and
  is not resume-eligible.

## Pipeline Coverage

The full machine-readable dependency and artifact tables are in
`stage_dependency_map.json`, `artifact_contract_map.json`, and
`coverage_matrix.json`.

| Coverage type | Result |
|---|---:|
| All-stage strict synthetic contract coverage | 60/60 |
| Real production scheduler/subprocess smoke | S00-S04, 5/5 |
| Second-run hash-valid resume skips | 5/5; zero subprocess executions |
| Formal 14h completed stages | 23 |
| Formal 14h attempted failure | D10, 1 |
| Formal 14h not exercised | 36 |

The real S00-S04 smoke at the frozen source completed in 15.944 seconds,
stopped before S05, and used no scientific H100 time. Each process exited 0;
each output manifest and completion passed strict validation. A copied
run-UUID mutation failed strict validation.

## ChatGPT Leads Disposition

1. `latest_checkpoint.json`: confirmed and fixed by R10-D2.
2. Device-sensitive loads: partially valid; R9 fixed the only demonstrated
   correctness bug, and all remaining formal loads are classified.
3. D11-D22 coverage: partially valid residual integration gap; hardened by
   explicit contracts and synthetic branch coverage, but only a formal run can
   exercise all expensive behavior.
4. 1D post-training parity: already handled by generic dispatch and the
   prompt-only resolved-config difference.
5. No-selected-checkpoint path: already handled as an explicit branch.
6. Immutable hard links: already handled by read-only consumers; no unsafe
   mutation path was found.

## Validation

All tests used process-start `PYTHONHASHSEED=25101`.

| Environment | Suite | Result |
|---|---|---|
| Local `appworld_env` | focused R10 | 16 passed in 39.76 s |
| Local `appworld_env` | full | 931 passed, 3 skipped in 78.34 s |
| Lambda `rcmf-py311` | focused R10 with CUDA | 16 passed in 15.83 s |
| Lambda `rcmf-py311` | full with CUDA | 934 passed in 33.27 s |

The test suite covers pointer SHA/root/boundary validation, restored module
hashes, exact scientific artifact sealing, mutation rejection, all 60 stage
contracts, strict prior-stage identity, 401/499 field invariants, authorization
rejection, conditional branches, resume, R9 cross-process CUDA equivalence,
and the actual sealed D09 one-unit diagnostic.

## Residual Risks

- D11-D22 and O00-O19 have not all executed in a complete fresh formal run.
  Static contract tracing, branch fixtures, 60-stage synthetic coverage, and
  strict output sealing reduce this risk but cannot replace the authorized
  run itself.
- Abrupt host loss can leave `scheduler.lock` for operator triage. The lock is
  fail closed, and process/heartbeat validation prevents duplicate launch.

Neither residual requires a scientific method change. No critical validation
gap remains that justifies another bounded repair milestone.

## Final 14j Package

```text
UUID: rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001
root: /lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001
launch source: 0e4015547da45802cc7b6ff3a9b92adce73077fc
config SHA256: 470f7183adb8804540c46e146e2db9e359f09f67b43eb0d3e0fab153bedf41a9
contract SHA256: 2ff7810700abe99c53c298978e0c6f14d56f6cd0465d2c8065d3569e59abdafb
artifact-index SHA256: 8c0af8a6c1dbd4e1a95dfeba60a334924dcf4b453db6a633afbd46fadd98f46b
authorization: NOT_AUTHORIZED
```

The root was absent before preparation and now contains `preflight/` only.
There is no runtime authorization, `stages/`, formal attempt ledger, or
scientific output. The proposed hard cap remains 120 hours and requires a new
explicit run-bound authorization.

Runtime estimates are unchanged because the scientific workload is unchanged:
3D-fail branch 26.75 h expected / 56.5 h conservative / 21.85 H100 h;
3D-PASS then complete 1D 47.75 h expected / 92.5 h conservative / 39.05
H100 h; storage 46/90 GiB expected/conservative.

## Scientific Freeze

Verified unchanged: global seed 25101; parent split algorithm and seed 18018;
29/8 parent split; 310,433 legal selector cells; CV seed 25071; final selector
seeds 25071/25072/25073; panel 256/499/40; D06 expectations 366/98 and
129/300/35 labels; Qwen/tokenizer/AppWorld; writer/reader, losses, two epochs,
checkpoint selection, D06B, D08B, D22, evaluator, and conditional 1D.

Scientific configuration changes: `0`.

H100 scientific active time: `0`.

Long scientific run launched: `false`.
