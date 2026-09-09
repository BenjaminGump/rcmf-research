# Harness V1 RC3 Final Independent Review

Status: `READY_TO_FREEZE_NEUTRAL_HARNESS_V1`

Verified UTC: `2026-09-09T08:03:21Z`

## Authority

- RCMF review base: `f18472c5c1890511d9c73946e83514dc749b48dc`.
- Harness RC3 branch: `rc/harness-v1-rc3`.
- Harness RC3 source: `827ed6f394804834e93444c9bb02c435e9e238a3`.
- Harness RC3 archive: `archive/harness-v1-rc3-source-827ed6f`.
- Harness RC3 records: `77a98f3e84a1d93cccc96ef45c9b90af5af625fe`.

The archive resolves exactly to source. Records are the direct records-only
child of source and change only `docs/` and `research/`. Fresh source and
records checkouts were clean, and `git fsck --full` passed. RC1/RC2 branch and
archive refs remain at their previously reviewed identities.

## Evidence Trust Boundary

RC3 no longer treats a Python evidence class as proof. Aggregation accepts a
`ResultEvidenceBundle` carrying the complete authoritative evidence set:

1. run manifest;
2. benchmark lock;
3. method lock;
4. ordered task manifest;
5. actual task-result rows;
6. result manifest.

For every bundle, aggregation first creates a canonical-JSON snapshot and then
calls `validate_finalized_result()` itself. This re-establishes run/lock
identity, exact task population and ordering, task-row hashes/statuses, and
typed execution status before comparison. No process-local sentinel, registry,
object identity, hidden token, or constructor privacy participates.

The same two valid bundles were serialized to JSON, loaded with
`ResultEvidenceBundle.from_mapping()` in a fresh Python process, and returned
`eligible=true` with no reasons. The proof is therefore reproducible from
serialized content-addressed evidence.

## RC2 Bypass Reproduction

The exact RC2 duplicate-task bypass was retried against RC3. All paths were
comparison-ineligible:

- raw mapping: `UNVALIDATED_RESULT_EVIDENCE`;
- directly instantiated old public evidence type:
  `UNVERIFIED_FINALIZATION_EVIDENCE`;
- copied forged evidence: `UNVERIFIED_FINALIZATION_EVIDENCE`;
- subclassed forged evidence: `UNVERIFIED_FINALIZATION_EVIDENCE`;
- directly constructed malformed bundle:
  `UNVERIFIED_FINALIZATION_EVIDENCE` after semantic revalidation.

The old diagnostic type remains importable for compatibility but is never
sufficient proof for aggregation.

## Positive And Negative Comparisons

Two complete valid bundles with distinct method identities and matching shared
identities were comparison-eligible. Independent mutations produced the
required typed ineligibility:

- harness source: `INCOMPARABLE_HARNESS_IDENTITY`;
- task manifest: `INCOMPARABLE_TASK_MANIFEST`;
- base prompt: `INCOMPARABLE_BASE_PROMPT`;
- model: `INCOMPARABLE_MODEL_IDENTITY`;
- generation config: `INCOMPARABLE_GENERATION_CONFIG`;
- execution status: the corresponding `EXECUTION_*` reason plus failed
  finalization evidence.

Changes that alter a benchmark lock also correctly report the broader
`INCOMPARABLE_BENCHMARK_DATA_IDENTITY` reason.

## Fairness Regression

Independent F01 fixtures confirmed rejection of duplicate/omitted/extra tasks,
exact-order violations, duplicate authorized IDs, missing actual rows, and
task-row hash/status mismatches. Independent F02 fixtures confirmed rejection
of every run, track, harness, lock, task-manifest, and task-row identity
mismatch.

All four F03 statuses remain excluded with their exact `EXECUTION_*` reasons.
Completed reward-zero and `binary_success=false` rows remain legitimate
benchmark outcomes.

## Method Neutrality And RCMF

RC2-to-RC3 protocol code, lifecycle tests, and all four reviewed schemas are
unchanged. Validation and aggregation contain no RCMF import or method-name
conditional. All six lifecycle mocks pass.

The RCMF focused compatibility suite passed `38/38`. A separate probe through
the actual RC3 `HarnessLifecycleRunner` accepted the RCMF plugin, retained
method-owned checkpoint/field state, preserved the harness-owned base prompt,
performed two episode resets, and reported no retrieval or raw-memory prompt.
No RCMF executable change was required.

## Tests And Decision

- Fresh Harness RC3 full suite: `69/69 PASS`.
- Independent trust/fairness/comparison reproducer: `38/38 PASS`.
- RCMF focused compatibility: `38/38 PASS`.
- Actual RC3 runner/RCMF plugin probe: `9/9 PASS`.

Thread B's RC3 handoff was accurate; no divergence was found. The final Harness
V1 freeze remains a Thread B action and was not performed here.

`READY_TO_FREEZE_NEUTRAL_HARNESS_V1`

NO RCMF OR BASELINE TRAINING WAS RUN

NO STANDARDIZED BENCHMARK RESULT WAS PRODUCED

NO FINAL HARNESS V1 WAS FROZEN

FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED
