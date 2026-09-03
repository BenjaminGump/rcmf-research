# EXP-037A-R3 Historical 3D Reproduction-Contract Repair

## Outcome

Decision: REPAIR_VALIDATED_READY_FOR_3D_PREFLIGHT

EXP-037A-R3 repaired and boundedly validated the two reproduction-contract
errors identified by EXP-037A-R2. The fresh selector reconstruction reproduces
the historical candidate winner, all 499 selected-memory decisions, all 499
selected classes, and all 499 available pre-generation context decisions.

This milestone did not run full D06 paired generation, D07, D08, D09, the 1D
arm, or a new long scientific run.

## Repair

VERIFIED:

- The selector parent split is freshly reconstructed from authoritative
  transition-parent identities using the historical deterministic algorithm:
  SHA256 ordering with seed 18018, 29 train parents, and 8 heldout parents.
- The fresh split was sealed before the historical reference was read. Its
  SHA256 is
  0c6707f61f3fa62847c1abea366b44e4fd50c206f773fa6e79a5e8ffe433c615.
- All 310,433 legal label pairs agree with the historical audit when compared
  by state/transition identity. Moved cells, missing rows, and fresh-only rows
  are all zero.
- Grouped CV folds, all candidate definitions, CV seed 25071, and final-member
  seeds 25071, 25072, and 25073 agree exactly.
- The paired-panel contract is now 256 initial states, 499 maximum states, and
  40 minimum completed rows per label. The downstream counts 366/98 are
  post-D06 reproduction expectations only and are not construction inputs.
- Both arm configs retain the same 256/499/40 contract. The sole intended arm
  difference remains full_demo versus full_demo_first_only.
- The prior 14c token-metadata repair remains unchanged.

The JSONL row order differs in 309,942 positions, but the content-addressed
pair map agrees 310,433/310,433. Row order is not part of the label-cell
scientific identity; folds and selected outputs are separately verified.

## Representation Invariants

Freshly reconstructed representations match all locked scientific targets:

| View | SHA256 |
| --- | --- |
| State final | fd4c32c8366604e34cc40a060aba4fa3269b9ef5cd135a4af96b77c70865c084 |
| State mean-final-four | eca49197c51266a4328af0d884216d10fac0ea391941cfce7d36a8bdacb2d30d |
| Transition final | 2ed58934ee486431b9d6cda7c581a2db1fb3db6e707a3817a8636b75a5495929 |
| Transition mean-final-four | 97ccc747e535910d8e3a462f52d718b0febd01f73c04d94de1d385f0854e1ec2 |

No historical representation tensor was used as a fresh input.

## Fresh Selector Diagnostic

Run:
rcmf_exp037a_r3_selector_reproduction_14e_fix1_20260903_001

Lambda root:
/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r3_selector_reproduction_14e_fix1_20260903_001

Fresh scientific source:
f6325e84a20856a3e704fc70cfbc72326feb2872

| Candidate | Mean NDCG@4 | Fold SD | Pairwise | State-shuffle drop | Transition-shuffle drop |
| --- | ---: | ---: | ---: | ---: | ---: |
| balanced_lr3e4_e60_t1 | 0.7203697809 | 0.0666361199 | 0.8323483123 | 0.5217010819 | 0.6450531527 |
| balanced_lr1e4_e120_t075 | 0.7259694962 | 0.0653525658 | 0.8279273118 | 0.5067633301 | 0.6616870101 |
| hard_lr3e4_e120_t075 | 0.7286580685 | 0.0634549077 | 0.8358056978 | 0.4964261517 | 0.6528245801 |

The fresh and historical winner is hard_lr3e4_e120_t075. All three fresh
member seeds are exact. Checkpoint byte hashes are recorded in the artifact
index. The ensemble factorization has key dimension 960, q shape [638, 960],
k shape [499, 960], and direct-versus-factorized maximum absolute error
2.86102294921875e-06 against tolerance 1e-05.

Post-seal comparison:

- selected-memory agreement: 499/499;
- selected-class agreement: 499/499;
- mismatch count: 0;
- context-decision agreement: 499/499.

Historical checkpoint files were hashable references only. The historical
selector was never loaded, deserialized, instantiated, or executed.

## Context Audit

D05 rendered and tokenized all 499 fresh selected-memory conditions. It
produced 472 scoreable rows and 27 over-context rows, exactly matching the
historical D05 decision manifest.

