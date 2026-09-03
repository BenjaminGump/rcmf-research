# EXP-037A-R1 D08 Producer/Consumer Contract Repair

## Outcome

`repair_decision = INCONCLUSIVE`  
`execution_action = STOP`  
`scientific_result = NOT_EVALUATED`

The known D08 metadata failure is repaired exactly: all 499 transition rows now
satisfy the unchanged historical EXP-031A `_section_contract`, and the new
`S05B_joint_source_contract_preflight` passes on the real transition cache.
The isolated real D08 then exposed a second frozen contract: the historical
configuration requires 366 model-training scoreable states, while the immutable
fresh D06 paired outcomes contain 342. Changing that count or filling the 24
missing rows would change D09 training coverage, so this milestone stops.

No D09 optimizer unit, zero cache, `_002` preflight, or long scientific run was
launched.

## Git And Scope

- Branch: `research/v6-rcmf-reproducible-pipeline-d08-repair`
- Starting failure record: `bba2b99fe360c531ccfe7281c07ffaea639c678e`
- Repair implementation: `0866d41622b4c6d08edbd3e31f6658c56335f7e6`
- Archive branch: `archive/exp037a-001-d08-contract-failure-bba2b99f`
- Annotated tag: `exp037a-001-d08-contract-failure-verified-bba2b99f`
- Historical consumer semantic changes: none
- Global seed: `25101`

## Original Failure And Repair

The source at `02ef947...` emitted `teacher_section_tokens`,
`teacher_section_sha256`, and `tokenizer_name_or_path`. The consumer also
requires `source_task_goal_tokens`, `canonical_pre_action_state_tokens`,
`complete_action_tokens`, and `complete_post_action_observation_tokens`.

The repaired producer in
`rcmf/benchmarks/appworld/transition_metadata_14c.py` tokenizes the complete
teacher section once, without special tokens or truncation, then obtains the
four counts from canonical offset-aligned spans. Schema:
`rcmf_transition_token_metadata_14c_v1`. The consumer was not weakened or
changed.

## Exact 499-Row Audit

- Rows: `499/499`
- Missing required fields: `0`
- Token-count mismatches: `0`
- Text/hash mismatches: `0`
- Truncation: `0`
- Token subsampling: `0`
- Ordered transition ID SHA: `6cbaf15f...f81659`
- Maximum full teacher section: `35,636` tokens
- Maximum goal/state/action/outcome sections: `132 / 35,511 / 177 / 9,787`

The real S05B gate called the unchanged historical consumer for all 499 rows;
its consumer-row SHA is `3a11b4d...b58d2`, with zero mismatches.

## Invariance

- Ordered IDs, raw transition-content hashes, and all four section-hash lists
  are identical before/after repair.
- Transition cache container SHA remains `f3bf7798...740b8`.
- Final-layer tensor SHA remains `2ed58934...95929`.
- Mean-final-four tensor SHA remains `97ccc747...e1ec2`.
- State cache, selector outputs, paired outcomes, teacher cache, and task split
  are the same hash-validated `_001` fixtures.
- Formal runtime-config diff passes with no prohibited difference. Scientific
  differences remain only the preregistered `full_demo` versus
  `full_demo_first_only`; path/run identities differ by arm as expected.

The transition manifest file hash changes from `f1484d41...29b3` to
`7e9e4198...03c7b` solely because derived metadata was added. The stage graph
changes from 57 to 58 stages solely by adding the engineering validity gate.

## Real D08 Diagnostics

Attempt `_001` of the repair diagnostic failed before D08 because its wrapper
passed unresolved arm include pointers. Commit `0866d41622b4c6d08edbd3e31f6658c56335f7e6` fixes that
plumbing and adds a regression test; it produced no scientific output.

Attempt `_002` passed the 499-row schema audit and S05B, then entered the real
historical prepare path. It created only the source cache, memory provenance,
and key-payload shuffle manifest before failing the prepared-count check:

```text
Configured: train scoreable 366, heldout scoreable 98
Observed:   train scoreable 342, heldout scoreable 98
```

The frozen paired panel records 464 initial states and 440 completed rows.
There are 23 over-context missing rows and 5 replay-semantic missing rows with
an overlap of 4, hence 24 distinct missing rows. No value was imputed.

The full-bank data manifest, source audit, exact selector-decomposition audit,
runtime counts, D08 smoke, zero cache, and D09 probe are absent. No checkpoint,
model, prompt, evaluator, or scientific config was modified; H100 work and
writer/reader optimizer updates are both zero.

## Tests

- Focused local: `38 passed` in `6.48s`.
- Focused Lambda: `38 passed` in `2.77s`.
- Full local: `827 passed, 2 skipped` in `17.22s`.
- Full Lambda: `829 passed` in `7.71s`.

The first full-suite invocation on each host omitted the required process-start
`PYTHONHASHSEED=25101` and was rejected during collection. Both suites were
rerun under the required contract and passed. The local skips require a CUDA
device split and Lambda-only Bash launcher respectively.

## Failed `_001` And Proposed `_002`

All 45 published `_001` artifacts rehash correctly after diagnostics. All
1,221 copied fixtures (2,359,800,062 bytes) match both source and diagnostic
copies. D00-D07 remain validated read-only diagnostic fixtures and are not
authorized as future scientific input.

The proposed `_002` root does not exist. Its preflight is not prepared, it is
not authorized, and the old `_001` 200-hour authorization is not inherited.

## Verified / Inference / Unverified

**VERIFIED:** the original metadata KeyError is fixed; 499-row/S05B checks pass;
scientific transition tensors are unchanged; the real D08 stops at 342 versus
366; zero-cache/D09 were not run; `_001` is immutable; full tests pass.

**INFERENCE:** resolving the 342/366 mismatch by changing the expected count or
filling missing rows would alter the scientific training population. The
repair charter therefore requires review rather than an automatic patch.

**UNVERIFIED:** downstream D08 contracts after the count gate, one-unit D09
loss/gradients/resume, and `_002` runtime/storage estimates.

## Decision

`INCONCLUSIVE`: the metadata repair is valid, but real D08 cannot complete
without a decision that affects scientific data coverage. A future reviewed
milestone must explicitly decide whether the fresh pipeline's missing-row
contract changes the expected training population or whether authoritative
inputs must be regenerated. This task does neither.

**NO NEW LONG SCIENTIFIC RUN WAS LAUNCHED.**
