# EXP-037A-R3 Structured Handoff

Timestamp: 2026-09-03T14:18:31Z

## Status

- Branch: research/v6-rcmf-exp037a-reproduction-contract-repair
- Starting commit: 1c990d5e5a08ee5d4db7c9ce941525ed763bba2d
- Repair implementation commits:
  - d885ca6c41b96bde66175821751477efa22a1354
  - 0d8cc37a4eac02a6d3f19f04b48f83eed4751259
  - f6325e84a20856a3e704fc70cfbc72326feb2872
  - 7dcc2640d10094473427343d8c511e0f7a24d2c5
- Decision: REPAIR_VALIDATED_READY_FOR_3D_PREFLIGHT
- Scientific performance result: NOT_EVALUATED
- Global seed: 25101

## Contract Repair

The repaired selector split is generated from authoritative transition-parent
identities by the historical deterministic_parent_split implementation. It
uses SHA256 ordering, seed 18018, 29 train parents, and 8 heldout parents.
Only after the fresh file was sealed was the historical manifest inspected.

- Parent-split SHA256:
  0c6707f61f3fa62847c1abea366b44e4fd50c206f773fa6e79a5e8ffe433c615
- Legal label cells: 310,433
- Exact keyed cell agreement: 310,433/310,433
- Moved/missing/fresh-only cells: 0/0/0
- Fold memberships: exact
- Candidate definitions: exact
- CV seed: 25071
- Final member seeds: 25071, 25072, 25073

The panel now uses 256 initial states, 499 maximum states, and minimum 40 per
label. Historical 366/98 completion counts are post-D06 audit gates with
construction_input=false.

The resolved 3D and 1D configs have identical scientific settings except for
full_demo versus full_demo_first_only. No prompt, data, model, evaluator,
representation, selector architecture, writer/reader, field algebra, context
limit, or generation setting changed.

## Fresh Diagnostic

Fresh run:
rcmf_exp037a_r3_selector_reproduction_14e_fix1_20260903_001

Fresh root:
/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r3_selector_reproduction_14e_fix1_20260903_001

Read-only audit:
rcmf_exp037a_r3_sealed_d05_audit_14e_20260903_001

Audit root:
/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r3_sealed_d05_audit_14e_20260903_001

All seven allowed stages completed. Unique stage attempts are:

- S05_transition_representations-r3-1788435371969248462
- D00_state_representations-r3-1788435858256608652
- D01_selector_candidate_cv-r3-1788436457705323929
- D02_selector_candidate_selection-r3-1788440632807887017
- D03_final_selector_ensemble-r3-1788440632830669700
- D04_selector_factorization-r3-1788444106207616367
- D05_selected_memory_manifest-r3-1788444107496009828

All seven completed, with zero failed and zero open stage attempts.

Candidate NDCG@4:

- balanced_lr3e4_e60_t1: 0.7203697809257877
- balanced_lr1e4_e120_t075: 0.7259694961904805
- hard_lr3e4_e120_t075: 0.7286580684969522

Fresh and historical winner:
hard_lr3e4_e120_t075

Fresh selected-memory agreement: 499/499.
Fresh selected-class agreement: 499/499.
Fresh context-decision agreement: 499/499.

Fresh member checkpoint hashes:

- seed 25071:
  1c73282f4e372c17a5378db66fccd9efd2d7a0013c8b87e4e18fb43e82432687
- seed 25072:
  1ec8cc2dd323a3cba5bd5264615bfcbf7c358fb8d32dda401a55f99a3998920f
- seed 25073:
  8b788bd35aa21cb46426ab4441e7bcf4fa349922ed7286293ba07f5b9f907f85

For appworld:trace:afc0fce_1:step:13:line:69 the corrected fresh
selection is 2a8422ce-c2d8-51d4-b19b-d7741a67cae2. Token accounting is
23,205 base + 11,045 memory = 34,250, below the 40,960 limit, so the decision
is PASS. It matches historical evidence exactly.

## Runtime

- Bounded stage wall time: 8,774.226 seconds / 2.437 hours
- Approximate H100 active time: 2.426 hours
- Optimizer/backward count in the read-only audit: 0
- D06 full paired generation: not run
- D07/D08/D09: not run
- 1D arm: not run

Future clean 3D estimate:

- expected wall time: 26.5 hours
- conservative wall time: 56 hours
- estimated H100 active time: 21.8 hours
- storage: 46 GiB expected, 90 GiB conservative
- proposed anomaly cap: 80 hours
- authorization: explicit user approval required

The old 200-hour authorization does not carry over. No future run was launched.

## Validation

Exact final local commands:

    $env:PYTHONHASHSEED='25101'; C:\\Users\\Admin\\miniconda3\\Scripts\\conda.exe run -n appworld_env python -m pytest -q -p no:cacheprovider --basetemp .codex_tmp/pytest-r3-final-focused-20260903T1425Z tests/test_exp037a_r3_reproduction_contract.py

    $env:PYTHONHASHSEED='25101'; C:\\Users\\Admin\\miniconda3\\Scripts\\conda.exe run -n appworld_env python -m pytest -q -p no:cacheprovider --basetemp .codex_tmp/pytest-r3-final-full-20260903T1425Z

Exact final Lambda commands:

    PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python -m pytest -q tests/test_exp037a_r3_reproduction_contract.py

    PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python -m pytest -q

Final results:

- Local focused: 12 passed.
- Local full: 849 passed, 2 skipped.
- Lambda focused: 12 passed.
- Lambda full: 851 passed.

Earlier implementation checkpoints passed 58 focused / 847 full plus 2
skipped locally, then 11 focused / 848 full plus 2 skipped locally and 11
focused / 850 full on Lambda. Required full suites used process-start
PYTHONHASHSEED=25101.

## Deviations

The initial audit root stopped before GPU stages because positional JSONL
comparison falsely treated order as identity. The main fresh root then sealed
all D05 outputs but its post-hoc reporter rejected null token counts for
over-context rows. Both were audit-only implementation issues. The latter was
resolved by a standalone read-only audit of the already sealed root. No
scientific configuration or output was changed.

A final local focused invocation without repository-local basetemp also hit
five fixture-setup ACL errors in the Windows default pytest temp directory
after seven tests passed. The unchanged command with the ignored repository
basetemp passed 12/12.

## Artifacts

- Report:
  research/results/EXP_037A_R3_REPRODUCTION_CONTRACT_REPAIR.md
- Artifact index:
  research/results/exp037a_r3_reproduction_contract_repair/artifact_index.json
- Artifact-index SHA256:
  2a86cafffd14ee03c87ad7e46650415bbc96d8284cd6f5813ea830b9bfc742db
- Future runtime proposal:
  research/results/exp037a_r3_reproduction_contract_repair/future_3d_runtime_proposal.json
- Sealed audit summary:
  research/results/exp037a_r3_reproduction_contract_repair/sealed_d05_audit_summary.json

## Safety And Provenance

VERIFIED:

- Historical selector checkpoint files were never loaded, deserialized,
  instantiated, or executed.
- Historical selected memories and outcomes were inspected only after fresh
  outputs were sealed.
- Historical outcomes did not construct or select the fresh panel.
- The fresh scientific root was not mutated by the standalone audit.
- No full D06, D07, D08, D09, 1D arm, or long run was launched.

INFERENCE:

- The 26.5/56-hour future wall estimate and 21.8-hour H100 estimate are
  planning values inherited from prior complete-pipeline measurements, not
  completion measurements.

UNVERIFIED:

- Future full-D06 366/98 completion reproduction remains untested by design.
  It is the next post-generation gate if a future run is explicitly approved.