For appworld:trace:afc0fce_1:step:13:line:69:

- selected memory: 2a8422ce-c2d8-51d4-b19b-d7741a67cae2;
- base prompt tokens: 23,205;
- selected-memory increment: 11,045;
- total prompt tokens: 34,250;
- context limit: 40,960;
- decision: PASS;
- base prompt SHA256:
  1182bd66d535a0b928f6f7f9846d6c867912a360e04887f15e00f19f9ed142e8.

This replaces the erroneous R2 fresh selection that added 19,981 tokens and
reached 43,186. The corrected accounting is an exact historical match.

An audit-only simulation of the historical outcome sequence confirms the
restored 256/499/40 algorithm would attempt all 499 logical states because the
final label counts are POSITIVE 129, NEUTRAL 300, and HARMFUL 35. Those
historical outcomes were never used to construct or select a fresh panel.

## Runtime And Attempts

Seven bounded selector stages completed with seven unique completed attempts
and no open or failed stage attempt:

| Stage | Seconds |
| --- | ---: |
| S05 transition representations | 486.274 |
| D00 state representations | 599.431 |
| D01 selector candidate CV | 4,175.093 |
| D02 candidate selection | 0.014 |
| D03 final selector ensemble | 3,473.357 |
| D04 selector factorization | 1.279 |
| D05 selected-memory/context manifest | 38.778 |

Total bounded stage time was 8,774.226 seconds (2.437 hours). Approximate H100
active time was 2.426 hours.

Two audit-tool incidents are preserved:

1. The first diagnostic root stopped before GPU stages because the initial
   static audit compared JSONL rows positionally and falsely reported moved
   labels. The comparison was corrected to use pair identity.
2. The fix1 root sealed all D05 outputs, then its post-hoc reporting step
   rejected a legitimate null raw_prompt_tokens value on over-context rows.
   A standalone read-only audit at source 7dcc264 verified the sealed outputs
   without mutating the fresh root.

Neither incident changed scientific data, config, model, selector training, or
the sealed fresh outputs.

## Tests

Final local commands:

    $env:PYTHONHASHSEED='25101'; C:\\Users\\Admin\\miniconda3\\Scripts\\conda.exe run -n appworld_env python -m pytest -q -p no:cacheprovider --basetemp .codex_tmp/pytest-r3-final-focused-20260903T1425Z tests/test_exp037a_r3_reproduction_contract.py

    $env:PYTHONHASHSEED='25101'; C:\\Users\\Admin\\miniconda3\\Scripts\\conda.exe run -n appworld_env python -m pytest -q -p no:cacheprovider --basetemp .codex_tmp/pytest-r3-final-full-20260903T1425Z

Final Lambda commands:

    PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python -m pytest -q tests/test_exp037a_r3_reproduction_contract.py

    PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python -m pytest -q

Results:

- Final local focused: 12 passed.
- Final local full: 849 passed, 2 skipped.
- Final Lambda focused: 12 passed.
- Final Lambda full: 851 passed.
- Earlier implementation checkpoints also passed 58 focused / 847 full plus
  2 skipped locally, then 11 focused / 848 full plus 2 skipped locally and
  11 focused / 850 full on Lambda.
- All required full-suite runs used process-start PYTHONHASHSEED=25101.
- A final local invocation without repository-local basetemp produced 7 passes
  and 5 fixture-setup errors because the Windows default pytest temp root was
  access denied. The exact basetemp rerun above passed 12/12 without a source
  change.

## Future 3D Proposal

No future run was launched. The existing full-pipeline preflight implies a
future clean 3D reproduction estimate of 26.5 expected and 56 conservative
wall-clock hours, about 21.8 estimated H100-active hours, and 46/90 GiB
expected/conservative storage. The proposed anomaly cap is 80 hours.

This exceeds the 18-hour automatic gate. The old 200-hour authorization does
not carry over; explicit user approval is required before launch. Resume would
use atomic stage outputs, append-only attempts, content-hash validation, and
the first incomplete stage.

## Decision

REPAIR_VALIDATED_READY_FOR_3D_PREFLIGHT

This decision authorizes preparation of a future run proposal only. It does
not authorize D06, downstream generation/training, or the 1D arm.

VERIFIED facts are stated above. No material inference is needed for the
decision. The future wall/H100/storage values remain preflight estimates and
are not measured completion times.
