# EXP-037A-R3 Reproduction-Contract Repair

Date: 2026-09-03

Starting commit: `1c990d5e5a08ee5d4db7c9ce941525ed763bba2d`

Branch: `research/v6-rcmf-exp037a-reproduction-contract-repair`

## Scope

This milestone repairs only two historical reproduction contracts identified
by EXP-037A-R2:

1. the selector parent split is reconstructed from authoritative transition
   parents with the historical deterministic algorithm (`seed=18018`, 29/8),
   rather than from the downstream writer/reader task split;
2. the paired causal panel is restored to initial 256, maximum 499, and a
   minimum of 40 completed rows per label, with deterministic adaptive
   expansion.

The historical 366 train and 98 heldout counts remain post-D06 reproduction
expectations only. They are not panel-construction inputs.

Historical checkpoints, q/k tensors, selected memories, outcomes, and labels
are comparison-only. Fresh artifacts are sealed before matching historical
JSON evidence is read. No historical selector checkpoint may be loaded,
deserialized, instantiated, or executed.

## Bounded Diagnostic

Run ID:
`rcmf_exp037a_r3_selector_reproduction_14e_20260903_001`

The only executable pipeline stages are:

- `S05_transition_representations`
- `D00_state_representations`
- `D01_selector_candidate_cv`
- `D02_selector_candidate_selection`
- `D03_final_selector_ensemble`
- `D04_selector_factorization`
- `D05_selected_memory_manifest`

The runner rejects D06 and every later three-demo or one-demo stage. D05 is
used to render and tokenize all 499 raw-teacher conditions without model
generation.

Measured immutable EXP-037A `_001` stage anchors total 1.745 hours:

| Stage | Seconds |
| --- | ---: |
| S05 | 487.481 |
| D00 | 622.135 |
| D01 | 3606.785 |
| D02 | 0.020 |
| D03 | 1519.091 |
| D04 | 1.291 |
| D05 | 46.604 |

Expected wall time is 2.25 hours; conservative wall time is 4.0 hours;
expected H100-active time is 1.9 hours. This is below the 18-hour review gate.
The old 200-hour authorization is explicitly not inherited.

Stages are content-addressed and append-only. Resume skips a stage only when
its source commit, completion status, result path, and result SHA256 validate.

## Gates

Before selector training, the fresh split, 310,433 label cells, grouped folds,
candidate definitions, and seeds must match the historical audit contract.
Fresh state/transition representation tensor hashes must match the four
preregistered scientific identities.

After fresh D05 is sealed, the selected candidate must be
`hard_lr3e4_e120_t075`, selected-memory and selected-class agreement must be
499/499, and all 499 available context decisions must match historical audit
evidence. Otherwise downstream work remains blocked.

No full D06, D07, D08, D09, one-demo arm, scientific `_002` root, or long
scientific run is authorized by this plan.
