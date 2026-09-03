# EXP-037A-R2 First-Divergence Provenance Audit

Date: 2026-09-03

Run ID: `rcmf_exp037a_r2_first_divergence_audit_14d_20260903_001`

Starting commit: `255eec178e6e5a1a0e3d0d1f4d246c240a4f6ecb`

Branch: `research/v6-rcmf-exp037a-first-divergence-audit`

Decision: `CAUSE_IDENTIFIED`

Scientific result: `NOT_EVALUATED`

Optimizer updates: `0`

Backward passes: `0`

## Scope

This was a read-only provenance and data-flow diagnosis of the three-demo
`366 -> 342` reconstruction discrepancy. It did not repair the pipeline,
change the expected count, fill missing rows, train a selector, build D08
training units, or launch a new scientific run.

The historical selector checkpoint was never loaded, deserialized, or
executed. Historical selector evidence was limited to JSON/text manifests,
recorded selections, configuration, metric reports, and checkpoint SHA256
metadata.

## Executive Finding

The historical `366` is real. The historical paired-outcome artifact contains
366 completed model-training rows and 98 heldout rows, and the EXP-031A
training-unit manifest consumes all 366 as correct units.

Two verified upstream contract divergences caused the fresh reconstruction to
produce 342 model-training rows:

1. At `L4_selector_training_provenance`, the fresh pipeline uses the
   downstream query-task split as the selector parent split. The historical
   selector uses the locked clean parent split. This changes 97,734 of 310,433
   legal pair label cells, changes CV folds, and changes the selected CV
   winner.
2. At paired-panel admission, the fresh pipeline treats the historical
   post-outcome count `366` as an a-priori training-panel quota, creates an
   initial panel of `366 + 98 = 464`, and sets `minimum_per_label = 0`. The
   historical run starts with 256 states, has `minimum_per_label = 40`, and
   expands through the remaining 243 states. Because the historical harmful
   quota never reached 40, it attempts all 499 states.

The exact completed-train reconciliation is:

```text
historical completed train                         366
- historical completions omitted by fresh panel   25
- historical completion lost inside fresh panel    1
+ fresh-only completions                            2
= fresh completed train                           342
```

The one historical completion lost inside the fresh panel is
`appworld:trace:afc0fce_1:step:13:line:69`. Its base prompt is identical, but
the changed selector provenance leads to a different selected transition. The
historical raw prompt is 34,250 tokens and passes the 40,960-token limit; the
fresh raw prompt is 43,186 tokens and fails it.

## A. Is Historical 366 Real?

**VERIFIED: yes.**

- Historical paired outcomes:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_structured_gate_compiler_7hr_20260823_001/paired_causal/paired_outcomes.json`
- SHA256:
  `1b02ab0ecf3da87b2485acce6de8a7188db610e1f56f9bb66c55ad45f94e0bf9`
- Completed rows: 464 total, 366 model-training, 98 heldout validation.
- Historical EXP-031A data manifest SHA256:
  `196f9ec57674de202324bd34f0168cf4c0f6c5399fc9828939a8dcc730b214b2`
- Historical EXP-031A training-unit manifest SHA256:
  `7bb0532e54ee8039b349fdba4ab29d595ced60c79f8ad7f8a74a2eb14f76b8c7`
- The training-unit manifest contains 366 correct-state units and 1,152
  optimizer steps over two epochs in the historical experiment.

The ordered 366/98 historical state manifest is committed as
`research/analysis/exp037a_r2_historical_state_manifest.json`; the published
Lambda copy has SHA256
`2104500ec2ed785f1b7ee82b8484b9c11aa1a7ac699e0334213998ff586e592c`.

## B. State Universes And Set Accounting

**VERIFIED:** the historical and fresh logical state universes contain the
same 499 state IDs. Their set SHA256 is
`67f8087199024d0bf7bbf013bbebb3aafac51f2a7b6df321ba8a43a48b5e9766`.
Their order differs because the panel-construction contracts differ.

Completed train sets:

| Set | Count |
| --- | ---: |
| Historical completed train | 366 |
| Fresh completed train | 342 |
| Intersection | 340 |
| Historical-only | 26 |
| Fresh-only | 2 |

Of the 26 historical-only completions, 25 were outside the fresh initial panel
and therefore never attempted. One was inside the panel and changed from
historical scoreable to fresh over-context. The two fresh-only completions
were historical failures:

- `appworld:trace:3c13f5a_2:step:18:line:498`
- `appworld:trace:afc0fce_1:step:18:line:74`

The full ordered/set comparison is in
`research/analysis/exp037a_r2_state_set_comparison.json`.

## C. Prompt And Representation Identity

**VERIFIED:** all 499 states have matching historical/fresh base rendered
prompt SHA256 values and matching base prompt token counts. The historical
paired artifacts do not preserve a separate structured-message-array SHA or
input-token-ID SHA, so those narrower L1/L2 identities remain unavailable.
That evidence gap is not material to the proven count cause.

The scientific state/transition tensor-state hashes match:

| Representation | SHA256 |
| --- | --- |
| State final | `fd4c32c8366604e34cc40a060aba4fa3269b9ef5cd135a4af96b77c70865c084` |
| State mean-final-four | `eca49197c51266a4328af0d884216d10fac0ea391941cfce7d36a8bdacb2d30d` |
| Transition final | `2ed58934ee486431b9d6cda7c581a2db1fb3db6e707a3817a8636b75a5495929` |
| Transition mean-final-four | `97ccc747e535910d8e3a462f52d718b0febd01f73c04d94de1d385f0854e1ec2` |

All 499 transition scientific identities match. Query-signature and illegal-
pair hashes also match. Container-level hashes differ only because the fresh
artifacts carry enriched metadata.

## D. Selector Training Provenance

**VERIFIED: the fresh selector recipe did not reproduce the historical split
contract.**

Historical parent-split SHA256:
`0c6707f61f3fa62847c1abea366b44e4fd50c206f773fa6e79a5e8ffe433c615`

Historical heldout parent tasks:

```text
07b42fd_1  07b42fd_2  287e338_1  76f2c72_1
76f2c72_3  771d8fc_1  b7a9ee9_1  cf6abd2_3
```

Fresh parent-split SHA256:
`0b6ca2f85c939c3753617bf43ca178be801b0d88a0fdec4cb1f06afe970438ae`

Fresh heldout parent tasks:

```text
76f2c72_1  76f2c72_2  7d7fbf6_1  b7a9ee9_2
c901732_1  c901732_3  e7a10f8_1  e7a10f8_3
```

Both label manifests contain 310,433 legal rows, but only 212,699 retain the
same cell assignment. Exactly 97,734 label cells move. The nominal candidate
definitions and seeds `25071, 25072, 25073` match, but fold membership changes.

- Historical winner: `hard_lr3e4_e120_t075`
- Fresh winner: `balanced_lr3e4_e60_t1`
- Historical selector SHA256 metadata:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Fresh selector file SHA256:
  `07eee76ace3c2e48485b4290528d65232a9fc3b8041b2659fed54e9f2b291544`

The committed source-level cause is
`scripts/prepare_rcmf_reproducible_pipeline_14b.py::_parent_split`, which
derives the selector parent split from the downstream query-task split named
by `configs/pipeline/rcmf_appworld_repro_14b.yaml`. The historical selector
preparation instead consumes the clean parent split through
`scripts/prepare_signature_balanced_field_7c.py`.

## E. Selected-Memory Agreement

Historical selected-memory IDs were read only as archaeology evidence. They
were never used to make fresh decisions.

| Population | Agreement |
| --- | ---: |
| All 499 | 367/499 = 73.547% |
| Historical completed train | 271/366 = 74.044% |
| Common completed train | 254/340 = 74.706% |
| Historical-only completed | 17/26 = 65.385% |
| Fresh missing 24 | 20/24 = 83.333% |
| Matched successful controls | 18/24 = 75.000% |
| Heldout 98 | 68/98 = 69.388% |

**VERIFIED:** the fresh missing states do not disproportionately select a
different memory. Their agreement is higher than the matched controls. The
blanket claim that the fresh selector simply chose longer memories for all 24
missing rows is false. A changed selection directly explains one net lost
historical completion, not the dominant 25-row panel omission.

## F. Exact Over-Context Accounting

The fresh attempted panel has 19 over-context rows and five replay-semantic
rows, with zero overlap. Across all 499 logical slots there are 23 over-context
rows; the additional four are unattempted expansion rows. Therefore the prior
`23 over-context / 5 replay / 4 overlap` description conflated attempted and
unattempted rows.

All 23 over-context rows partition as:

| Cause | Count |
| --- | ---: |
| Same historical over-context decision | 19 |
| Selector changed, but both historical and fresh are over-context | 3 |
| Selector changed, historical PASS became fresh over-context | 1 |

For `appworld:trace:afc0fce_1:step:13:line:69`:

| Quantity | Historical | Fresh |
| --- | ---: | ---: |
| Base prompt tokens | 23,205 | 23,205 |
| Selected-memory increment | 11,045 | 19,981 |
| Total input tokens | 34,250 | 43,186 |
| Effective limit | 40,960 | 40,960 |
| Headroom | +6,710 | -2,226 |
| Decision | PASS | OVER_CONTEXT |

Generation reservation is zero in this admission calculation; the rendered
input contains all counted framework/special-token overhead. Exact arithmetic
for every over-context and replay row is in
`research/analysis/exp037a_r2_context_budget_comparison.jsonl`.

## G. Replay-Semantic Failures

The five fresh replay-semantic failures are all states after replay step 15 in
task `afc0fce_3`. They share:

- reason: `python_traceback_format_differs_under_locked_semantic_v3`
- exception: `KeyError`
- action SHA256:
  `43dfa0c64c517beb3d6b92de7ce3a326c730aae9071f32a54869aaf05e7992e3`
- actual observation SHA256:
  `6eb7e58a8f880ef35eed2206f468858d156b28a28ec56fcd0a747d3e76cc712b`
- expected observation SHA256:
  `8ab7ca320cc9458f3b33d42b84fdd393555b240a6d725ae95c62e1287401c162`

Four are the same historical replay-semantic failure at the failed-step
evidence level. The fifth was historically over-context, so the replay failure
was previously masked. None was historically scoreable, so these failures do
not contribute to the `366 -> 342` count loss.

## H. Why Heldout Remains 98/98

**VERIFIED:** all 98 heldout states were members of the historical 256-state
initial panel, which is a subset of the fresh 464-state initial panel. The
fresh panel cap removes expansion train rows, not heldout rows. The completed
heldout sets are exactly equal.

Only 68/98 heldout selections agree, which is useful negative-control evidence:
a selector change alone does not imply paired-row loss. Missingness requires
the changed decision to interact with context or replay admission.

## Fresh Missing 24

The canonical fresh attempted-but-not-completed list is:

```text
appworld:trace:229360a_2:step:16:line:354
appworld:trace:229360a_3:step:27:line:382
appworld:trace:3c13f5a_2:step:19:line:499
appworld:trace:afc0fce_1:step:13:line:69
appworld:trace:afc0fce_1:step:17:line:73
appworld:trace:afc0fce_1:step:19:line:75
appworld:trace:afc0fce_1:step:20:line:76
appworld:trace:afc0fce_1:step:22:line:78
appworld:trace:afc0fce_1:step:23:line:79
appworld:trace:afc0fce_1:step:24:line:80
appworld:trace:afc0fce_1:step:25:line:81
appworld:trace:afc0fce_1:step:28:line:84
appworld:trace:afc0fce_1:step:33:line:89
appworld:trace:afc0fce_1:step:34:line:90
appworld:trace:afc0fce_1:step:35:line:91
appworld:trace:afc0fce_1:step:36:line:92
appworld:trace:afc0fce_3:step:12:line:104
appworld:trace:afc0fce_3:step:15:line:107
appworld:trace:afc0fce_3:step:16:line:108
appworld:trace:afc0fce_3:step:17:line:109
appworld:trace:afc0fce_3:step:21:line:113
appworld:trace:afc0fce_3:step:22:line:114
appworld:trace:afc0fce_3:step:23:line:115
appworld:trace:afc0fce_3:step:24:line:116
```

Only `afc0fce_1:step:13` was historically scoreable. The other 23 were
already historical non-scoreable states. The complete L0-L8 status ladder and
evidence for every row is in
`research/analysis/exp037a_r2_first_divergence_rows.jsonl`.

## First-Divergence Decision

Primary metric:

```text
first_divergence_resolved_count / 24 = 24 / 24
```

Decision: `CAUSE_IDENTIFIED`.

**VERIFIED causal partition:**

- 25 historical scoreable train rows were omitted before fresh generation by
  the changed panel-admission contract.
- One historical scoreable row in the fresh panel became over-context after
  the L4 selector-provenance divergence changed its selected memory.
- Two historical failures became fresh completions and partially offset those
  26 losses, yielding the observed net loss of 24.
- The five fresh replay-semantic failures are preexisting or previously masked
  and do not remove any historical scoreable row.

## Evidence Gaps

**UNVERIFIED, non-material to the count cause:** the historical paired rows do
not retain separate structured-message-array or input-token-ID SHA256 fields.
They do retain rendered base-prompt SHA256 and token count, both of which match
fresh evidence for all 499 states.

No material missing state remains unexplained.

## Proposed Minimal Repair - Not Implemented

The evidence supports the following review proposal only:

1. Restore the historical selector parent split exactly.
2. Restore the historical paired-panel contract: 256 initial states, maximum
   499, `minimum_per_label = 40`, and deterministic expansion.
3. Retrain a fresh selector from authoritative inputs under that exact recipe;
   do not load or reuse the historical selector.
4. Before any new scientific run, require exact pre-generation invariance for
   the 499-state universe/order, parent split, label-cell assignments, folds,
   selected CV winner, selected-memory manifest, context decisions, and the
   historical 366/98 completion sets.

No part of this proposal was implemented in EXP-037A-R2.

## Verification

- Focused audit suite: `10 passed in 0.17s`.
- Complete local repository suite under process-start
  `PYTHONHASHSEED=25101`: `837 passed, 2 skipped in 19.04s`.
- Generated JSON validation: five JSON documents and all experiment-ledger
  rows parsed successfully.
- Generated JSONL validation: 24 ladder rows and 28 context/replay rows.
- Established JWT/bearer/credential patterns: zero matches in 11 new files.

## Artifact Index

Published Lambda diagnostics root:

`/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r2_first_divergence_audit_14d_20260903_001_published`

The complete input/output path, size, and SHA256 index is committed as
`research/analysis/exp037a_r2_artifact_index.json`. The published index SHA256
is `4f0920551b67e8c856ddfdf5fbc03811a0c72cd32d3f983eb50807c360bfed2d`.

## Deviations

- The first local focused pytest invocation used the Windows default temporary
  directory and hit five fixture setup permission errors; five pure tests
  passed. The unchanged suite was rerun with a repository-local ignored
  `--basetemp` and passed 10/10. This was test infrastructure only.
- The first complete-suite invocation omitted the repository-required
  process-start `PYTHONHASHSEED=25101` and failed collection at the intentional
  EXP-036C guard. The required-contract rerun passed 837 with two skipped.
- Audit instrumentation was iterated in append-only diagnostics roots before
  publishing the final content-addressed root. No immutable historical or
  fresh run root was written.
- No scientific semantics, model, selector, data, checkpoint, or admission
  rule was changed.
